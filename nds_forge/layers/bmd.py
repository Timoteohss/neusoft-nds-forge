"""Experimental BMD tile compiler adapted from the local Curitiba builder.

This stage emits per-tile BMD geometry blobs and a deterministic manifest. It
does not emit the SQLite-backed `BMD.NDS` database, names database, style
catalog, or a Neusoft package; those require explicit profile schema seeds.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .bmd_codec import encode_road_tile, nds_coordinate, tile_for, tile_origin


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _line_parts(geometry: dict[str, Any]):
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if kind == "LineString":
        yield coordinates
    elif kind == "MultiLineString":
        yield from coordinates
    else:
        raise ValueError(f"Unsupported clipped road geometry: {kind!r}")


def _split_segment_by_tile(a, b, level: int, shift: int):
    """Split one lon/lat segment at NDS tile edges and quantize each fragment."""
    if not 0 <= level <= 15 or not 0 <= shift < 31 - level:
        raise ValueError("Invalid profile level/shift")
    xa, ya = nds_coordinate(a[0]), nds_coordinate(a[1]) & 0x7fffffff
    xb, yb = nds_coordinate(b[0]), nds_coordinate(b[1]) & 0x7fffffff
    step = 1 << (31 - level)
    cuts = {0.0, 1.0}
    for start, end, mask in ((xa, xb, 0xffffffff), (ya, yb, 0x7fffffff)):
        start &= mask
        end &= mask
        low, high = sorted((start, end))
        first = (low // step + 1) * step
        for boundary in range(first, high, step):
            t = (boundary - start) / (end - start) if end != start else 0.0
            if 1e-12 < t < 1 - 1e-12:
                cuts.add(t)
    origin_by_tile: dict[int, tuple[int, int]] = {}
    ordered = sorted(cuts)
    for lo, hi in zip(ordered, ordered[1:]):
        if hi - lo <= 1e-12:
            continue
        mid = (lo + hi) / 2
        midpoint = (a[0] + (b[0] - a[0]) * mid,
                    a[1] + (b[1] - a[1]) * mid)
        tile = tile_for(*midpoint, level)
        origin = origin_by_tile.setdefault(tile, tile_origin(tile, level))
        points = []
        for t in (lo, hi):
            point = (a[0] + (b[0] - a[0]) * t,
                     a[1] + (b[1] - a[1]) * t)
            coord = (nds_coordinate(point[0]), nds_coordinate(point[1]))
            points.append(tuple(origin[i] + round((coord[i] - origin[i]) / (1 << shift)) * (1 << shift)
                                for i in range(2)))
        if points[0] != points[1]:
            yield tile, points


def compile_bmd_from_clip(clip_dir: Path, output_dir: Path, profile: dict[str, Any]) -> dict[str, Any]:
    """Compile clipped road ways into BMD blobs using explicit profile policy.

    Required profile fields: `level`, `shift`, `frc_by_highway`, `default_frc`,
    and `excluded_highways`. The compiler intentionally refuses implicit road
    class defaults. Its source is the output of `osm.clip`, not a packaged NDS.
    """
    report_path = clip_dir / "clip-report.json"
    roads_path = clip_dir / "roads.geojson"
    if not report_path.is_file() or not roads_path.is_file():
        raise FileNotFoundError("osm.clip must provide roads.geojson and clip-report.json")
    clip_report = json.loads(report_path.read_text(encoding="utf-8"))
    source = clip_report.get("source", {})
    region = clip_report.get("region", {})
    if not source.get("sha256") or not region.get("resolved"):
        raise ValueError("BMD requires a resolved clip with source hash/provenance")
    for field in ("level", "shift", "frc_by_highway", "default_frc", "excluded_highways"):
        if field not in profile:
            raise ValueError(f"BMD profile lacks explicit {field}")
    level, shift = int(profile["level"]), int(profile["shift"])
    frc_map = profile["frc_by_highway"]
    if not isinstance(frc_map, dict) or not isinstance(profile["excluded_highways"], list):
        raise ValueError("Invalid BMD FRC/exclusion policy")
    default_frc = int(profile["default_frc"])
    if not 0 <= default_frc <= 7 or any(not 0 <= int(v) <= 7 for v in frc_map.values()):
        raise ValueError("BMD FRC must be in range 0..7")

    document = json.loads(roads_path.read_text(encoding="utf-8"))
    grouped: dict[int, list[tuple]] = defaultdict(list)
    name_values: set[str] = set()
    feature_count = 0
    for feature in document.get("features", []):
        props = feature.get("properties") or {}
        highway = props.get("highway")
        if not isinstance(highway, str):
            continue
        if highway in profile["excluded_highways"]:
            continue
        frc = int(frc_map.get(highway, default_frc))
        name = props.get("name")
        if name is not None:
            if not isinstance(name, str) or not name:
                raise ValueError("OSM name must be a non-empty string when present")
            name_values.add(name)
        for line in _line_parts(feature.get("geometry") or {}):
            for a, b in zip(line, line[1:]):
                for tile, points in _split_segment_by_tile(a, b, level, shift):
                    grouped[tile].append((points, frc, name))
                    feature_count += 1

    names = {value: int(profile.get("name_id_start", 10001)) + i
             for i, value in enumerate(sorted(name_values))}
    output_dir.mkdir(parents=True, exist_ok=True)
    tiles_dir = output_dir / "tiles"
    tiles_dir.mkdir()
    tile_records = []
    for tile, lines in sorted(grouped.items()):
        rows = [(points, frc, names.get(name)) for points, frc, name in lines]
        payload = encode_road_tile(rows, level, shift, tile)
        path = tiles_dir / f"{tile}.bmd.bin"
        path.write_bytes(payload)
        tile_records.append({"tile_id": tile, "path": f"tiles/{path.name}",
                             "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
                             "line_count": len(rows),
                             "named_line_count": sum(name is not None for _, _, name in lines)})
    names_path = output_dir / "street_names.json"
    names_path.write_text(json.dumps(names, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    manifest = {
        "schema_version": 1, "step": "geometry.bmd", "maturity": "experimental",
        "profile": {"level": level, "shift": shift, "frc_by_highway": frc_map,
                    "default_frc": default_frc,
                    "excluded_highways": sorted(profile["excluded_highways"])},
        "source": {"sha256": source["sha256"], "license": source.get("license"),
                   "attribution": source.get("attribution")},
        "clip": {"report_sha256": _sha256(report_path), "roads_sha256": _sha256(roads_path),
                 "region_id": region.get("id"), "bbox_lon_lat": region.get("bbox_lon_lat")},
        "counts": {"input_features": len(document.get("features", [])),
                   "encoded_segments": feature_count, "tiles": len(tile_records),
                   "unique_names": len(names)},
        "outputs": {"street_names.json": {"sha256": _sha256(names_path),
                                           "bytes": names_path.stat().st_size}},
        "tiles": tile_records,
        "capabilities_produced": ["bmd_tile_geometry", "osm_way_ids_not_encoded_in_nds"],
        "capabilities_missing": ["Neusoft_SQLite_BMD.NDS", "3D", "full_name_catalog",
                                 "native_render_validation"],
        "limitations": ["Partial legacy BMD codec; no style/pattern/catalog builder.",
                        "Unknown highway values use only the explicit profile default FRC.",
                        "This is not a complete Neusoft package or validated regional map."],
    }
    manifest_path = output_dir / "bmd-manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    return {"counts": manifest["counts"], "manifest": "bmd-manifest.json",
            "capabilities_produced": manifest["capabilities_produced"],
            "capabilities_missing": manifest["capabilities_missing"],
            "maturity": "experimental"}
