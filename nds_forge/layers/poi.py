"""Curated POI input adapter producing canonical records, not POI.NDS."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .bmd_codec import nds_coordinate
from .names import morton64, strip_accents


def compile_curated_pois(source_path: Path, output_dir: Path, profile: dict[str, Any],
                         category_map: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate and normalize a user-supplied POI FeatureCollection.

    The input is deliberately explicit (for example a curated ten-POI set).
    This adapter does not imply comprehensive OSM POI extraction or Neusoft
    category coverage.
    """
    category_map = category_map if category_map is not None else profile.get("poi_categories")
    if not isinstance(category_map, dict) or not category_map:
        raise ValueError("POI profile requires an explicit poi_categories mapping")
    region_id = profile.get("region_id")
    if not isinstance(region_id, int):
        raise ValueError("POI profile requires an integer region_id")
    document = json.loads(source_path.read_text(encoding="utf-8"))
    if document.get("type") == "FeatureCollection" and isinstance(document.get("features"), list):
        source_records = []
        for feature in document["features"]:
            props = feature.get("properties") or {}
            geometry = feature.get("geometry") or {}
            coordinates = geometry.get("coordinates") or []
            source_records.append({"id": props.get("id"), "name": props.get("name"),
                                  "category": props.get("category"),
                                  "lon": coordinates[0] if len(coordinates) >= 2 else None,
                                  "lat": coordinates[1] if len(coordinates) >= 2 else None,
                                  "aliases": props.get("aliases", []),
                                  "source_feature_id": feature.get("id")})
    elif document.get("schema_version") == 1 and isinstance(document.get("records"), list):
        source_records = document["records"]
        if document.get("profile_id") != profile.get("profile_id"):
            raise ValueError("Curated POI profile_id does not match the selected profile")
    else:
        raise ValueError("Curated POI source must be GeoJSON FeatureCollection or schema_version=1 records JSON")
    ids: set[str] = set()
    records = []
    rejected = []
    for source_record in source_records:
        ident, name, category_key = (source_record.get("id"), source_record.get("name"),
                                     source_record.get("category"))
        if not isinstance(ident, (str, int)) or not isinstance(name, str) or not name:
            rejected.append({"feature_id": source_record.get("source_feature_id"),
                             "reason": "missing_id_or_name"})
            continue
        ident = str(ident)
        if ident in ids:
            raise ValueError(f"Duplicate POI id: {ident}")
        ids.add(ident)
        if category_key not in category_map:
            rejected.append({"feature_id": ident, "reason": "unmapped_category"})
            continue
        try:
            lon, lat = float(source_record.get("lon")), float(source_record.get("lat"))
        except (TypeError, ValueError):
            rejected.append({"feature_id": ident, "reason": "unsupported_geometry"})
            continue
        if not -180 <= lon <= 180 or not -90 <= lat <= 90:
            rejected.append({"feature_id": ident, "reason": "coordinate_out_of_range"})
            continue
        aliases = source_record.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
            raise ValueError(f"POI aliases must be strings: {ident}")
        records.append({"id": ident, "name": name, "category": category_key,
                        "nds_category_id": category_map[category_key], "longitude": lon,
                        "latitude": lat,
                        "morton": morton64(nds_coordinate(lon) & 0xffffffff,
                                           nds_coordinate(lat) & 0xffffffff),
                        "aliases": "\x1c".join(dict.fromkeys([name, *aliases])),
                        "search_tags": strip_accents(name), "region_id": region_id,
                        "origin": "curated_external_input"})
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "poi-records.json"
    payload = {"schema_version": 1, "step": "poi.compile", "maturity": "experimental",
               "profile_id": profile.get("profile_id"), "records": records,
               "counts": {"input": len(source_records), "accepted": len(records),
                          "rejected": len(rejected)}, "rejected": rejected,
               "source": {"sha256": hashlib.sha256(source_path.read_bytes()).hexdigest()},
               "capabilities_produced": ["canonical_curated_poi_records"],
               "capabilities_missing": ["Neusoft_POI.NDS", "complete_osm_poi_coverage",
                                        "native_search_validation"]}
    output_path.write_text(json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return {"counts": payload["counts"], "outputs": {"poi-records.json": {
        "bytes": output_path.stat().st_size,
        "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest()}},
        "capabilities_produced": payload["capabilities_produced"],
        "capabilities_missing": payload["capabilities_missing"], "maturity": "experimental"}
