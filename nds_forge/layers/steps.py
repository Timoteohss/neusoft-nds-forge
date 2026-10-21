"""Profile-aware layer step declarations and executor bindings.

The core calls ``register_layer_steps`` after declaring its built-in IDs. This
module owns only compiler adapters; pipeline orchestration remains in core.
"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

from ..registry import EXPERIMENTAL, Step, StepContext, replace_step
from .bmd import compile_bmd_from_clip
from .container import pack_package_tree
from .names import compile_name_records
from .poi import POICategoryRule, compile_curated_pois, compile_osm_pois
from .route_graph import build as build_route_graph
from .route_aux import compile_route_aux


def _profile(config: Any, key: str) -> dict:
    if key == "bmd":
        grid = config.neusoft.get("grid", {})
        spatial = grid.get("bmd") if isinstance(grid, dict) else None
        roads = config.neusoft.get("road_policy")
        if isinstance(spatial, dict) and isinstance(roads, dict):
            return {**spatial, **roads, "profile_id": config.neusoft.get("profile_id")}
    value = config.neusoft.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"neusoft.{key} profile block is required")
    return {**value, "profile_id": config.neusoft.get("profile_id")}


def _execute_bmd(context: StepContext) -> dict:
    return compile_bmd_from_clip(context.dependency_outputs["osm.clip"], context.output_dir,
                                 _profile(context.config, "bmd"))


def _execute_names(context: StepContext) -> dict:
    return compile_name_records(
        context.dependency_outputs["osm.clip"] / "roads.geojson",
        context.dependency_outputs["geometry.bmd"], context.output_dir,
        _profile(context.config, "names"))


def validate_poi_profile(profile: dict) -> None:
    source = profile.get("source", "profile_curated")
    if source not in {"profile_curated", "osm"}:
        raise ValueError("neusoft.poi.source must be profile_curated or osm")
    if source == "profile_curated":
        if not profile.get("curated_poi_file"):
            raise ValueError("Curated POI mode requires neusoft.poi.curated_poi_file")
        return
    mapping = profile.get("osm_tag_categories")
    category_ids = profile.get("poi_table_category_ids", {})
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("OSM POI mode requires explicit osm_tag_categories; native categories are not inferred")
    for tag, category in mapping.items():
        if (not isinstance(tag, str) or "=" not in tag or
                not all(tag.split("=", 1)) or not isinstance(category, str) or
                category not in category_ids or
                not isinstance(category_ids[category], int) or category_ids[category] < 0):
            raise ValueError("OSM POI mappings must use key=value and a category declared in poi_table_category_ids")


def _execute_poi(context: StepContext) -> dict:
    profile = _profile(context.config, "poi")
    validate_poi_profile(profile)
    if profile.get("source") == "osm":
        # Never use the speculative category defaults of the standalone model
        # compiler for an app package. Each mapping must be supplied explicitly.
        mapping = profile["osm_tag_categories"]
        rules = POICategoryRule(
            tag_map={tuple(tag.split("=", 1)): (category, profile["poi_table_category_ids"][category])
                     for tag, category in mapping.items()},
            dedup_distance_meters=float(profile.get("dedup_distance_meters", 50.0)))
        report = compile_osm_pois(
            context.dependency_outputs["osm.clip"] / "osm-dataset.json", context.output_dir,
            {**profile, "grid": context.config.neusoft.get("grid", {})}, rules)
        path = context.output_dir / "poi-records.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        # Source identities remain preserved; SQL/NDS joins require numeric IDs.
        for index, row in enumerate(sorted(document["records"], key=lambda item: item["id"]), 1):
            row["source_ref"] = row["id"]
            row["id"] = str(index)
        document["source"]["category_mapping"] = mapping
        dataset = json.loads((context.dependency_outputs["osm.clip"] / "osm-dataset.json").read_text())
        document["source"]["osm_provenance"] = dataset["provenance"]
        document["region"] = dataset.get("clip_lineage", {})
        document["limitations"] = [
            "POI ways use the vertex-mean representative point; this is not a polygon centroid.",
            "Complete multipolygon relation geometry and native POI acceptance remain unvalidated."]
        path.write_text(json.dumps(document, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        report["outputs"]["poi-records.json"] = {
            "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        return report
    input_path = context.external_inputs.get("neusoft.poi.curated_poi_file")
    if input_path is None:
        raise ValueError("neusoft.poi.curated_poi_file must be a declared external input")
    return compile_curated_pois(input_path, context.output_dir,
                                profile)


def _route_profile(config: Any) -> dict:
    route = config.neusoft.get("route")
    grid = config.neusoft.get("grid")
    if not isinstance(route, dict) or not isinstance(grid, dict):
        raise ValueError("neusoft.route and neusoft.grid profile blocks are required")
    profile = {**route, "profile_id": config.neusoft.get("profile_id"),
               "level": grid.get("route_level"), "shift": grid.get("route_shift")}
    required = ("profile_id", "region_id", "level", "shift", "simplify_tolerance_degrees",
                "driveable_highways", "denied_access_values", "frc_by_highway", "fallback_speed_kmh")
    missing = [key for key in required if profile.get(key) is None]
    if missing:
        raise ValueError(f"Route profile lacks explicit fields: {', '.join(missing)}")
    return profile


def _execute_route_main(context: StepContext) -> dict:
    source = json.loads((context.dependency_outputs["source.acquire"] / "source.json").read_text())
    region = json.loads((context.dependency_outputs["region.resolve"] / "region.json").read_text())
    if region.get("resolution"):
        raise ValueError("Route compilation needs a resolved local polygon/bbox")
    geometry = region.get("geometry_geojson")
    if geometry is None:
        bbox = region.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError("Route compilation needs a resolved Polygon or bbox")
        west, south, east, north = map(float, bbox)
        geometry = {"type": "Polygon", "coordinates": [[[west, south], [east, south],
                      [east, north], [west, north], [west, south]]]}
    profile = _route_profile(context.config)
    return build_route_graph(Path(source["path"]), geometry, context.output_dir,
                             level=profile["level"], shift=profile["shift"],
                             simplify_tolerance_degrees=profile["simplify_tolerance_degrees"],
                             policy=profile, region_id=profile["region_id"],
                             source_metadata={"sha256": source["sha256"],
                                              "license": source.get("license"),
                                              "attribution": source.get("attribution")})


def _execute_route_aux(context: StepContext) -> dict:
    return compile_route_aux(context.dependency_outputs["routing.main"],
                             context.output_dir, _route_profile(context.config))


def _execute_container_pack(context: StepContext) -> dict:
    key_references = context.config.neusoft.get("container_keys")
    if not isinstance(key_references, dict):
        raise ValueError("Named per-output neusoft.container_keys references are required")
    resolver = context.resolve_secret
    if resolver is None:
        raise RuntimeError("No external key-provider resolver is available in this runtime")
    keys = {}
    for output_name, reference in key_references.items():
        if not isinstance(output_name, str) or not isinstance(reference, str) or not reference:
            raise ValueError("container_keys must map output names to named secret references")
        keys[output_name] = resolver(reference)
    return pack_package_tree(context.dependency_outputs["package.assemble"],
                             context.output_dir, keys)


def register_layer_steps() -> None:
    """Replace supported built-in planned stages with experimental adapters."""
    replace_step(Step(
        "geometry.bmd", EXPERIMENTAL, ("osm.clip",), "geometry-bmd",
        "Compile experimental BMD geometry blobs from clipped OSM roads",
        inputs=("osm.clip/roads.geojson", "osm.clip/clip-report.json", "neusoft.bmd"),
        outputs=("tiles", "street_names.json", "bmd-manifest.json"),
        capabilities=("bmd_tile_geometry",),
    ), _execute_bmd)
    replace_step(Step(
        "names.compile", EXPERIMENTAL, ("osm.clip", "geometry.bmd"), "name-records",
        "Produce canonical NAME/FTS intermediate records using explicit locale profile",
        inputs=("osm.clip/roads.geojson", "geometry.bmd/street_names.json", "neusoft.names"),
        outputs=("name-records.json",), capabilities=("canonical_name_records", "canonical_search_records"),
    ), _execute_names)
    replace_step(Step(
        "poi.compile", EXPERIMENTAL, ("osm.clip",), "poi-records",
        "Compile explicitly selected OSM or curated POI records",
        inputs=("neusoft.poi.curated_poi_file", "neusoft.poi.poi_categories", "neusoft.poi"),
        outputs=("poi-records.json",),
        capabilities=("canonical_poi_records",),
        optional_external_inputs=("neusoft.poi.curated_poi_file",),
    ), _execute_poi)
    replace_step(Step(
        "routing.main", EXPERIMENTAL, ("source.acquire", "region.resolve"), "routing-main",
        "Compile the explicit Curitiba v9 OSM route graph and preserve a canonical AUX model",
        inputs=("source.acquire/source.json", "region.resolve/region.json", "neusoft.route", "neusoft.grid"),
        outputs=("route-manifest.json", "route-model.json", "<tile>.route.bin"),
        capabilities=("main_route_graph", "cross_tile_link_references"),
    ), _execute_route_main)
    replace_step(Step(
        "routing.aux", EXPERIMENTAL, ("routing.main",), "routing-aux",
        "Compile sparse v9 AUX geometry rows while preserving main link ordinals",
        inputs=("routing.main/route-model.json", "neusoft.route", "neusoft.grid"),
        outputs=("aux-manifest.json", "<tile>.route-aux.bin"),
        capabilities=("sparse_aux_geometry", "u16_row_offset_guard"),
    ), _execute_route_aux)
    replace_step(Step(
        "container.pack", EXPERIMENTAL, ("package.assemble",), "package-final",
        "Pack generated SQLite databases as ZV-zlib and preserve allowlisted wrapped assets",
        inputs=("package.assemble/package-layout.json", "neusoft.container_keys"),
        outputs=("db/", "artifact-manifest.json"),
        capabilities=("app_readable_container",), cacheable=False,
    ), _execute_container_pack)
