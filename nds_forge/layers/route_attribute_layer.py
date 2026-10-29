"""Binary codec for the ROUTE auxiliary attribute layer (`nameAttributeLayer`).

Format reverse-engineered from `libNdsLib.so`
(sha256 c34a7e7a6201cb469bd6c9b2d62d36ef606f12a369ce3027969647a34a970e55) and
validated against 3.879 OEM layer blobs. Keep this codec data-driven: it does
not assign semantics to attribute ids beyond the two value shapes the native
reader accepts today.

Bit layout (serialization version 15, the only version observed):

    bit 0:      1 = extension present before the map list (never observed)
    var15:      map count
    per map:    u8 ids count, u16 ids (big-endian, MSB-first), 4-bit reference
                type, u32 absolute byte offset of the map container
    align:      to byte boundary
    containers: at the declared offsets, in map order

Container:

    2 bits:     mode (0 = one reference per entry, 1 = reference group)
    var15:      entry/group count
    mode 0:     align, one reference, align, values for every declared id
    mode 1:     align, var15 reference count, references, align, one shared
                value set for the whole group

References (read as 16/15 bits by type):

    1,3:        (direction << 15) | index, direction = 2 - mask (mask 1 or 2)
    2,4:        15-bit index, applies to both directions
    10:         implicit default reference (index 0xffff, no bits)

Values:

    0x90, 0x94, 0x95, 0x96: u32 namedObjectId
    0xB6:                   4-bit encoded type, string, 1-bit flag, payload
                            type 0/1: two var29 numbers
                            type 2:   var15 count + var29 numbers
                            type 3:   var15 count + strings
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

NAME_ATTRIBUTES = frozenset({0x90, 0x94, 0x95, 0x96})
HOUSE_NUMBER_ATTRIBUTE = 0xB6
DEFAULT_REFERENCE_INDEX = 0xFFFF


class LayerFormatError(ValueError):
    """Raised when a layer blob or model cannot be decoded or encoded."""


@dataclass
class _Reader:
    data: bytes
    at: int
    end: int

    def read(self, width: int) -> int:
        if width < 0 or self.at + width > self.end:
            raise LayerFormatError(f"read of {width} bits past {self.end} at {self.at}")
        value = 0
        for _ in range(width):
            byte = self.data[self.at >> 3]
            value = (value << 1) | ((byte >> (7 - (self.at & 7))) & 1)
            self.at += 1
        return value

    def align(self) -> None:
        self.at += (-self.at) % 8

    def var15(self) -> int:
        first = self.read(8)
        return ((first & 0x7F) << 8) | self.read(8) if first & 0x80 else first

    def var29(self) -> int:
        value = 0
        for index in range(4):
            byte = self.read(8)
            if index == 3:
                return (value << 8) | byte
            value = (value << 7) | (byte & 0x7F)
            if not byte & 0x80:
                return value
        raise LayerFormatError("unreachable var29")

    def string(self) -> dict[str, Any]:
        length = self.var15()
        raw = bytes(self.read(8) for _ in range(length))
        try:
            text: str | None = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        return {"text": text, "bytes_hex": raw.hex()}


@dataclass
class _Writer:
    """MSB-first bit writer mirroring :class:`_Reader`."""

    bits: list[int] = field(default_factory=list)

    def put(self, value: int, width: int) -> None:
        if width < 0 or value < 0 or value >= (1 << width):
            raise LayerFormatError(f"value {value} does not fit {width} bits")
        for shift in range(width - 1, -1, -1):
            self.bits.append((value >> shift) & 1)

    def align(self) -> None:
        self.bits.extend([0] * ((-len(self.bits)) % 8))

    def var15(self, value: int) -> None:
        if not 0 <= value < 0x8000:
            raise LayerFormatError(f"var15 value out of range: {value}")
        if value < 0x80:
            self.put(value, 8)
        else:
            self.put(0x80 | (value >> 8), 8)
            self.put(value & 0xFF, 8)

    def var29(self, value: int) -> None:
        if not 0 <= value < (1 << 29):
            raise LayerFormatError(f"var29 value out of range: {value}")
        if value < (1 << 7):
            self.put(value, 8)
        elif value < (1 << 14):
            self.put(0x80 | (value >> 7), 8)
            self.put(value & 0x7F, 8)
        elif value < (1 << 21):
            self.put(0x80 | (value >> 14), 8)
            self.put(0x80 | ((value >> 7) & 0x7F), 8)
            self.put(value & 0x7F, 8)
        else:
            self.put(0x80 | (value >> 21) & 0x7F, 8)
            self.put(0x80 | ((value >> 14) & 0x7F), 8)
            self.put(0x80 | ((value >> 7) & 0x7F), 8)
            self.put(value & 0xFF, 8)

    def string(self, entry: dict[str, Any]) -> None:
        if "bytes_hex" in entry:
            raw = bytes.fromhex(entry["bytes_hex"])
        elif entry.get("text") is not None:
            raw = entry["text"].encode("utf-8")
        else:
            raise LayerFormatError("string value needs bytes_hex or text")
        self.var15(len(raw))
        for byte in raw:
            self.put(byte, 8)

    def bytes(self) -> bytes:
        if len(self.bits) % 8:
            raise LayerFormatError("stream is not byte aligned")
        out = bytearray(len(self.bits) // 8)
        for index, bit in enumerate(self.bits):
            if bit:
                out[index >> 3] |= 1 << (7 - (index & 7))
        return bytes(out)


def _value(reader: _Reader, attribute: int) -> dict[str, Any]:
    if attribute in NAME_ATTRIBUTES:
        return {"kind": "name", "named_object_id": reader.read(32)}
    if attribute == HOUSE_NUMBER_ATTRIBUTE:
        encoded_type = reader.read(4)
        label = reader.string()
        flag = reader.read(1)
        entry: dict[str, Any] = {
            "kind": "house_number",
            "encoded_type": encoded_type,
            "label": label,
            "flag": flag,
        }
        if encoded_type in (0, 1):
            entry["numbers"] = [reader.var29(), reader.var29()]
        elif encoded_type == 2:
            entry["numbers"] = [reader.var29() for _ in range(reader.var15())]
        elif encoded_type == 3:
            entry["strings"] = [reader.string() for _ in range(reader.var15())]
        else:
            raise LayerFormatError(f"unsupported house encoded type {encoded_type}")
        return entry
    raise LayerFormatError(f"unsupported attribute {attribute:#x}")


def _reference(reader: _Reader, reference_type: int) -> dict[str, int]:
    if reference_type in (1, 3):
        raw = reader.read(16)
        return {"index": raw & 0x7FFF, "direction_mask": 2 - (raw >> 15)}
    if reference_type in (2, 4):
        return {"index": reader.read(15), "direction_mask": 3}
    if reference_type == 10:
        return {"index": DEFAULT_REFERENCE_INDEX, "direction_mask": 3}
    raise LayerFormatError(f"unsupported reference type {reference_type}")


def _container(data: bytes, descriptor: dict[str, Any], end_bit: int) -> dict[str, Any]:
    reader = _Reader(data, descriptor["offset"] * 8, end_bit)
    start = reader.at
    mode = reader.read(2)
    count = reader.var15()
    groups = []
    if mode not in (0, 1):
        raise LayerFormatError(f"unsupported container mode {mode} at {start}")
    for _ in range(count):
        reader.align()
        references = ([_reference(reader, descriptor["reference_type"])]
                      if mode == 0 else
                      [_reference(reader, descriptor["reference_type"])
                       for _ in range(reader.var15())])
        reader.align()
        values = [{"attribute_id": attribute, "value": _value(reader, attribute)}
                  for attribute in descriptor["attribute_ids"]]
        groups.append({"references": references, "values": values})
    return {"mode": mode, "count": count, "groups": groups, "start_bit": start,
            "end_bit": reader.at}


def read_attribute_layer(blob: bytes) -> dict[str, Any]:
    """Decode a layer blob into maps/containers/references/values."""
    reader = _Reader(blob, 0, len(blob) * 8)
    extension = reader.read(1)
    if extension:
        raise LayerFormatError("extension before the map list is not supported")
    map_count = reader.var15()
    descriptors: list[dict[str, Any]] = []
    for _ in range(map_count):
        ids = [reader.read(16) for _ in range(reader.read(8))]
        reference_type = reader.read(4)
        offset = reader.read(32)
        descriptors.append({"attribute_ids": ids, "reference_type": reference_type,
                            "offset": offset})
    reader.align()
    maps = []
    for index, descriptor in enumerate(descriptors):
        end_bit = (int(descriptors[index + 1]["offset"]) * 8
                   if index + 1 < len(descriptors) else len(blob) * 8)
        maps.append({**descriptor, "container": _container(blob, descriptor, end_bit)})
    return {"serialization_version": 15, "extension": 0, "map_count": map_count,
            "maps": maps}


def _write_value(writer: _Writer, attribute: int, entry: dict[str, Any]) -> None:
    if attribute in NAME_ATTRIBUTES:
        writer.put(int(entry["named_object_id"]), 32)
        return
    if attribute == HOUSE_NUMBER_ATTRIBUTE:
        encoded_type = int(entry["encoded_type"])
        writer.put(encoded_type, 4)
        writer.string(entry["label"])
        writer.put(int(entry["flag"]) & 1, 1)
        if encoded_type in (0, 1):
            numbers = entry["numbers"]
            if len(numbers) != 2:
                raise LayerFormatError("encoded types 0 and 1 carry exactly two numbers")
            writer.var29(int(numbers[0]))
            writer.var29(int(numbers[1]))
        elif encoded_type == 2:
            writer.var15(len(entry["numbers"]))
            for number in entry["numbers"]:
                writer.var29(int(number))
        elif encoded_type == 3:
            writer.var15(len(entry["strings"]))
            for string in entry["strings"]:
                writer.string(string)
        else:
            raise LayerFormatError(f"unsupported house encoded type {encoded_type}")
        return
    raise LayerFormatError(f"unsupported attribute {attribute:#x}")


def _write_reference(writer: _Writer, reference_type: int, entry: dict[str, int]) -> None:
    index = int(entry["index"])
    if reference_type in (1, 3):
        mask = int(entry.get("direction_mask", 3))
        if mask not in (1, 2):
            raise LayerFormatError("reference types 1/3 encode a single direction only")
        writer.put(((2 - mask) << 15) | index, 16)
        return
    if reference_type in (2, 4):
        writer.put(index & 0x7FFF, 15)
        return
    if reference_type == 10:
        if index != DEFAULT_REFERENCE_INDEX:
            raise LayerFormatError("reference type 10 carries no index bits")
        return
    raise LayerFormatError(f"unsupported reference type {reference_type}")


def write_attribute_layer(model: dict[str, Any]) -> bytes:
    """Encode a layer model back to bytes (round-trips OEM blobs byte for byte)."""
    maps = model.get("maps")
    if not isinstance(maps, list) or not maps:
        raise LayerFormatError("layer needs at least one map")
    header = _Writer()
    header.put(int(model.get("extension", 0)) & 1, 1)
    header.var15(len(maps))
    for descriptor in maps:
        ids = [int(value) for value in descriptor["attribute_ids"]]
        if not 0 < len(ids) < 256:
            raise LayerFormatError("attribute id count outside 1..255")
        header.put(len(ids), 8)
        for attribute in ids:
            header.put(attribute, 16)
        header.put(int(descriptor["reference_type"]), 4)
        header.put(0, 32)  # offset patched after containers are laid out
    header.align()
    header_bytes = header.bytes()
    containers = bytearray()
    offsets = []
    for descriptor in maps:
        offsets.append(len(header_bytes) + len(containers))
        writer = _Writer()
        container = descriptor["container"]
        mode = int(container["mode"])
        if mode not in (0, 1):
            raise LayerFormatError(f"unsupported container mode {mode}")
        writer.put(mode, 2)
        groups = container["groups"]
        writer.var15(len(groups))
        for group in groups:
            writer.align()
            references = group["references"]
            if mode == 0:
                if len(references) != 1:
                    raise LayerFormatError("mode 0 needs exactly one reference")
            else:
                writer.var15(len(references))
            for reference in references:
                _write_reference(writer, descriptor["reference_type"], reference)
            writer.align()
            values = group["values"]
            if len(values) != len(descriptor["attribute_ids"]):
                raise LayerFormatError("every declared attribute needs one value")
            for value in values:
                _write_value(writer, int(value["attribute_id"]), value["value"])
        writer.align()
        containers.extend(writer.bytes())
    out = bytearray(header_bytes)
    # Patch descriptor offsets in place: header layout is deterministic.
    bit = 1 + (16 if len(maps) >= 0x80 else 8)
    for descriptor, offset in zip(maps, offsets):
        bit += 8 + 16 * len(descriptor["attribute_ids"]) + 4
        for k in range(32):
            position = bit + k
            byte_index = position >> 3
            mask = 1 << (7 - (position & 7))
            if (offset >> (31 - k)) & 1:
                out[byte_index] |= mask
            else:
                out[byte_index] &= ~mask
        bit += 32
    out.extend(containers)
    return bytes(out)
