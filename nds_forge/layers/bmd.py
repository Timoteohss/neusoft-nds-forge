"""Experimental BMD tile compiler adapted from the local Curitiba builder.

This stage emits per-tile BMD geometry blobs and a deterministic manifest. It
does not emit the SQLite-backed `BMD.NDS` database, names database, style
catalog, or a Neusoft package; those require explicit profile schema seeds.
"""
from __future__ import annotations

import hashlib
import itertools
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .bmd_codec import encode_road_tile, nds_coordinate, tile_for, tile_origin
from .bmd_levels import validate_bmd_levels


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


def _point_at(line: list, t: float) -> tuple[float, float]:
    seg = min(int(t), len(line) - 2)
    frac = t - seg
    return (
        line[seg][0] + (line[seg + 1][0] - line[seg][0]) * frac,
        line[seg][1] + (line[seg + 1][1] - line[seg][1]) * frac,
    )


def _quantize(lon: float, lat: float, shift: int, origin: tuple[int, int]) -> tuple[int, int]:
    coord = (nds_coordinate(lon), nds_coordinate(lat))
    return (
        origin[0] + round((coord[0] - origin[0]) / (1 << shift)) * (1 << shift),
        origin[1] + round((coord[1] - origin[1]) / (1 << shift)) * (1 << shift),
    )


