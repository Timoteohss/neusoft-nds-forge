"""Deterministic layer-coverage catalog derivation.

Maintains backward-compatibility aliases while delegating to the generic
nds_forge.layers.catalog builder.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .catalog import (
    CatalogError,
    CatalogProfileConfig,
    EmptyLayerError,
    ExtraTileError,
    LayerRule,
    MissingTileError,
    ParentChildCoverageError,
    SQLiteIntegrityError,
    StaleReferenceError,
    build_coverage_catalogs,
    register_catalog_step,
)


def build_catalogs(bmd_dir: Path, names_dir: Path, poi_dir: Path,
                   route_dir: Path, aux_dir: Path, output_dir: Path,
                   profile: dict[str, Any]) -> dict[str, Any]:
    """Compatibility wrapper around build_coverage_catalogs."""
    region_id = int(profile.get("route", {}).get("region_id", profile.get("region_id", 0)))
    profile_dict = {
        **profile,
        "region_id": region_id,
    }
    return build_coverage_catalogs(
        bmd_dir=bmd_dir,
        names_dir=names_dir,
        poi_dir=poi_dir,
        route_dir=route_dir,
        aux_dir=aux_dir,
        output_dir=output_dir,
        profile=profile_dict,
        generate_sqlite_index=False,
    )


__all__ = [
    "CatalogError",
    "CatalogProfileConfig",
    "EmptyLayerError",
    "ExtraTileError",
    "LayerRule",
    "MissingTileError",
    "ParentChildCoverageError",
    "SQLiteIntegrityError",
    "StaleReferenceError",
    "build_catalogs",
    "build_coverage_catalogs",
    "register_catalog_step",
]
