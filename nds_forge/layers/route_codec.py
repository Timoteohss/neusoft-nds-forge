"""Experimental ROUTE main/AUX codecs ported from the local verified probes.

These functions encode and parse bounded tile fixtures. They do not implement
turn restrictions, conditional access, non-inline attributes, or guidance.
"""
from __future__ import annotations

import struct
from typing import Any

from .bmd_codec import Bits, decode_polyline, encode_polyline, tile_origin


def read_var15(stream: Bits) -> int:
    first = stream.read(8)
    return ((first & 0x7f) << 8) | stream.read(8) if first & 0x80 else first


def put_var15(stream: Bits, value: int) -> None:
    if not 0 <= value < 32768:
        raise ValueError("External tile index exceeds the 15-bit codec")
    stream.put(0x8000 | value, 16) if value >= 128 else stream.put(value, 8)


def put_var29(stream: Bits, value: int) -> None:
    """Native 1..4 byte unsigned codec (7+7+7+8 payload bits)."""
    if not 0 <= value < (1 << 29):
        raise ValueError("Variable link value out of range")
    if value < 128:
        groups = [value]
    elif value < 16384:
        groups = [0x80 | (value >> 7), value & 127]
    elif value < 2097152:
        groups = [0x80 | (value >> 14), 0x80 | ((value >> 7) & 127), value & 127]
    else:
        groups = [0x80 | (value >> 22), 0x80 | ((value >> 15) & 127),
                  0x80 | ((value >> 8) & 127), value & 255]
    for group in groups:
        stream.put(group, 8)


def parse_main_header(blob: bytes) -> dict[str, Any]:
    if len(blob) < 2:
        raise ValueError("Truncated ROUTE header")
    flags = int.from_bytes(blob[:2], "big") >> 6
    end = 2 + flags.bit_count() * 4
    if end > len(blob):
        raise ValueError("Truncated ROUTE offsets")
    offsets = [0] * 10
    pos = 2
    for i in range(10):
        if flags & (1 << (9 - i)):
            offsets[i] = struct.unpack_from(">I", blob, pos)[0]
            pos += 4
            if not end <= offsets[i] < len(blob):
                raise ValueError("ROUTE offset outside payload")
    return {"flags": flags, "offsets": offsets, "header_bytes": end}


def parse_intersections(blob: bytes, tile: int, level: int, shift: int) -> list[dict[str, Any]]:
    header = parse_main_header(blob)
    start = header["offsets"][1]
    if not start:
        return []
    if header["offsets"][7]:
        raise NotImplementedError("Intersection patterns are not decoded")
    end = min((x for x in header["offsets"] if x > start), default=len(blob))
    stream = Bits.from_bytes(blob[start:end])
    count = stream.read(16)
    if count >= 32767:
        raise ValueError("Intersection count exceeds native limit")
    width = 32 - level - shift
    if not 1 <= width <= 32:
        raise ValueError("Invalid coordinate precision")
    origin = tile_origin(tile, level)
    result = []
    for index in range(count):
        bit_offset = stream.at
        xy = [stream.read(width, True), stream.read(width, True)]
        degree = stream.read(8)
        if not 1 <= degree <= 16:
            raise ValueError("Intersection degree outside native range")
        refs = []
        for _ in range(degree):
            external = bool(stream.read(1))
            external_index = read_var15(stream) if external else None
            ref = stream.read(16)
            refs.append({"external": external, "external_index": external_index,
                         "link": ref & 0x7fff, "direction_bit": ref >> 15})
        result.append({"index": index, "bit_offset": bit_offset,
                       "nds_coordinates": [origin[i] + (xy[i] << shift) for i in range(2)],
                       "links": refs})
    return result