def _split_polyline_by_tile(
    line: list, level: int, shift: int
) -> list[tuple[int, list[tuple[int, int]]]]:
    """Cut a whole lon/lat polyline at NDS tile edges, keeping point order.

    Consecutive input vertices that fall in the same tile are emitted as one
    continuous quantized fragment. A new fragment starts only at tile
    boundaries, exactly where the old per-segment splitter cut. Collapsed
    fragments (<2 distinct quantized points) are dropped.
    """
    if not 0 <= level <= 15 or not 0 <= shift < 31 - level:
        raise ValueError("Invalid profile level/shift")
    step = 1 << (31 - level)
    n = len(line)
    if n < 2:
        return []
    # Precompute NDS coords once; per-segment cuts reuse the old rule.
    nds = [(nds_coordinate(p[0]), nds_coordinate(p[1]) & 0x7FFFFFFF) for p in line]
    # Every original vertex bounds an interval, including vertices exactly
    # on a tile edge. Midpoint classification is valid only within a segment.
    cuts: set[float] = {float(i) for i in range(n)}
    for i in range(n - 1):
        xa, ya = nds[i]
        xb, yb = nds[i + 1]
        for start, end, mask in ((xa, xb, 0xFFFFFFFF), (ya, yb, 0x7FFFFFFF)):
            start &= mask
            end &= mask
            low, high = sorted((start, end))
            first = (low // step + 1) * step
            for boundary in range(first, high, step):
                t = (boundary - start) / (end - start) if end != start else 0.0
                if 1e-12 < t < 1 - 1e-12:
                    cuts.add(i + t)
    ordered = sorted(cuts)
    fragments: list[tuple[int, list[tuple[int, int]]]] = []
    origin_by_tile: dict[int, tuple[int, int]] = {}
    current_tile: int | None = None
    current_points: list[tuple[float, float]] = []

    def flush() -> None:
        nonlocal current_tile, current_points
        if current_tile is not None and len(current_points) >= 2:
            origin = origin_by_tile.setdefault(current_tile, tile_origin(current_tile, level))
            quantized = [_quantize(lon, lat, shift, origin) for lon, lat in current_points]
            merged: list[tuple[int, int]] = [quantized[0]]
            for point in quantized[1:]:
                if point != merged[-1]:
                    merged.append(point)
            if len(merged) >= 2:
                fragments.append((current_tile, merged))
        current_tile, current_points = None, []

    for lo, hi in itertools.pairwise(ordered):
        if hi - lo <= 1e-12:
            continue
        mid = _point_at(line, (lo + hi) / 2)
        tile = tile_for(mid[0], mid[1], level)
        if tile != current_tile:
            flush()
            current_tile = tile
            # Start the new fragment at the interval start; the shared
            # boundary point chains fragments without duplicating vertices.
            current_points = [_point_at(line, lo)]
        current_points.append(_point_at(line, hi))
    flush()
    return fragments


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
    frc_map = profile["frc_by_highway"]
    if not isinstance(frc_map, dict) or not isinstance(profile["excluded_highways"], list):
        raise ValueError("Invalid BMD FRC/exclusion policy")  # noqa: TRY004 -- ValueError is the established validation exception; changing its class changes caller-visible behavior
    default_frc = int(profile["default_frc"])
    if not 0 <= default_frc <= 7 or any(not 0 <= int(v) <= 7 for v in frc_map.values()):
        raise ValueError("BMD FRC must be in range 0..7")

    document = json.loads(roads_path.read_text(encoding="utf-8"))
    raw_levels = profile.get("bmd_levels")
    if raw_levels is not None:
        return _compile_levels(
            document, raw_levels, frc_map, default_frc, profile,
            source, region, report_path, roads_path, output_dir)
    level, shift = int(profile["level"]), int(profile["shift"])
    if not 0 <= level <= 15 or not 0 <= shift < 31 - level:
        raise ValueError("Invalid profile level/shift")
    return _compile_single(
        document, level, shift, None, frc_map, default_frc, profile,
        source, region, report_path, roads_path, output_dir)


def _collect_grouped(document: dict, level: int, shift: int, frc_max: int | None,
                     frc_map: dict, default_frc: int, profile: dict,
                     name_values: set[str]):
    grouped: dict[int, list[tuple]] = defaultdict(list)
    for feature in document.get("features", []):
        props = feature.get("properties") or {}
        highway = props.get("highway")
        if not isinstance(highway, str):
            continue
        if highway in profile["excluded_highways"]:
            continue
        frc = int(frc_map.get(highway, default_frc))
        if frc_max is not None and frc > frc_max:
            continue
        name = props.get("name")
        if name is not None:
            if not isinstance(name, str) or not name:
                raise ValueError("OSM name must be a non-empty string when present")
            name_values.add(name)
        for line in _line_parts(feature.get("geometry") or {}):
            # The splitter already emits each contiguous run of this part.
            # Never join separate parts/features or nonconsecutive tile visits,
            # even if their quantized endpoints, OSM IDs and attributes match.
            for tile, points in _split_polyline_by_tile(line, level, shift):
                grouped[tile].append((points, frc, name))
    return grouped


def _write_tiles(by_tile: dict[int, list[tuple]], names: dict[str, int],
                 level: int, shift: int, tiles_dir: Path):
    tiles_dir.mkdir(parents=True, exist_ok=True)
    tile_records = []
    for tile, lines in sorted(by_tile.items()):
        rows = [(points, frc, names.get(name)) for points, frc, name in lines]
        payload = encode_road_tile(rows, level, shift, tile)
        path = tiles_dir / f"{tile}.bmd.bin"
        path.write_bytes(payload)
        tile_records.append({"tile_id": tile, "path": f"tiles/{path.name}",
                             "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
                             "line_count": len(rows),
                             "named_line_count": sum(1 for _, _, name in rows if name is not None)})
    return tile_records


def _compile_single(document: dict, level: int, shift: int, frc_max: int | None,
                    frc_map: dict, default_frc: int, profile: dict,
                    source: dict, region: dict,
                    report_path: Path, roads_path: Path, output_dir: Path):
    name_values: set[str] = set()
    grouped = _collect_grouped(document, level, shift, frc_max, frc_map,
                               default_frc, profile, name_values)
    names = {value: int(profile.get("name_id_start", 10001)) + i
             for i, value in enumerate(sorted(name_values))}
    output_dir.mkdir(parents=True, exist_ok=True)
    by_tile = grouped
    feature_count = sum(len(lines) for lines in by_tile.values())
    tile_records = _write_tiles(by_tile, names, level, shift, output_dir / "tiles")
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


def _compile_levels(document: dict, raw_levels: list, frc_map: dict, default_frc: int,
                    profile: dict, source: dict, region: dict,
                    report_path: Path, roads_path: Path, output_dir: Path):
    validate_bmd_levels(raw_levels)
    levels = []
    for entry in raw_levels:
        lv = {
            "level": int(entry["level"]),
            "shift": int(entry["shift"]),
            "frc_max": None if entry.get("frc_max") is None else int(entry["frc_max"]),
            "min_scale": entry.get("min_scale"),
            "max_scale": entry.get("max_scale"),
        }
        levels.append(lv)
    # Global name table first so ids are stable across levels.
    name_values: set[str] = set()
    for lv in levels:
        _collect_grouped(document, lv["level"], lv["shift"], lv["frc_max"],
                         frc_map, default_frc, profile, name_values)
    names = {value: int(profile.get("name_id_start", 10001)) + i
             for i, value in enumerate(sorted(name_values))}
    level_entries = []
    all_tile_records = []
    for lv in levels:
        grouped = _collect_grouped(document, lv["level"], lv["shift"], lv["frc_max"],
                                   frc_map, default_frc, profile, set())
        by_tile = grouped
        level_dir = output_dir / "levels" / str(lv["level"])
        tile_records = _write_tiles(by_tile, names, lv["level"], lv["shift"],
                                    level_dir / "tiles")
        # Rewrite paths relative to the level dir for manifest consumers.
        for rec in tile_records:
            rec["path"] = f"levels/{lv['level']}/tiles/{Path(rec['path']).name}"
        counts = {"encoded_features": sum(r["line_count"] for r in tile_records),
                  "tiles": len(tile_records)}
        level_entries.append({"level": lv["level"], "shift": lv["shift"],
                              "frc_max": lv["frc_max"], "min_scale": lv["min_scale"],
                              "max_scale": lv["max_scale"],
                              "counts": counts, "tiles": tile_records})
        # Root tiles aggregate every level with its level attached, so legacy
        # catalog/package readers keep working without schema change.
        for rec in tile_records:
            all_tile_records.append({**rec, "level": lv["level"],
                                     "shift": lv["shift"]})
    output_dir.mkdir(parents=True, exist_ok=True)
    names_path = output_dir / "street_names.json"
    names_path.write_text(json.dumps(names, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    manifest = {
        "schema_version": 1, "step": "geometry.bmd", "maturity": "experimental",
        "profile": {"bmd_levels": levels, "frc_by_highway": frc_map,
                    "default_frc": default_frc,
                    "excluded_highways": sorted(profile["excluded_highways"])},
        "source": {"sha256": source["sha256"], "license": source.get("license"),
                   "attribution": source.get("attribution")},
        "clip": {"report_sha256": _sha256(report_path), "roads_sha256": _sha256(roads_path),
                 "region_id": region.get("id"), "bbox_lon_lat": region.get("bbox_lon_lat")},
        "counts": {"input_features": len(document.get("features", [])),
                   "levels": {str(e["level"]): e["counts"]["encoded_features"]
                              for e in level_entries},
                   "tiles": len(all_tile_records),
                   "unique_names": len(names)},
        "levels": level_entries,
        "outputs": {"street_names.json": {"sha256": _sha256(names_path),
                                           "bytes": names_path.stat().st_size}},
        "tiles": all_tile_records,
        "capabilities_produced": ["bmd_tile_geometry", "osm_way_ids_not_encoded_in_nds",
                                  "bmd_multi_level"],
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
