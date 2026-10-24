"""Experimental NDS.Classic BMD polyline geometry codec.

Storage and the geometry stream are separate. This does not compile routing,
names, or full BMD feature attributes. Bit layout comes from the installed
Neusoft reader, not an assumed interchange format.
"""
import itertools
import math
from dataclasses import dataclass

SCALE = (1 << 31) / 180


def signed(value, bits=32):
    return value - (1 << bits) if value & (1 << (bits-1)) else value


def nds_coordinate(degrees):
    return round(degrees*SCALE)


def morton(x, y, bits=16):
    return sum(((x >> i) & 1) << (2*i) | ((y >> i) & 1) << (2*i+1) for i in range(bits))


def tile_for(lon, lat, level):
    if not 0 <= level <= 15:
        raise ValueError("Unsupported tile level")
    shift = 31-level
    x = (nds_coordinate(lon) & 0xffffffff) >> shift
    y = (nds_coordinate(lat) & 0x7fffffff) >> shift
    return (1 << (16+level)) | morton(x,y,level+1)


def tile_origin(tile, level):
    code = tile & ((1 << (2*level+1))-1)
    x = sum(((code >> (2*i)) & 1) << i for i in range(level+1))
    y = sum(((code >> (2*i+1)) & 1) << i for i in range(level))
    step = 1 << (31-level)
    ox = signed((x*step + step//2) & 0xffffffff)
    # Latitude uses a half-world index and signed coordinates.
    oy = (y*step + step//2)
    if oy >= 1 << 30:
        oy -= 1 << 31
    return ox, oy


@dataclass
class Bits:
    data: str = ""
    at: int = 0

    @classmethod
    def from_bytes(cls, data):
        return cls("".join(f"{b:08b}" for b in data))

    def read(self, width, is_signed=False):
        if width < 0 or self.at+width > len(self.data):
            raise ValueError("Truncated bit stream")
        value = int(self.data[self.at:self.at+width] or "0",2)
        self.at += width
        return signed(value,width) if is_signed else value

    def put(self, value, width):
        if width <= 0 or not -(1 << (width-1)) <= value < 1 << width:
            raise ValueError("Value out of range")
        self.data += format(value & ((1 << width)-1),f"0{width}b")

    def var_count(self):
        value = self.read(4)
        if value == 0:
            value = self.read(8)
            if value == 255:
                value = self.read(16)
        return value

    def put_count(self, value):
        if not 0 <= value < 65536:
            raise ValueError("Count out of range")
        if 1 <= value <= 15:
            self.put(value,4)
        else:
            self.put(0,4)
            self.put(min(255, value),8)
            if value >= 255:
                self.put(value,16)

    def bytes(self):
        padded = self.data+"0"*((-len(self.data))%8)
        return int(padded or "0",2).to_bytes(len(padded)//8,"big")


def decode_polyline(blob, bit_offset, coordinate_bits, shift, origin):
    stream = Bits.from_bytes(blob)
    stream.at = bit_offset
    count = stream.var_count()
    x = stream.read(coordinate_bits+1,True)
    y = stream.read(coordinate_bits+1,True)
    result = [(origin[0]+(x<<shift),origin[1]+(y<<shift))]
    delta_bits = stream.read(4)+1
    for _ in range(count-1):
        x += stream.read(delta_bits,True)
        y += stream.read(delta_bits,True)
        result.append((origin[0]+(x<<shift),origin[1]+(y<<shift)))
    return result, stream.at


def encode_polyline(points, coordinate_bits, shift, origin):
    if len(points) < 2:
        raise ValueError("Need at least two vertices")
    local = [(round((x-origin[0])/(1<<shift)),round((y-origin[1])/(1<<shift))) for x,y in points]
    subdivided = [local[0]]
    for b in local[1:]:
        a = subdivided[-1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        max_d = max(abs(dx), abs(dy))
        if max_d >= 32768:
            steps = math.ceil(max_d / 30000)
            for s in range(1, steps):
                subdivided.append((
                    round(a[0] + dx * s / steps),
                    round(a[1] + dy * s / steps)
                ))
        subdivided.append(b)
    local = subdivided
    deltas = [(b[0]-a[0],b[1]-a[1]) for a,b in itertools.pairwise(local)]
    width = next((w for w in range(1,17) if all(-(1<<(w-1)) <= v < 1<<(w-1) for d in deltas for v in d)),None)
    if width is None:
        raise ValueError("Deltas exceed 16-bit geometry range; subdivide or reduce precision")
    stream = Bits()
    stream.put_count(len(local))
    for v in local[0]:
        if not -(1<<coordinate_bits) <= v < 1<<coordinate_bits:
            raise ValueError("Initial point outside tile coordinate range")
        stream.put(v,coordinate_bits+1)
    stream.put(width-1,4)
    for dx,dy in deltas:
        stream.put(dx,width)
        stream.put(dy,width)
    return stream


def encode_road_tile(lines, level, shift, tile, frc=4):
    """NDS 2.4.4 BMD polyline geometry and name attribute codec.

    Each element of `lines` may either be:
    - a list of points
    - a tuple `(points, line_frc)`
    - a tuple `(points, line_frc, name_id)`
    """
    if not 0 <= frc <= 7 or not 1 <= len(lines) <= 65535:
        raise ValueError("Invalid FRC or feature count")
    # The name-association count reserves its top bit as an encoding marker.
    # Check before encoding geometry; never silently wrap a dense parent tile.
    named_count = sum(isinstance(item, tuple) and len(item) == 3 and item[2] is not None
                      for item in lines)
    if named_count > 32767:
        raise ValueError("BMD name association count exceeds 32767 per tile")

    origin = tile_origin(tile, level)
    lines_body = Bits()
    lines_body.put(0, 8)  # No shared road attribute groups.
    lines_body.put(1, 8)  # One line feature class.
    lines_body.put(1, 8)  # Road feature class.
    lines_body.put(len(lines), 16)

    name_entries = []
    for idx, item in enumerate(lines):
        line_name_id = None
        if isinstance(item, tuple):
            if len(item) == 3:
                line, line_frc, line_name_id = item
            elif len(item) == 2 and isinstance(item[1], int):
                line, line_frc = item
            else:
                line, line_frc = item[0], frc
        else:
            line, line_frc = item, frc

        if not 0 <= line_frc <= 7:
            raise ValueError(f"Invalid line FRC {line_frc}")

        lines_body.put(0, 1)  # No scale sublevel override.
        lines_body.put(3, 2)  # Inline road attributes.
        lines_body.put(line_frc << 12, 15)  # Functional road class, optional flags clear.
        lines_body.data += encode_polyline(line, 31 - level - shift, shift, origin).data

        if line_name_id is not None:
            name_entries.append((idx, int(line_name_id)))

    # Bounded padding lets native readers prefetch safely at end of stream.
    lines_body.put(0, 16)
    lines_bytes = lines_body.bytes()

    if not name_entries:
        # 0x40 Line section only.
        result = Bits()
        result.put(0x40, 8)
        result.put(5, 32)
        return result.bytes() + lines_bytes

    # 0x42 Line section + Name attributes section.
    attr_offset = 9 + len(lines_bytes)
    table_offset = attr_offset + 9
    desc_val = (8 << 36) | (table_offset << 4)
    attr_header = bytes([1, 1, 0, 0x96]) + desc_val.to_bytes(5, "big")

    table_bits = Bits()
    table_bits.put(0, 2)  # Mode 0.
    cnt = len(name_entries)
    if cnt < 128:
        table_bits.put(cnt, 8)
    else:
        table_bits.put(0x8000 | cnt, 16)
    table_bytes = table_bits.bytes()

    entry_bytes = bytearray()
    for l_idx, n_id in name_entries:
        entry_bytes += l_idx.to_bytes(2, "big") + n_id.to_bytes(4, "big")

    attr_bytes = attr_header + table_bytes + bytes(entry_bytes)
    tile_header = bytes([0x42]) + (9).to_bytes(4, "big") + attr_offset.to_bytes(4, "big")
    return tile_header + lines_bytes + attr_bytes
