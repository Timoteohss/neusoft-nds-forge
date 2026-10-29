"""Experimental sparse ROUTE-AUX writer for the validated v9 profile."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .route_attribute_compiler import (
    STREET_NAME_ATTRIBUTE,
    compile_link_names,
    compile_link_numbers,
    mirror_aux_house_numbers,
)
from .route_attribute_layer import write_attribute_layer
from .route_codec import encode_aux_sparse, parse_aux_geometries


def compile_route_aux(route_main_dir: Path, output_dir: Path,
                      profile: dict[str, Any],
                      address_dir: Path | None = None,
                      roads_dir: Path | None = None,
                      names_dir: Path | None = None) -> dict[str, Any]:
    """Write boundary-adjacent and bent-link shapes, retaining map markers.

    This is pinned to `neusoft-curitiba-v9`; the sparse policy is backed by
    local native+SDK validation for its 103-tile municipal package. It is not
    asserted as a universal fallback rule for other profiles.
    """
    # Brazil/Paraná uses the same reviewed sparse-AUX policy under its own
    # declared local identity.
    if profile.get("profile_id") not in ("neusoft-curitiba-v9", "neusoft-brazil-parana"):
        raise ValueError("Sparse AUX policy is verified only for a reviewed v9 policy")
    level, shift = int(profile["level"]), int(profile["shift"])
    if (level, shift) != (13, 4):
        raise ValueError("The validated v9 AUX policy requires route level 13 / shift 4")
    model_path = route_main_dir / "route-model.json"
    main_manifest_path = route_main_dir / "route-manifest.json"
    if not model_path.is_file() or not main_manifest_path.is_file():
        raise FileNotFoundError("routing.main must provide route-model.json and route-manifest.json")
    model = json.loads(model_path.read_text(encoding="utf-8"))
    main_manifest = json.loads(main_manifest_path.read_text(encoding="utf-8"))
    if model.get("schema_version") != 1 or model.get("profile_id") != profile["profile_id"]:
        raise ValueError("ROUTE model schema/profile mismatch")
    tiles = model.get("tiles")
    if not isinstance(tiles, list) or not tiles:
        raise ValueError("ROUTE model has no tile records")
    if len(tiles) != len(main_manifest.get("tiles", [])):
        raise ValueError("ROUTE model and main manifest tile counts differ")
    output_dir.mkdir(parents=True, exist_ok=True)
    attribute_models: dict[int, dict[str, Any]] = {}
    if address_dir is not None:
        address_doc = json.loads(
            (address_dir / "address-doc.json").read_text(encoding="utf-8"))
        attribute_models = compile_link_numbers(model, address_doc)
    if (roads_dir is None) != (names_dir is None):
        raise ValueError("Street-name attributes require both roads_dir and names_dir")
    name_inputs: dict[str, str] = {}
    if roads_dir is not None and names_dir is not None:
        roads_path, names_path = roads_dir / "roads.geojson", names_dir / "name-records.json"
        aux_link_ordinals = {
                int(t["tile_id"]): ([i for i, link in enumerate(t["links"])
                                     if link.get("retain_aux_geometry") is True]
                                    or ([0] if profile.get("empty_aux_policy") == "retain_first_experimental" else []))
                for t in tiles}
        name_models = compile_link_names(
            model, json.loads(roads_path.read_text(encoding="utf-8")),
            json.loads(names_path.read_text(encoding="utf-8")), aux_link_ordinals)
        for tile_id, name_model in name_models.items():
            attribute_models.setdefault(tile_id, {"extension": 0, "maps": []})["maps"].extend(
                name_model["maps"])
        mirror_aux_house_numbers(attribute_models, aux_link_ordinals)
        name_inputs = {"roads_sha256": hashlib.sha256(roads_path.read_bytes()).hexdigest(),
                       "name_records_sha256": hashlib.sha256(names_path.read_bytes()).hexdigest()}
    records: list[dict[str, Any]] = []
    total_links = total_rows = 0
    max_bytes = max_bits = 0
    fallback_tiles = []
    empty_policy = profile.get("empty_aux_policy", "reject")
    if empty_policy not in {"reject", "retain_first_experimental"}:
        raise ValueError("empty_aux_policy must be reject or retain_first_experimental")
    for tile in tiles:
        tile_id = int(tile["tile_id"])
        links = tile.get("links")
        if not isinstance(links, list) or not links:
            raise ValueError(f"No main links for tile {tile_id}")
        retained = {index for index, link in enumerate(links)
                    if link.get("retain_aux_geometry") is True}
        if not retained:
            if empty_policy == "reject":
                raise ValueError(f"Tile {tile_id} has no AUX geometry selected by the validated policy; "
                                 "empty_aux_policy=retain_first_experimental requires explicit opt-in")
            retained = {0}
            fallback_tiles.append(tile_id)
        blob, stats = encode_aux_sparse(tile_id, level, shift, links, retained,
                                        guard_u16_offsets=True)
        rows = parse_aux_geometries(blob, tile_id, level, shift)
        if len(rows) != len(retained) or {row["link"] for row in rows} != retained:
            raise ValueError(f"AUX retained-link roundtrip failed for tile {tile_id}")
        path = output_dir / f"{tile_id}.route-aux.bin"
        path.write_bytes(blob)
        digest = hashlib.sha256(blob).hexdigest()
        attribute_record: dict[str, Any] | None = None
        model = attribute_models.get(tile_id)
        if model is not None:
            layer_blob = write_attribute_layer(model)
            layer_path = output_dir / f"{tile_id}.route-attrs.bin"
            layer_path.write_bytes(layer_blob)
            attribute_record = {
                "path": layer_path.name, "bytes": len(layer_blob),
                "sha256": hashlib.sha256(layer_blob).hexdigest(),
                "link_ranges": sum(len(m["container"]["groups"]) for m in model["maps"]
                                   if 0xB6 in m["attribute_ids"] and m["reference_type"] == 2),
                "link_names": sum(len(m["container"]["groups"]) for m in model["maps"]
                                  if STREET_NAME_ATTRIBUTE in m["attribute_ids"] and m["reference_type"] == 2),
                "aux_row_names": sum(len(m["container"]["groups"]) for m in model["maps"]
                                     if STREET_NAME_ATTRIBUTE in m["attribute_ids"] and m["reference_type"] == 4),
            }
        records.append({"tile_id": tile_id, "path": path.name, "bytes": len(blob),
                        "sha256": digest, "attribute_layer": attribute_record, **stats,
                        "boundary_rows": sum(1 for i in retained if links[i]["boundary_adjacent"]),
                        "curved_rows": sum(1 for i in retained if links[i]["curved"])})
        total_links += len(links)
        total_rows += len(retained)
        max_bytes = max(max_bytes, len(blob))
        max_bits = max(max_bits, stats["max_row_relative_bit_offset"])
    report = {"schema_version": 1, "step": "routing.aux", "maturity": "experimental",
              "validation_status": "unvalidated",
              "profile_id": profile["profile_id"], "policy": "boundary-adjacent plus curved links",
              "level": level, "shift": shift,
              "source_main_manifest_sha256": hashlib.sha256(main_manifest_path.read_bytes()).hexdigest(),
              "name_inputs": name_inputs,
              "counts": {"tiles": len(records), "main_links": total_links,
                         "geometry_rows": total_rows, "omitted_geometry_rows": total_links-total_rows,
                         "experimental_fallback_tiles": len(fallback_tiles),
                         "attribute_layer_tiles": sum(1 for r in records if r["attribute_layer"]),
                         "attribute_link_ranges": sum(
                             r["attribute_layer"]["link_ranges"] for r in records
                             if r["attribute_layer"]),
                         "attribute_link_names": sum(
                             r["attribute_layer"]["link_names"] for r in records
                             if r["attribute_layer"])},
              "empty_aux_policy": empty_policy, "experimental_fallback_tile_ids": fallback_tiles,
              "max_aux_blob_bytes": max_bytes, "max_row_relative_bit_offset": max_bits,
              "tiles": records,
              "capabilities_produced": ["sparse_route_aux_geometry", "retained_main_link_ids",
                                          "house_number_attribute_layer"] + (
                                              ["street_name_attribute_layer"] if name_inputs else []),
              "capabilities_missing": ["turn_restrictions", "conditional_access", "maneuver_guidance"],
              "limitations": ["Sparse policy is validated only for the Curitiba v9 municipal profile.",
                              "Any retain-first fallback requires separate native/SDK validation; offline roundtrip is insufficient.",
                              "No turn restrictions or guidance are encoded.",
                              "House-number ranges and street-name maps are independent; unnamed OSM ways remain unnamed.",
                              "Street-name bindings use exact OSM way ids and existing NAME ids, without nearest-road inference."]}
    report_path = output_dir / "aux-manifest.json"
    report_path.write_text(json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    return {"counts": report["counts"], "max_aux_blob_bytes": max_bytes,
            "max_row_relative_bit_offset": max_bits,
            "manifest_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            "maturity": "experimental", "validation_status": report["validation_status"]}
