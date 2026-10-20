"""POI layer compiler supporting canonical OSM datasets and curated inputs.

Compiles POIs from OSM nodes, ways, and relations into indexed layer records with
explicit tag-category mapping, category inclusion/exclusion rules, spatial and name
deduplication, tile/morton indexing, and comprehensive rejection auditing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .bmd_codec import nds_coordinate, tile_for
from .names import morton64, strip_accents
from ..osm_model import OSMDataset


DEFAULT_TAG_CATEGORY_MAP: dict[tuple[str, str], tuple[str, int]] = {
    ("amenity", "fuel"): ("fuel", 1001),
    ("amenity", "restaurant"): ("restaurant", 1002),
    ("amenity", "cafe"): ("cafe", 1003),
    ("amenity", "fast_food"): ("fast_food", 1004),
    ("amenity", "hospital"): ("hospital", 1005),
    ("amenity", "pharmacy"): ("pharmacy", 1006),
    ("amenity", "bank"): ("bank", 1007),
    ("amenity", "atm"): ("atm", 1008),
    ("amenity", "parking"): ("parking", 1009),
    ("amenity", "charging_station"): ("charging_station", 1010),
    ("shop", "supermarket"): ("supermarket", 1011),
    ("shop", "convenience"): ("convenience", 1012),
    ("shop", "bakery"): ("bakery", 1013),
    ("tourism", "hotel"): ("hotel", 1014),
    ("tourism", "motel"): ("motel", 1015),
    ("tourism", "attraction"): ("attraction", 1016),
    ("tourism", "museum"): ("museum", 1017),
    ("leisure", "park"): ("park", 1018),
    ("historic", "monument"): ("monument", 1019),
}


@dataclass
class POICategoryRule:
    """Configurable category mapping, filtering, and deduplication rules."""
    tag_map: dict[tuple[str, str], tuple[str, int]] = field(
        default_factory=lambda: dict(DEFAULT_TAG_CATEGORY_MAP)
    )
    include_categories: set[str] | None = None
    exclude_categories: set[str] = field(default_factory=set)
    dedup_distance_meters: float = 50.0

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> POICategoryRule:
        tag_map = dict(DEFAULT_TAG_CATEGORY_MAP)
        if "custom_tag_map" in config:
            for k, v in config["custom_tag_map"].items():
                if isinstance(k, str) and "=" in k:
                    tag_k, tag_v = k.split("=", 1)
                    if isinstance(v, (list, tuple)) and len(v) == 2:
                        tag_map[(tag_k.strip(), tag_v.strip())] = (str(v[0]), int(v[1]))
                    elif isinstance(v, str):
                        tag_map[(tag_k.strip(), tag_v.strip())] = (v, 1000)

        includes = set(config["include_categories"]) if "include_categories" in config else None
        excludes = set(config.get("exclude_categories", []))
        dedup_dist = float(config.get("dedup_distance_meters", 50.0))
        return cls(
            tag_map=tag_map,
            include_categories=includes,
            exclude_categories=excludes,
            dedup_distance_meters=dedup_dist,
        )


def _approx_distance_meters(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Approximate planar distance in meters between two WGS84 coordinates."""
    lat_rad = math.radians((lat1 + lat2) / 2.0)
    dx = (lon1 - lon2) * 111320.0 * math.cos(lat_rad)
    dy = (lat1 - lat2) * 110540.0
    return math.hypot(dx, dy)


