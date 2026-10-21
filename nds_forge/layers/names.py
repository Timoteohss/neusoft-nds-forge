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
    if scope not in ("municipality", "state"):
        raise ValueError("admin_scope must be municipality or state")
    hierarchy = profile.get("admin_hierarchy", [])
    if not isinstance(hierarchy, list):
        raise ValueError("admin_hierarchy must be a list")  # noqa: TRY004 -- ValueError is the established validation exception; changing its class changes caller-visible behavior
    if scope == "state":
        if any(not isinstance(item, dict) for item in hierarchy):
            raise ValueError("Each state hierarchy item must be an administrative record")
        if [item.get("class") for item in hierarchy] != [0, 2]:
            raise ValueError("State-only hierarchy must contain country/state classes 0 and 2 only")
        if any(profile.get(f"criterion_{letter}") for letter in "cdj"):
            raise ValueError("State-only hierarchy cannot supply municipality/postcode criteria C, D or J")
        if len(profile.get("admin_substitutions", [])) != 2:
            raise ValueError("State-only hierarchy requires country/state substitutions only")
    return scope


def compile_name_records(roads_path: Path, bmd_dir: Path, output_dir: Path,
                         profile: dict[str, Any]) -> dict[str, Any]:
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
                        "origin": "osm_way_names", "point_count_for_centroid": len(points)})

    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "step": "names.compile", "maturity": "experimental",
               "profile_id": profile.get("profile_id"), "admin_scope": scope,
               "records": admin_records + streets,
               "counts": {"admin_records": len(admin_records), "street_records": len(streets)},
               "capabilities_produced": ["canonical_name_records", "canonical_search_records"],
               "capabilities_missing": ["Neusoft_NAME.NDS", "Neusoft_FTS.NDS",
                                        "house_number_tables", "native_search_validation"],
               "limitations": ["Output records are intermediates, not Neusoft databases."] + (
                   [("State-only fallback: streets have no municipality or postcode assignment; "
                    "municipal spatial joins and device search validation are still pending.")]
                   if scope == "state" else [])}
    output_path = output_dir / "name-records.json"
    output_path.write_text(json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return {"counts": payload["counts"], "outputs": {"name-records.json": {
        "bytes": output_path.stat().st_size,
        "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest()}},
        "capabilities_produced": payload["capabilities_produced"],
        "capabilities_missing": payload["capabilities_missing"], "maturity": "experimental"}