def encode_route_tile(tile: int, level: int, shift: int, nodes: dict[Any, tuple[int, int]],
                      links: list[dict[str, Any]], node_links: dict,
                      external_refs: dict, external_tiles: list[int]) -> bytes:
    """Encode ROUTE intersections, local links, and external tile index list.

    This is the verified Curitiba-v9 main-table subset. Inputs use graph-local
    identities; turn-restriction and maneuver sections are not emitted.
    """
    if not 0 < len(nodes) < 32767 or not 0 < len(links) < 32768:
        raise ValueError(f"Tile {tile} exceeds native intersection/link limits")
    origin = tile_origin(tile, level)
    width = 32 - level - shift
    node_stream = Bits()
    node_stream.put(len(nodes), 16)
    for node_key, (x, y) in nodes.items():
        refs = list(node_links[node_key]) + list(external_refs.get(node_key, ()))
        if not 1 <= len(refs) <= 16:
            raise ValueError(f"Tile {tile} node degree {len(refs)} outside 1..16")
        for axis, coordinate in enumerate((x, y)):
            delta = coordinate - origin[axis]
            quantum = 1 << shift
            if delta % quantum:
                raise ValueError("Route intersection coordinate is not quantized")
            local = delta >> shift
            if not -(1 << (width - 1)) <= local < (1 << (width - 1)):
                raise ValueError(f"Tile {tile} coordinate falls outside route tile bounds")
            node_stream.put(local, width)
        node_stream.put(len(refs), 8)
        for external, ext_index, link_id, direction_bit in refs:
            node_stream.put(1 if external else 0, 1)
            if external:
                put_var15(node_stream, ext_index)
            node_stream.put((direction_bit << 15) | link_id, 16)

    link_stream = Bits()
    link_stream.put(len(links), 16)
    for edge in links:
        attrs = edge["attrs"]
        put_var29(link_stream, attrs["length_raw"])
        link_stream.put(3, 2)
        for key, bits, default in (("road_raw", 15, 4 << 12), ("access_raw", 7, 0),
                                   ("scalar8", 8, 0), ("scalar6_a", 6, 0),
                                   ("scalar6_b", 6, 0)):
            value = attrs.get(key, default)
            if not 0 <= value < (1 << bits):
                raise ValueError(f"{key} outside {bits}-bit range")
            link_stream.put(value, bits)

    node_bytes, link_bytes = node_stream.bytes(), link_stream.bytes()
    ext_bytes = bytes([len(external_tiles)]) + b"".join(int(t).to_bytes(4, "big") for t in external_tiles)
    header_bytes = 2 + 4 * 3
    intersection_offset = header_bytes
    link_offset = intersection_offset + len(node_bytes)
    ext_offset = link_offset + len(link_bytes)
    header = (0x181 << 6).to_bytes(2, "big") + b"".join(
        x.to_bytes(4, "big") for x in (intersection_offset, link_offset, ext_offset))
    return header + node_bytes + link_bytes + ext_bytes


def parse_aux_header(blob: bytes) -> dict[str, Any]:
    if len(blob) < 17:
        raise ValueError("Truncated routing auxiliary header")
    if blob[0] & 0x7f:
        raise NotImplementedError("Unsupported model-15 auxiliary flags")
    count = 5 if blob[0] & 0x80 else 4
    size = 1 + 4 * count
    if len(blob) < size:
        raise ValueError("Truncated routing auxiliary offsets")
    offsets = list(struct.unpack_from(">" + str(count) + "I", blob, 1))
    if any(x and not size <= x < len(blob) for x in offsets):
        raise ValueError("Routing auxiliary offset outside payload")
    return {"flags": blob[0], "offsets": offsets, "header_bytes": size,
            "geometries_offset": offsets[-2], "attributes_offset": offsets[-1]}


def parse_aux_geometries(blob: bytes, tile: int, level: int, shift: int) -> list[dict[str, Any]]:
    header = parse_aux_header(blob)
    start = header["geometries_offset"]
    if not start:
        raise ValueError("Missing geometry section")
    end = min((x for x in header["offsets"] if x > start), default=len(blob))
    section = blob[start:end]
    bits = Bits.from_bytes(section)
    count = bits.read(16)
    rows = []
    for index in range(count):
        at = bits.at
        mode = bits.read(2)
        if mode != 3:
            raise NotImplementedError("Auxiliary non-inline road attributes")
        road, access = bits.read(15), bits.read(7)
        points, bits.at = decode_polyline(section, bits.at, 31 - level - shift,
                                           shift, tile_origin(tile, level))
        external = bool(bits.read(1))
        external_index = read_var15(bits) if external else None
        link = bits.read(15)
        reference_value = read_var15(bits)
        has_extension = bool(bits.read(1))
        if has_extension:
            raise NotImplementedError("Auxiliary geometry extension")
        rows.append({"index": index, "bit_offset": at, "road_raw": road,
                     "access_raw": access, "points": points, "external": external,
                     "external_index": external_index, "link": link,
                     "reference_value": reference_value})
    return rows


