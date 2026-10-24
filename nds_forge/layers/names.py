"""Deterministic intermediate NAME/FTS record compiler.

It deliberately stops before Neusoft SQLite schema insertion. City hierarchy,
locale aliases and name-class IDs are explicit profile data rather than
implicit Curitiba defaults.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path
from typing import Any

from .bmd_codec import nds_coordinate


def morton64(x: int, y: int) -> int:
    value = 0
    for bit in range(32):
        value |= ((x >> bit) & 1) << (2 * bit)
        value |= ((y >> bit) & 1) << (2 * bit + 1)
    return value - (1 << 64) if value >= (1 << 63) else value


def strip_accents(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", value)
                   if unicodedata.category(c) != "Mn")


def _coords(geometry: dict[str, Any]):
    kind = geometry.get("type")
    value = geometry.get("coordinates", [])
    if kind == "LineString":
        yield from value
    elif kind == "MultiLineString":
        for line in value:
            yield from line
    else:
        raise ValueError(f"Unsupported road geometry for names: {kind!r}")


def _aliases(name: str, policy: dict[str, Any]) -> str:
    aliases = [name]
    if policy.get("include_unaccented", False):
        base = strip_accents(name)
        if base != name:
            aliases.append(base)
    for prefix in policy.get("prefixes", []):
        if name.startswith(prefix):
            short = name[len(prefix):]
            aliases.append(short)
            if policy.get("include_unaccented", False):
                aliases.append(strip_accents(short))
            break
    for original, replacement in policy.get("literal_replacements", {}).items():
        if original in name:
            aliases.append(name.replace(original, replacement))
    unique = list(dict.fromkeys(a for a in aliases if a))
    return "\x1c".join(unique)


def validate_admin_scope(profile: dict[str, Any]) -> str:
    """Keep the state-only fallback from inventing one city/postcode for all roads.

    Municipal profiles retain the four-record v9 contract. State profiles omit
    city classes and city/postcode FTS criteria until spatial assignment exists.
    """
    scope = profile.get("admin_scope", "municipality")
    if scope not in ("municipality", "state", "state_municipalities"):
        raise ValueError("admin_scope must be municipality, state or state_municipalities")
    hierarchy = profile.get("admin_hierarchy", [])
    if not isinstance(hierarchy, list):
        raise ValueError("admin_hierarchy must be a list")  # noqa: TRY004 -- ValueError is the established validation exception; changing its class changes caller-visible behavior
    if scope in {"state", "state_municipalities"}:
        if any(not isinstance(item, dict) for item in hierarchy):
            raise ValueError("Each state hierarchy item must be an administrative record")
        if [item.get("class") for item in hierarchy] != [0, 2]:
            raise ValueError("State-only hierarchy must contain country/state classes 0 and 2 only")
        if any(profile.get(f"criterion_{letter}") for letter in "cdj"):
            raise ValueError("State-only hierarchy cannot supply municipality/postcode criteria C, D or J")
        if len(profile.get("admin_substitutions", [])) != 2:
            raise ValueError("State-only hierarchy requires country/state substitutions only")
    return scope


def _load_municipalities(path: Path) -> tuple[list[dict[str, Any]], Any]:
    try:
        from shapely import STRtree
        from shapely.geometry import Point, shape
    except ImportError as exc:
        raise ValueError("Municipality assignment requires the optional shapely dependency") from exc
    document = json.loads(path.read_text(encoding="utf-8"))
    records = document.get("records")
    if document.get("schema_version") != 1 or not isinstance(records, list) or not records:
        raise ValueError("municipalities.json must contain schema_version=1 and records[]")
    records = sorted(records, key=lambda item: str(item.get("ibge_code", "")))
    geometries = []
    for item in records:
        if not isinstance(item, dict) or not isinstance(item.get("geometry"), dict):
            raise ValueError("Each municipality needs a GeoJSON geometry")  # noqa: TRY004 -- JSON shape validation shares the compiler's ValueError contract
        geometry = shape(item["geometry"])
        if geometry.is_empty or not geometry.is_valid:
            raise ValueError(f"Invalid municipality geometry: {item.get('name', item.get('id'))}")
        geometries.append(geometry)
    tree = STRtree(geometries)
    for index, item in enumerate(records):
        item["_geometry"] = geometries[index]
        item["criterion_c"] = chr(0xE2000 + index)
        item["criterion_d"] = str(item["name"])
        postal = str(item.get("postal_code", "")).strip()
        item["criterion_j"] = postal.split("-", 1)[0] if postal else ""
        item["_point"] = Point(float(item["longitude"]), float(item["latitude"]))
    return records, tree


def _assign_municipalities(streets: list[dict[str, Any]], records: list[dict[str, Any]],
                           tree: Any) -> tuple[int, int]:
    assigned = 0
    for street in streets:
        point = street.pop("_point", None)
        if point is None:
            continue
        matches = tree.query(point)
        municipality = None
        for index in matches:
            candidate = records[int(index)]
            if candidate["_geometry"].covers(point):
                municipality = candidate
                break
        if municipality is None:
            continue
        street["criterion_c"] = municipality["criterion_c"]
        street["criterion_d"] = municipality["criterion_d"]
        street["criterion_j"] = municipality["criterion_j"]
        street["municipality_id"] = int(municipality["id"])
        street["ibge_code"] = str(municipality["ibge_code"])
        street["municipality_name"] = str(municipality["name"])
        assigned += 1
    return assigned, len(streets) - assigned


def compile_name_records(roads_path: Path, bmd_dir: Path, output_dir: Path,
                         profile: dict[str, Any], municipalities_path: Path | None = None) -> dict[str, Any]:
    """Produce canonical name and search records from clipped ways.

    Requires explicit `admin_hierarchy`, `alias_policy`, `street_class` and
    `region_id` in the selected profile. It does not claim FTS/NAME SQLite
    compatibility until the package writer inserts these records into a
    verified schema seed and a native/UI search query is validated.
    """
    required = ("admin_hierarchy", "alias_policy", "street_class", "region_id")
    missing = [field for field in required if field not in profile]
    if missing:
        raise ValueError(f"Name profile lacks required fields: {', '.join(missing)}")
    scope = validate_admin_scope(profile)
    names_file = bmd_dir / "street_names.json"
    if not names_file.is_file() or not roads_path.is_file():
        raise FileNotFoundError("Expected BMD street_names.json and roads.geojson inputs")
    name_map = json.loads(names_file.read_text(encoding="utf-8"))
    name_to_id = {str(name): int(ident) for name, ident in name_map.items()}
    document = json.loads(roads_path.read_text(encoding="utf-8"))
    coords_by_name: dict[str, list[list[float]]] = {}
    for feature in document.get("features", []):
        props = feature.get("properties") or {}
        name = props.get("name")
        if name is not None:
            if name not in name_to_id:
                raise ValueError(f"Named road {name!r} has no stable BMD name ID")
            coords_by_name.setdefault(name, []).extend(_coords(feature.get("geometry") or {}))

    hierarchy = profile["admin_hierarchy"]
    if not isinstance(hierarchy, list):
        raise ValueError("admin_hierarchy must be a list")  # noqa: TRY004 -- ValueError is the established validation exception; changing its class changes caller-visible behavior
    admin_records = []
    for item in hierarchy:
        if not isinstance(item, dict) or not {"id", "name", "class"} <= item.keys():
            raise ValueError("Each admin hierarchy record needs id, name and class")
        if "morton" not in item:
            raise ValueError("Each admin record needs an explicit profile Morton code")
        admin_records.append({"id": int(item["id"]), "name": str(item["name"]),
                              "class": int(item["class"]), "morton": int(item["morton"]),
                              "region_id": int(profile["region_id"]),
                              "origin": "profile_admin_hierarchy"})

    municipalities: list[dict[str, Any]] = []
    municipality_tree = None
    if municipalities_path is not None:
        municipalities, municipality_tree = _load_municipalities(municipalities_path)
    elif scope == "state_municipalities":
        raise ValueError("admin_scope=state_municipalities requires a municipalities.json input")
    Point: Any = None
    if municipalities:
        try:
            from shapely.geometry import Point
        except ImportError as exc:
            raise ValueError("Municipality assignment requires the optional shapely dependency") from exc

    city_admin_records: list[dict[str, Any]] = []
    substitutions: list[dict[str, Any]] = []
    for index, municipality in enumerate(municipalities):
        point = municipality["_point"]
        mcode = morton64(nds_coordinate(point.x) & 0xffffffff,
                         nds_coordinate(point.y) & 0xffffffff)
        criterion_a = profile.get("criterion_a", "")
        criterion_b = profile.get("criterion_b", "")
        criterion_c = municipality["criterion_c"]
        criterion_d = municipality["criterion_d"]
        base_id = 900000 + index * 2
        city_admin_records.append({
            "id": base_id, "name": municipality["name"], "class": 9, "morton": mcode,
            "region_id": int(profile["region_id"]), "origin": "osm_admin_boundary",
            "criterion_a": criterion_a, "criterion_b": criterion_b,
            "criterion_c": criterion_c, "ibge_code": municipality["ibge_code"],
        })
        city_admin_records.append({
            "id": base_id + 1, "name": municipality["name"], "class": 10, "morton": mcode,
            "region_id": int(profile["region_id"]), "origin": "osm_admin_boundary",
            "criterion_a": criterion_a, "criterion_b": criterion_b,
            "criterion_c": criterion_c, "criterion_d": criterion_d,
            "ibge_code": municipality["ibge_code"],
        })
        substitutions.append({
            "code": criterion_c, "display": criterion_d,
            "priority": 100, "frequency": 100,
        })
    admin_records.extend(city_admin_records)

    streets = []
    for name, ident in sorted(name_to_id.items(), key=lambda pair: pair[1]):
        points = coords_by_name.get(name)
        if not points:
            raise ValueError(f"Name ID {ident} has no clipped geometry: {name!r}")
        lon = sum(float(point[0]) for point in points) / len(points)
        lat = sum(float(point[1]) for point in points) / len(points)
        mcode = morton64(nds_coordinate(lon) & 0xffffffff,
                         nds_coordinate(lat) & 0xffffffff)
        streets.append({"id": ident, "name": name, "class": int(profile["street_class"]),
                        "morton": mcode, "aliases": _aliases(name, profile["alias_policy"]),
                        "search_tags": strip_accents(name), "region_id": int(profile["region_id"]),
                        "origin": "osm_way_names", "point_count_for_centroid": len(points),
                        "_point": Point(lon, lat) if Point is not None else None})

    assigned_streets = 0
    if municipalities and municipality_tree is not None:
        assigned_streets, _ = _assign_municipalities(
            streets, municipalities, municipality_tree)
    for street in streets:
        street.pop("_point", None)

    output_dir.mkdir(parents=True, exist_ok=True)
    limitations = ["Output records are intermediates, not Neusoft databases."]
    if scope == "state":
        limitations.append(
            "State-only fallback: streets have no municipality or postcode assignment; "
            "municipal spatial joins and device search validation are still pending.")
    if municipalities:
        limitations.append(
            "Municipality assignment uses the street centroid and OSM administrative boundaries; "
            "native/UI search acceptance remains unvalidated.")
    payload = {"schema_version": 1, "step": "names.compile", "maturity": "experimental",
               "profile_id": profile.get("profile_id"), "admin_scope": scope,
               "records": admin_records + streets,
               "substitutions": substitutions,
               "counts": {"admin_records": len(admin_records), "street_records": len(streets),
                          "municipality_records": len(municipalities),
                          "assigned_streets": assigned_streets,
                          "unassigned_streets": len(streets) - assigned_streets},
               "capabilities_produced": ["canonical_name_records", "canonical_search_records"],
               "capabilities_missing": ["Neusoft_NAME.NDS", "Neusoft_FTS.NDS",
                                        "house_number_tables", "native_search_validation"],
               "limitations": limitations}
    output_path = output_dir / "name-records.json"
    output_path.write_text(json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return {"counts": payload["counts"], "outputs": {"name-records.json": {
        "bytes": output_path.stat().st_size,
        "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest()}},
        "capabilities_produced": payload["capabilities_produced"],
        "capabilities_missing": payload["capabilities_missing"], "maturity": "experimental"}
