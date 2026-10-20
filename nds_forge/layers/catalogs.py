"""Deterministic v9 layer-coverage catalog derivation.

This builds only the tile/index plan consumed by the package assembler. It does
not claim that catalog or product identifiers generalize beyond the explicit
Curitiba v9 compatibility profile.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .bmd_codec import tile_for, tile_origin
from ..registry import EXPERIMENTAL, Step, StepContext, replace_step


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_catalogs(bmd_dir: Path, names_dir: Path, poi_dir: Path,
                   route_dir: Path, aux_dir: Path, output_dir: Path,
                   profile: dict[str, Any]) -> dict[str, Any]:
    if profile.get("profile_id") != "neusoft-curitiba-v9":
        raise ValueError("Catalog writer is explicitly scoped to neusoft-curitiba-v9")
    bmd = _read(bmd_dir / "bmd-manifest.json")
    names = _read(names_dir / "name-records.json")
    poi = _read(poi_dir / "poi-records.json")
    route = _read(route_dir / "route-manifest.json")
    aux = _read(aux_dir / "aux-manifest.json")
    # The legacy v9 ROUTE manifest predates `profile_id`; bind it by the
    # region/source/grid values it does record, while AUX carries an explicit ID.
    source_sha = profile.get("source_sha256")
    if aux.get("profile_id") != profile["profile_id"]:
        raise ValueError("AUX profile mismatch")
    if (int(route.get("region_id", -1)) != int(profile["route"]["region_id"])
            or route.get("source_sha256") != source_sha):
        raise ValueError("ROUTE source or region does not match the selected profile")
    if route.get("level") != 13 or route.get("shift") != 4:
        raise ValueError("Curitiba v9 catalog evidence covers route level 13 / shift 4")
    bmd_tiles = sorted(int(item["tile_id"]) for item in bmd.get("tiles", []))
    route_tiles = sorted(int(item["id"]) for item in route.get("tiles", []))
    if not bmd_tiles or not route_tiles or len(set(route_tiles)) != len(route_tiles):
        raise ValueError("BMD/ROUTE manifests require non-empty unique tile lists")
    if set(route_tiles) != {int(item["tile_id"]) for item in aux.get("tiles", [])}:
        raise ValueError("AUX tile coverage must exactly match ROUTE tile coverage")
    bmd_level = int(profile["grid"]["bmd"]["level"])
    bmd_shift = int(profile["grid"]["bmd"]["shift"])
    route_index_level = int(profile["index_levels"]["route"])
    route_index_tiles = set()
    for tile_id in route_tiles:
        x, y = tile_origin(tile_id, int(route["level"]))
        lon, lat = x * 180 / (1 << 31), y * 180 / (1 << 31)
        route_index_tiles.add(int(tile_for(lon, lat, route_index_level)))
    document = {
        "schema_version": 1,
        "step": "catalogs.build",
        "maturity": "experimental",
        "validation_status": "unvalidated",
        "profile_id": profile["profile_id"],
        "region_id": int(profile["route"]["region_id"]),
        "layer_coverage": {
            "bmd": {"building_block_id": 1, "level": bmd_level, "shift": bmd_shift,
                    "tile_ids": bmd_tiles},
            "poi": {"building_block_id": 4, "level": bmd_level, "tile_ids": bmd_tiles,
                    "record_count": int(poi["counts"]["accepted"])},
            "route": {"building_block_id": 2, "payload_level": int(route["level"]),
                      "payload_shift": int(route["shift"]), "index_level": route_index_level,
                      "index_tile_ids": sorted(route_index_tiles), "tile_ids": route_tiles,
                      "link_count": int(route["links"]),
                      "intersection_count": int(route["intersections"]),
                      "aux_geometry_count": int(aux["counts"]["geometry_rows"])},
        },
        "counts": {"bmd_tiles": len(bmd_tiles), "route_tiles": len(route_tiles),
                   "route_index_tiles": len(route_index_tiles),
                   "street_records": int(names["counts"]["street_records"]),
                   "admin_records": int(names["counts"]["admin_records"]),
                   "poi_records": int(poi["counts"]["accepted"])},
        "source_artifacts": {
            "bmd_manifest_sha256": _sha(bmd_dir / "bmd-manifest.json"),
            "names_sha256": _sha(names_dir / "name-records.json"),
            "poi_sha256": _sha(poi_dir / "poi-records.json"),
            "route_manifest_sha256": _sha(route_dir / "route-manifest.json"),
            "aux_manifest_sha256": _sha(aux_dir / "aux-manifest.json"),
        },
        "capabilities_produced": ["v9_profile_tile_coverage_plan"],
        "capabilities_missing": ["national_catalog_ids", "address_coverage",
                                 "Brazilian_TMC_mapping", "native_or_device_validation"],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "catalogs.json"
    out_path.write_text(json.dumps(document, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return {"outputs": {"catalogs.json": {"bytes": out_path.stat().st_size,
                                           "sha256": _sha(out_path)}},
            "counts": document["counts"], "maturity": "experimental",
            "validation_status": "unvalidated",
            "capabilities_produced": document["capabilities_produced"],
            "capabilities_missing": document["capabilities_missing"]}


def _execute(context: StepContext) -> dict[str, Any]:
    config = context.config.neusoft
    return build_catalogs(
        context.dependency_outputs["geometry.bmd"],
        context.dependency_outputs["names.compile"],
        context.dependency_outputs["poi.compile"],
        context.dependency_outputs["routing.main"],
        context.dependency_outputs["routing.aux"],
        context.output_dir,
        {**config, "profile_id": config.get("profile_id"),
         "source_sha256": context.config.source.get("sha256")},
    )


def register_catalog_step() -> None:
    replace_step(Step(
        "catalogs.build", EXPERIMENTAL,
        ("geometry.bmd", "names.compile", "poi.compile", "routing.main", "routing.aux"),
        "catalogs", "Build coverage indices for the explicit Curitiba v9 profile",
        inputs=("geometry.bmd/bmd-manifest.json", "names.compile/name-records.json",
                "poi.compile/poi-records.json", "routing.main/route-manifest.json",
                "routing.aux/aux-manifest.json", "neusoft.grid", "neusoft.index_levels"),
        outputs=("catalogs.json",), capabilities=("profile_tile_coverage_plan",),
    ), _execute)