def encode_aux_fixture(tile: int, level: int, shift: int, lines: list[list[tuple[int, int]]],
                       link_attributes: list[dict[str, int]] | None = None) -> bytes:
    """Encode one auxiliary shape row per link; not a sparse production writer."""
    if not 0 < len(lines) < 32768:
        raise ValueError("Invalid auxiliary link count")
    if link_attributes is not None and len(link_attributes) != len(lines):
        raise ValueError("One attribute specification is required per geometry")
    links, geometries = Bits(), Bits()
    links.put(len(lines), 16)
    geometries.put(len(lines), 16)
    origin = tile_origin(tile, level)
    for index, points in enumerate(lines):
        attrs = link_attributes[index] if link_attributes is not None else {}
        if not 2 <= len(points) < 65536:
            raise ValueError("Geometry needs at least two vertices")
        links.put(2, 2)
        geometries.put(3, 2)
        for name, width, default in (("road_raw", 15, 4 << 12), ("access_raw", 7, 0)):
            value = attrs.get(name, default)
            if not 0 <= value < (1 << width):
                raise ValueError(f"{name} out of range")
            geometries.put(value, width)
        geometries.data += encode_polyline(points, 31 - level - shift, shift, origin).data
        geometries.put(0, 1)
        geometries.put(index, 15)
        geometries.put(0, 8)
        geometries.put(0, 1)
    patterns = b"\0\0"
    link_data, geometry_data = links.bytes(), geometries.bytes()
    offsets = (17, 17 + len(patterns), 17 + len(patterns) + len(link_data),
               17 + len(patterns) + len(link_data) + len(geometry_data))
    return struct.pack(">B4I", 0, *offsets) + patterns + link_data + geometry_data + b"\0\0"


def encode_aux_sparse(tile: int, level: int, shift: int, links: list[dict[str, Any]],
                      retained_link_ids: set[int], *, guard_u16_offsets: bool = True) -> tuple[bytes, dict[str, int]]:
    """Encode all per-link map markers but geometry rows for selected links.

    Single-row omission passed one SDK A/B route in the v6 pilot; this policy is
    still experimental at municipal scale. `links` remain in main-link order.
    """
    if not 0 < len(links) < 32768:
        raise ValueError("Invalid auxiliary main-link count")
    if any(not isinstance(i, int) or i < 0 or i >= len(links) for i in retained_link_ids):
        raise ValueError("Retained AUX link ID is outside the main-link table")
    map_bits = Bits()
    map_bits.put(len(links), 16)
    for _ in links:
        map_bits.put(2, 2)  # Observed per-link marker; retained even when geometry is omitted.
    geometry_bits = Bits()
    geometry_bits.put(len(retained_link_ids), 16)
    max_relative_bit_offset = 0
    origin = tile_origin(tile, level)
    for link_id in sorted(retained_link_ids):
        link = links[link_id]
        points = link.get("points")
        attrs = link.get("attrs", {})
        if not isinstance(points, list) or not 2 <= len(points) < 65536:
            raise ValueError(f"Invalid AUX geometry for main link {link_id}")
        # Bits.at is a read cursor; the writer's current position is the
        # length of its accumulated binary-string buffer.
        relative_offset = len(geometry_bits.data) - 16
        max_relative_bit_offset = max(max_relative_bit_offset, relative_offset)
        if guard_u16_offsets and relative_offset > 65535:
            raise ValueError(f"AUX geometry row offset exceeds uint16 at main link {link_id}: {relative_offset}")
        geometry_bits.put(3, 2)
        for name, width, default in (("road_raw", 15, 4 << 12), ("access_raw", 7, 0)):
            value = attrs.get(name, default)
            if not 0 <= value < (1 << width):
                raise ValueError(f"{name} out of range for main link {link_id}")
            geometry_bits.put(value, width)
        geometry_bits.data += encode_polyline(points, 31 - level - shift, shift, origin).data
        geometry_bits.put(0, 1)  # local link reference
        geometry_bits.put(link_id, 15)  # main-link ordinal, not AUX row ordinal
        geometry_bits.put(0, 8)  # unresolved reference scalar as in fixture codec
        geometry_bits.put(0, 1)  # no extension
    patterns = b"\0\0"
    map_data, geometry_data = map_bits.bytes(), geometry_bits.bytes()
    offsets = (17, 17 + len(patterns), 17 + len(patterns) + len(map_data),
               17 + len(patterns) + len(map_data) + len(geometry_data))
    blob = struct.pack(">B4I", 0, *offsets) + patterns + map_data + geometry_data + b"\0\0"
    return blob, {"main_links": len(links), "retained_geometry_rows": len(retained_link_ids),
                  "max_row_relative_bit_offset": max_relative_bit_offset}
