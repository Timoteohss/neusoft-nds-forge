"""Experimental sparse ROUTE-AUX writer for the validated v9 profile."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .route_codec import encode_aux_sparse, parse_aux_geometries


def compile_route_aux(route_main_dir: Path, output_dir: Path,
                      profile: dict[str, Any]) -> dict[str, Any]:
    """Write boundary-adjacent and bent-link shapes, retaining map markers.

    This is pinned to `neusoft-curitiba-v9`; the sparse policy is backed by
    local native+SDK validation for its 103-tile municipal package. It is not
    asserted as a universal fallback rule for other profiles.
    """
    if profile.get("profile_id") != "neusoft-curitiba-v9":
        raise ValueError("Sparse AUX policy is verified only for neusoft-curitiba-v9")
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
    records = []
    total_links = total_rows = 0
    max_bytes = max_bits = 0
    for tile in tiles:
        tile_id = int(tile["tile_id"])
        links = tile.get("links")
        if not isinstance(links, list) or not links:
            raise ValueError(f"No main links for tile {tile_id}")
        retained = {index for index, link in enumerate(links)
                    if link.get("retain_aux_geometry") is True}
        # Every tile in the already validated municipal baseline has at least
        # two retained rows. Keep this guard explicit to avoid the unvalidated
        # zero-geometry edge case observed in the earlier pilot.
        if not retained:
            raise ValueError(f"Sparse AUX policy retained no geometry rows for tile {tile_id}")
        blob, stats = encode_aux_sparse(tile_id, level, shift, links, retained,
                                        guard_u16_offsets=True)
        rows = parse_aux_geometries(blob, tile_id, level, shift)
        if len(rows) != len(retained) or {row["link"] for row in rows} != retained:
            raise ValueError(f"AUX retained-link roundtrip failed for tile {tile_id}")
        path = output_dir / f"{tile_id}.route-aux.bin"
        path.write_bytes(blob)
        digest = hashlib.sha256(blob).hexdigest()
        records.append({"tile_id": tile_id, "path": path.name, "bytes": len(blob),
                        "sha256": digest, **stats,
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
              "counts": {"tiles": len(records), "main_links": total_links,
                         "geometry_rows": total_rows, "omitted_geometry_rows": total_links-total_rows},
              "max_aux_blob_bytes": max_bytes, "max_row_relative_bit_offset": max_bits,
              "tiles": records,
              "capabilities_produced": ["sparse_route_aux_geometry", "retained_main_link_ids"],
              "capabilities_missing": ["turn_restrictions", "conditional_access", "maneuver_guidance"],
              "limitations": ["Sparse policy is validated only for the Curitiba v9 municipal profile.",
                              "No turn restrictions or guidance are encoded."]}
    report_path = output_dir / "aux-manifest.json"
    report_path.write_text(json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    return {"counts": report["counts"], "max_aux_blob_bytes": max_bytes,
            "max_row_relative_bit_offset": max_bits,
            "manifest_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            "maturity": "experimental", "validation_status": report["validation_status"]}
