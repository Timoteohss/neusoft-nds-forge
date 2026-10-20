"""Opt-in read-only SDK route calculation through Neusoft Thrift.

Requires an explicit --execute flag, caller serial, route points and output
path. It opens an ADB forward and calls startRoutePlan only after the SDK's
onRouteOption readiness event. It never starts guidance or mutates files.
Coordinates and raw route geometry are not written to the report.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import os
import re
import socket
import select
import struct
import subprocess
import time


def field(kind, index, payload):
    return struct.pack('>bh',kind,index)+payload


def position(lat, lon, kind):
    geo = field(4,1,struct.pack('>d',lat))+field(4,2,struct.pack('>d',lon))+b'\0'
    unavailable = b''.join(field(2,i,b'\0') for i in (1,2,3))+b'\0'
    available = field(2,1,b'\0')+b'\0'
    attrs = field(12,1,unavailable)+field(12,2,available)+b'\0'
    charging = b''.join(field(t,i,struct.pack('>i' if t==8 else '>d',0))
                        for i,t in [(1,8),(2,8),(3,8),(4,4),(6,8),(7,8),(8,4),(9,4),(10,4)])
    charging += field(11,5,struct.pack('>i',0))+b'\0'
    return (field(12,1,geo)+field(8,2,struct.pack('>i',0))+
            field(11,3,struct.pack('>i',0))+field(15,4,struct.pack('>bi',6,0))+
            field(15,5,struct.pack('>bi',8,0))+field(12,6,attrs)+field(8,7,struct.pack('>i',kind))+
            field(11,8,struct.pack('>i',0))+field(11,9,struct.pack('>i',0))+
            field(15,10,struct.pack('>bi',12,0))+field(11,11,struct.pack('>i',0))+
            field(12,12,charging)+field(15,13,struct.pack('>bi',12,0))+
            b''.join(field(11,i,struct.pack('>i',0)) for i in (14,15,16))+b'\0')


def plan(start, end, option, calculation):
    params = (field(12,1,position(*start,0))+field(12,2,position(*end,6))+
              field(15,3,struct.pack('>bi',12,0))+field(2,4,b'\1')+
              field(8,5,struct.pack('>i',option))+
              (field(8,6,struct.pack('>i',calculation)) if calculation is not None else b'')+
              field(8,8,struct.pack('>i',0))+b'\0')
    name = b'startRoutePlan'
    return struct.pack('>II',0x80010004,len(name))+name+struct.pack('>i',1)+field(12,1,params)+b'\0'


class Reader:
    def __init__(self, sock):
        self.sock = sock
    def take(self, size):
        if not 0 <= size <= 16*1024*1024:
            raise ValueError('Invalid Thrift length')
        data = bytearray()
        while len(data)<size:
            part = self.sock.recv(size-len(data))
            if not part:
                raise EOFError('Thrift connection closed')
            data.extend(part)
        return bytes(data)
    def number(self, fmt):
        return struct.unpack('>'+fmt,self.take(struct.calcsize('>'+fmt)))[0]
    def value(self, kind, depth=0):
        if depth>32:
            raise ValueError('Thrift nesting limit')
        if kind in (2,3,4,6,8,10):
            return self.number({2:'?',3:'b',4:'d',6:'h',8:'i',10:'q'}[kind])
        if kind==11:
            return self.take(self.number('i')).decode('utf-8',errors='replace')
        if kind==12:
            result = {}
            while True:
                subtype = self.number('b')
                if subtype==0:
                    return result
                index = self.number('h')
                result[str(index)] = self.value(subtype,depth+1)
        if kind in (14,15):
            subtype,count = self.number('b'),self.number('i')
            if not 0<=count<=100000:
                raise ValueError('Thrift collection limit')
            return [self.value(subtype,depth+1) for _ in range(count)]
        if kind==13:
            keytype,valuetype,count = self.number('b'),self.number('b'),self.number('i')
            if not 0<=count<=100000:
                raise ValueError('Thrift map limit')
            return [[self.value(keytype,depth+1),self.value(valuetype,depth+1)] for _ in range(count)]
        raise ValueError(f'Unknown Thrift type {kind}')
    def message(self):
        version = self.number('I')
        if version>>16 != 0x8001:
            raise ValueError('Expected versioned unframed TBinaryProtocol')
        name = self.take(self.number('i')).decode()
        sequence = self.number('i')
        return {'method':name,'type':version&255,'sequence':sequence,'fields':self.value(12)}


def probe(device, port, start, end, timeout=25, option=3, calculation=None,
          readiness_event='onRouteOption', library_path=None, expected_library_sha256=None):
    def adb(*args):
        return subprocess.check_output(['adb','-s',device,*args],text=True,timeout=15).strip()
    if not library_path or not expected_library_sha256:
        raise ValueError('An explicit remote library path and expected SHA-256 are required')
    current_library_sha256 = adb('shell','sha256sum',library_path).split()[0]
    if current_library_sha256 != expected_library_sha256:
        raise ValueError('The selected device library does not match the required profile hash')
    local = int(adb('forward','tcp:0',f'tcp:{port}'))
    report = {'host_time':datetime.now(timezone.utc).isoformat(),
              'library_sha256':current_library_sha256,
              'route_option':option,'calculation_type':calculation,
              'use_start_point':True,'guidance_requested':False,
              'readiness_event':readiness_event,'events':[], 'success':False}
    try:
        with socket.create_connection(('127.0.0.1',local),timeout=5) as sock:
            reader = Reader(sock)
            # The server sends state snapshots on connect, including INVALID.
            # Retain them separately so they cannot masquerade as this plan's result.
            initial_messages = []
            initial_deadline = time.monotonic()+8
            while time.monotonic()<initial_deadline:
                if select.select([sock],[],[],.5)[0]:
                    initial_messages.append(reader.message())
                elif initial_messages:
                    break
            ready = any(message['method'] == readiness_event for message in initial_messages)
            report['readiness_observed'] = ready
            report['initial_event_methods'] = [message['method'] for message in initial_messages]
            if not ready:
                report['error'] = 'SDK readiness event not observed; route request was not sent'
                return report
            sock.sendall(plan(start,end,option,calculation))
            deadline = time.monotonic()+timeout
            while time.monotonic()<deadline:
                sock.settimeout(max(.1,deadline-time.monotonic()))
                try:
                    message = reader.message()
                except socket.timeout:
                    report['timeout'] = True
                    break
                safe_event = {'method':message['method'],'type':message['type'],
                              'sequence':message['sequence']}
                if message['method']=='onCurrentRoutePlanParams':
                    params = message['fields'].get('1',{})
                    expected = [{'1':start[0],'2':start[1]}, {'1':end[0],'2':end[1]}]
                    echo_ok = all(
                        params.get(str(i+1),{}).get('1')==point
                        for i,point in enumerate(expected))
                    report['request_echo_matches'] = echo_ok
                    safe_event['request_echo_matches'] = echo_ok
                if message['method']=='onRouteResult':
                    values = message['fields']
                    safe_event.update({'result_status':values.get('1'),
                                       'calculator':values.get('3'),
                                       'alternative_count':len(values.get('2',[]))
                                           if isinstance(values.get('2'),list) else 0})
                    if values.get('1')==0:
                        # INVALID may be the connection's current-state snapshot;
                        # keep listening for a terminal calculation result.
                        report['events'].append(safe_event)
                        continue
                    report['success'] = (values.get('1')==7 and values.get('3')==0 and
                                         bool(values.get('2')) and report.get('request_echo_matches',False))
                    report['events'].append(safe_event)
                    break
                report['events'].append(safe_event)
    except (EOFError,OSError,ValueError) as error:
        report['error'] = str(error)
    finally:
        adb('forward','--remove',f'tcp:{local}')
    return report


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true',required=True,
                        help='Explicitly authorize one SDK route request; no guidance is started')
    parser.add_argument('--device',required=True,help='Caller-selected adb serial')
    parser.add_argument('--port',type=int,required=True,help='Neusoft Thrift service port')
    parser.add_argument('--library-path-device',required=True,
                        help='Caller-selected remote libNdsLib.so path')
    parser.add_argument('--expected-library-sha256',required=True,
                        help='Required native library profile SHA-256')
    parser.add_argument('--start',nargs=2,type=float,required=True,metavar=('LAT','LON'))
    parser.add_argument('--end',nargs=2,type=float,required=True,metavar=('LAT','LON'))
    parser.add_argument('--option',type=int,required=True)
    parser.add_argument('--calculation',type=int,default=None)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    if not (1 <= args.port <= 65535):
        parser.error('--port must be in 1..65535')
    if not re.fullmatch(r'[0-9a-fA-F]{64}', args.expected_library_sha256):
        parser.error('--expected-library-sha256 must be 64 hexadecimal characters')
    lib_path = Path(args.library_path_device)
    if not args.library_path_device.startswith('/') or '..' in lib_path.parts:
        parser.error('--library-path-device must be an absolute safe device path')
    for label, point in (('--start', args.start), ('--end', args.end)):
        if not (-90 <= point[0] <= 90 and -180 <= point[1] <= 180):
            parser.error(f'{label} latitude/longitude is out of range')
    if args.output.exists() or not args.output.parent.is_dir():
        parser.error('--output must be a new file inside an existing directory')
    report = probe(args.device,args.port,args.start,args.end,option=args.option,calculation=args.calculation,
                   library_path=args.library_path_device,
                   expected_library_sha256=args.expected_library_sha256.lower())
    encoded = (json.dumps(report,indent=2)+'\n').encode()
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(encoded)
    print(json.dumps({'success':report.get('success',False),
                      'readiness_observed':report.get('readiness_observed',False),
                      'event_methods':report.get('initial_event_methods',[]),
                      'report':str(args.output)}))