def compile_osm_pois(
    dataset_source: OSMDataset | dict[str, Any] | Path | str,
    output_dir: Path,
    profile: dict[str, Any],
    rules: POICategoryRule | None = None,
) -> dict[str, Any]:
    """Compile POI records from an OSMDataset with tag mapping, filtering, and dedup.

    Args:
        dataset_source: Loaded OSMDataset, dataset dict, or path to osm-dataset.json.
        output_dir: Output directory to write poi-records.json.
        profile: Profile configuration dict containing region_id, profile_id, grid, etc.
        rules: Optional POICategoryRule instance for category mapping and dedup.

    Returns:
        Step execution report summarizing counts, capabilities, and outputs.
    """
    if isinstance(dataset_source, (str, Path)):
        path = Path(dataset_source)
        dataset = OSMDataset.from_json(path.read_text(encoding="utf-8"))
        source_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    elif isinstance(dataset_source, dict):
        dataset = OSMDataset.from_dict(dataset_source)
        source_sha256 = hashlib.sha256(json.dumps(dataset_source, sort_keys=True).encode()).hexdigest()
    elif isinstance(dataset_source, OSMDataset):
        dataset = dataset_source
        source_sha256 = hashlib.sha256(dataset.to_json().encode()).hexdigest()
    else:
        raise TypeError(f"Unsupported dataset source type: {type(dataset_source)}")

    if rules is None:
        rules = POICategoryRule()

    region_id = int(profile.get("region_id", profile.get("route", {}).get("region_id", 0)))
    poi_level = int(profile.get("grid", {}).get("bmd", {}).get("level", 11))
    profile_id = str(profile.get("profile_id", "generic-profile"))

    counts: dict[str, Any] = {
        "nodes_seen": len(dataset.nodes),
        "ways_seen": len(dataset.ways),
        "relations_seen": len(dataset.relations),
        "candidates_evaluated": 0,
        "accepted": 0,
        "rejected": 0,
        "rejected_by_reason": {
            "missing_name": 0,
            "unmapped_category": 0,
            "excluded_category": 0,
            "invalid_coordinates": 0,
            "duplicate_id": 0,
            "duplicate_spatial_name": 0,
        },
        "category_counts": {},
    }

    candidates: list[dict[str, Any]] = []

    poi_keys = {k[0] for k in rules.tag_map.keys()} | {
        "amenity", "shop", "tourism", "leisure", "historic", "office", "craft",
    }

    def is_candidate(tags: dict[str, str]) -> bool:
        return any(k in tags for k in poi_keys)

    # 1. Evaluate Nodes
    for node in dataset.nodes.values():
        if not node.tags or not is_candidate(node.tags):
            continue
        counts["candidates_evaluated"] += 1
        candidates.append({
            "entity_type": "node",
            "entity_id": node.id,
            "tags": dict(node.tags),
            "lon": node.lon,
            "lat": node.lat,
        })

    # 2. Evaluate Ways (Polygon / Footprint POIs)
    for way in dataset.ways.values():
        if not way.tags or not is_candidate(way.tags):
            continue
        counts["candidates_evaluated"] += 1
        # Calculate centroid from coordinates or node references
        coords: list[tuple[float, float]] = []
        if way.coordinates:
            coords = way.coordinates
        else:
            for nid in way.nodes:
                if nid in dataset.nodes:
                    n = dataset.nodes[nid]
                    coords.append((n.lon, n.lat))
        if coords:
            avg_lon = sum(c[0] for c in coords) / len(coords)
            avg_lat = sum(c[1] for c in coords) / len(coords)
        else:
            avg_lon, avg_lat = None, None

        candidates.append({
            "entity_type": "way",
            "entity_id": way.id,
            "tags": dict(way.tags),
            "lon": avg_lon,
            "lat": avg_lat,
        })

    # 3. Evaluate Relations (Multipolygon POIs)
    for rel in dataset.relations.values():
        if not rel.tags or not is_candidate(rel.tags):
            continue
        counts["candidates_evaluated"] += 1
        # Resolve centroid from member nodes or ways
        coords = []
        for m in rel.members:
            if m.type == "node" and m.ref in dataset.nodes:
                n = dataset.nodes[m.ref]
                coords.append((n.lon, n.lat))
            elif m.type == "way" and m.ref in dataset.ways:
                w = dataset.ways[m.ref]
                if w.coordinates:
                    coords.extend(w.coordinates)
                else:
                    for nid in w.nodes:
                        if nid in dataset.nodes:
                            coords.append((dataset.nodes[nid].lon, dataset.nodes[nid].lat))
        if coords:
            avg_lon = sum(c[0] for c in coords) / len(coords)
            avg_lat = sum(c[1] for c in coords) / len(coords)
        else:
            avg_lon, avg_lat = None, None

        candidates.append({
            "entity_type": "relation",
            "entity_id": rel.id,
            "tags": dict(rel.tags),
            "lon": avg_lon,
            "lat": avg_lat,
        })

    # 4. Filter, Match Categories, and Validate
    validated_pois: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for c in candidates:
        tags = c["tags"]
        etype = c["entity_type"]
        eid = c["entity_id"]
        source_ref = f"{etype}:{eid}"

        # Coordinate check
        lon, lat = c["lon"], c["lat"]
        if lon is None or lat is None or not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
            rejected.append({"entity": source_ref, "reason": "invalid_coordinates"})
            counts["rejected"] += 1
            counts["rejected_by_reason"]["invalid_coordinates"] += 1
            continue

        # Name check
        name = tags.get("name") or tags.get("brand") or tags.get("operator")
        if not name or not name.strip():
            rejected.append({"entity": source_ref, "reason": "missing_name"})
            counts["rejected"] += 1
            counts["rejected_by_reason"]["missing_name"] += 1
            continue
        name = name.strip()

        # Category mapping check
        matched_category = None
        matched_nds_id = None
        for (tag_k, tag_v), (cat_name, nds_id) in rules.tag_map.items():
            if tags.get(tag_k) == tag_v:
                matched_category = cat_name
                matched_nds_id = nds_id
                break

        if matched_category is None:
            rejected.append({"entity": source_ref, "reason": "unmapped_category"})
            counts["rejected"] += 1
            counts["rejected_by_reason"]["unmapped_category"] += 1
            continue

        # Include/Exclude category check
        if matched_category in rules.exclude_categories:
            rejected.append({"entity": source_ref, "reason": "excluded_category", "category": matched_category})
            counts["rejected"] += 1
            counts["rejected_by_reason"]["excluded_category"] += 1
            continue
        if rules.include_categories is not None and matched_category not in rules.include_categories:
            rejected.append({"entity": source_ref, "reason": "excluded_category", "category": matched_category})
            counts["rejected"] += 1
            counts["rejected_by_reason"]["excluded_category"] += 1
            continue

        # Tile and Morton index
        tile_id = tile_for(lon, lat, poi_level)
        morton = morton64(
            nds_coordinate(lon) & 0xffffffff,
            nds_coordinate(lat) & 0xffffffff,
        )

        aliases = [tags[k] for k in ("alt_name", "official_name", "loc_name") if k in tags]
        all_aliases = dict.fromkeys([name, *aliases])

        validated_pois.append({
            "id": f"{etype}_{eid}",
            "source_type": etype,
            "source_id": eid,
            "name": name,
            "category": matched_category,
            "nds_category_id": matched_nds_id,
            "longitude": round(lon, 7),
            "latitude": round(lat, 7),
            "tile_id": tile_id,
            "morton": morton,
            "aliases": "\x1c".join(all_aliases),
            "search_tags": strip_accents(name),
            "region_id": region_id,
            "origin": "osm_compiled",
            "raw_tags": dict(sorted(tags.items())),
        })

    # 5. Deduplication
    # Sort order prioritizes ways and relations (polygonal footprints) over single nodes
    type_priority = {"relation": 0, "way": 1, "node": 2}
    validated_pois.sort(key=lambda p: (type_priority.get(p["source_type"], 9), p["id"]))

    accepted_records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for poi in validated_pois:
        poi_id = poi["id"]
        if poi_id in seen_ids:
            rejected.append({"entity": poi_id, "reason": "duplicate_id"})
            counts["rejected"] += 1
            counts["rejected_by_reason"]["duplicate_id"] += 1
            continue

        # Spatial + Name + Category deduplication
        norm_name = strip_accents(poi["name"]).lower()
        is_spatial_dup = False
        for acc in accepted_records:
            if (acc["category"] == poi["category"] and
                    strip_accents(acc["name"]).lower() == norm_name):
                dist = _approx_distance_meters(
                    poi["longitude"], poi["latitude"],
                    acc["longitude"], acc["latitude"],
                )
                if dist <= rules.dedup_distance_meters:
                    is_spatial_dup = True
                    break

        if is_spatial_dup:
            rejected.append({
                "entity": poi_id,
                "reason": "duplicate_spatial_name",
                "name": poi["name"],
                "category": poi["category"],
            })
            counts["rejected"] += 1
            counts["rejected_by_reason"]["duplicate_spatial_name"] += 1
            continue

        seen_ids.add(poi_id)
        accepted_records.append(poi)
        counts["accepted"] += 1
        cat = poi["category"]
        counts["category_counts"][cat] = counts["category_counts"].get(cat, 0) + 1

    # Deterministic sort for accepted records
    accepted_records.sort(key=lambda p: (p["tile_id"], p["morton"], p["id"]))

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "poi-records.json"
    payload = {
        "schema_version": 1,
        "step": "poi.compile",
        "maturity": "experimental",
        "profile_id": profile_id,
        "records": accepted_records,
        "counts": counts,
        "rejected": rejected,
        "source": {"sha256": source_sha256},
        "capabilities_produced": [
            "canonical_osm_poi_records",
            "poi_tile_references",
            "poi_search_index",
        ],
        "capabilities_missing": [
            "Neusoft_POI.NDS",
            "native_search_validation",
        ],
    }
    output_path.write_text(json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    out_bytes = output_path.stat().st_size
    out_sha = hashlib.sha256(output_path.read_bytes()).hexdigest()

    return {
        "counts": counts,
        "outputs": {
            "poi-records.json": {
                "bytes": out_bytes,
                "sha256": out_sha,
            },
        },
        "capabilities_produced": payload["capabilities_produced"],
        "capabilities_missing": payload["capabilities_missing"],
        "maturity": "experimental",
    }


def compile_curated_pois(
    source_path: Path,
    output_dir: Path,
    profile: dict[str, Any],
    category_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and normalize a user-supplied POI FeatureCollection or curated records.

    Maintained for baseline curated compatibility reproduction.
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
            source_records.append({
                "id": props.get("id"),
                "name": props.get("name"),
                "category": props.get("category"),
                "lon": coordinates[0] if len(coordinates) >= 2 else None,
                "lat": coordinates[1] if len(coordinates) >= 2 else None,
                "aliases": props.get("aliases", []),
                "source_feature_id": feature.get("id"),
            })
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
        ident, name, category_key = (
            source_record.get("id"),
            source_record.get("name"),
            source_record.get("category"),
        )
        if not isinstance(ident, (str, int)) or not isinstance(name, str) or not name:
            rejected.append({"feature_id": source_record.get("source_feature_id"), "reason": "missing_id_or_name"})
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
        records.append({
            "id": ident,
            "name": name,
            "category": category_key,
            "nds_category_id": category_map[category_key],
            "longitude": lon,
            "latitude": lat,
            "morton": morton64(
                nds_coordinate(lon) & 0xffffffff,
                nds_coordinate(lat) & 0xffffffff,
            ),
            "aliases": "\x1c".join(dict.fromkeys([name, *aliases])),
            "search_tags": strip_accents(name),
            "region_id": region_id,
            "origin": "curated_external_input",
        })

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "poi-records.json"
    payload = {
        "schema_version": 1,
        "step": "poi.compile",
        "maturity": "experimental",
        "profile_id": profile.get("profile_id"),
        "records": records,
        "counts": {
            "input": len(source_records),
            "accepted": len(records),
            "rejected": len(rejected),
        },
        "rejected": rejected,
        "source": {"sha256": hashlib.sha256(source_path.read_bytes()).hexdigest()},
        "capabilities_produced": ["canonical_curated_poi_records"],
        "capabilities_missing": [
            "Neusoft_POI.NDS",
            "complete_osm_poi_coverage",
            "native_search_validation",
        ],
    }
    output_path.write_text(json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "counts": payload["counts"],
        "outputs": {
            "poi-records.json": {
                "bytes": output_path.stat().st_size,
                "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
            },
        },
        "capabilities_produced": payload["capabilities_produced"],
        "capabilities_missing": payload["capabilities_missing"],
        "maturity": "experimental",
    }
