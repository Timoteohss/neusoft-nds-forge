"""Generic per-layer coverage catalog builder with independent profile rules.

Builds tile indexes and coverage matrices from actual layer artifacts with
independent layer rules, catches missing/extra tiles, detects stale references,
validates parent-child coverage, gracefully accommodates empty optional layers,
and verifies SQLite index integrity with deterministic reporting.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..registry import EXPERIMENTAL, Step, StepContext, replace_step
from .bmd_codec import tile_for, tile_origin

# --- Exceptions ---

class CatalogError(ValueError):
    """Base error for coverage catalog violations."""


class MissingTileError(CatalogError):
    """Raised when an expected tile is missing in a dependent layer."""


class ExtraTileError(CatalogError):
    """Raised when an unexpected extra tile is present in a dependent layer."""


class StaleReferenceError(CatalogError):
    """Raised when source artifact hashes or profile references are stale/mismatched."""


class ParentChildCoverageError(CatalogError):
    """Raised when index tiles do not correctly cover child payload tiles."""


class EmptyLayerError(CatalogError):
    """Raised when a required layer has no tiles or records."""


class SQLiteIntegrityError(CatalogError):
    """Raised when catalog SQLite database fails PRAGMA integrity_check."""


# --- Configuration Models ---

@dataclass(frozen=True)
class LayerRule:
    """Independent coverage and index rules for a specific layer."""
    name: str
    building_block_id: int
    payload_level: int
    payload_shift: int
    index_level: int | None = None
    is_optional: bool = False


@dataclass
class CatalogProfileConfig:
    """Catalog builder configuration driven by target profile parameters."""
    profile_id: str
    region_id: int
    source_sha256: str | None = None
    layer_rules: dict[str, LayerRule] = field(default_factory=dict)
    poi_outside_bmd_policy: str = "reject"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CatalogProfileConfig:
        profile_id = str(data.get("profile_id", "generic-profile"))
        region_id = int(data.get("region_id", 0))
        source_sha256 = data.get("source_sha256")

        # Layer rules configuration
        rules: dict[str, LayerRule] = {}
        grid = data.get("grid", {})
        index_levels = data.get("index_levels", {})

        # BMD layer
        bmd_grid = grid.get("bmd", {})
        rules["bmd"] = LayerRule(
            name="bmd",
            building_block_id=1,
            payload_level=int(bmd_grid.get("level", 11)),
            payload_shift=int(bmd_grid.get("shift", 4)),
            index_level=index_levels.get("bmd"),
            is_optional=False,
        )

        # ROUTE layer
        rules["route"] = LayerRule(
            name="route",
            building_block_id=2,
            payload_level=int(grid.get("route_level", 13)),
            payload_shift=int(grid.get("route_shift", 4)),
            index_level=int(index_levels.get("route", 9)) if "route" in index_levels else None,
            is_optional=False,
        )

        # POI layer
        rules["poi"] = LayerRule(
            name="poi",
            building_block_id=4,
            payload_level=int(bmd_grid.get("level", 11)),
            payload_shift=int(bmd_grid.get("shift", 4)),
            index_level=None,
            is_optional=True,
        )

        # NAMES layer
        rules["names"] = LayerRule(
            name="names",
            building_block_id=3,
            payload_level=0,
            payload_shift=0,
            index_level=None,
            is_optional=False,
        )

        # Allow user overrides from data["layer_rules"]
        if "layer_rules" in data:
            for k, v in data["layer_rules"].items():
                if isinstance(v, LayerRule):
                    rules[k] = v
                elif isinstance(v, dict):
                    rules[k] = LayerRule(
                        name=v.get("name", k),
                        building_block_id=int(v.get("building_block_id", 0)),
                        payload_level=int(v.get("payload_level", 0)),
                        payload_shift=int(v.get("payload_shift", 0)),
                        index_level=int(v["index_level"]) if v.get("index_level") is not None else None,
                        is_optional=bool(v.get("is_optional", False)),
                    )

        poi_config = data.get("poi", {})
        poi_outside_bmd_policy = str(
            poi_config.get("outside_bmd_policy", "reject") if isinstance(poi_config, dict) else "reject")
        if poi_outside_bmd_policy not in {"reject", "exclude"}:
            raise CatalogError("poi.outside_bmd_policy must be reject or exclude")

        return cls(
            profile_id=profile_id,
            region_id=region_id,
            source_sha256=source_sha256,
            layer_rules=rules,
            poi_outside_bmd_policy=poi_outside_bmd_policy,
        )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise StaleReferenceError(f"Cannot read artifact JSON {path}: {exc}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_parent_index_tiles(
    child_tiles: list[int],
    payload_level: int,
    index_level: int,
    declared_index_tiles: list[int] | None = None,
) -> list[int]:
    """Calculate and validate parent index tiles covering child payload tiles.

    Raises:
        ParentChildCoverageError: When parent-child containment fails or index is orphan.
    """
    if not child_tiles:
        return []

    calculated_parents: set[int] = set()
    parent_to_children: dict[int, list[int]] = {}

    for tile_id in child_tiles:
        ox, oy = tile_origin(tile_id, payload_level)
        lon = ox * 180.0 / (1 << 31)
        lat = oy * 180.0 / (1 << 31)
        parent = tile_for(lon, lat, index_level)
        calculated_parents.add(parent)
        parent_to_children.setdefault(parent, []).append(tile_id)

    if declared_index_tiles is not None:
        declared_set = set(declared_index_tiles)
        # Check for child tiles whose parent is not declared
        uncovered = calculated_parents - declared_set
        if uncovered:
            raise ParentChildCoverageError(
                f"Child tiles map to parent index tiles {sorted(uncovered)} which are "
                f"not in declared index tiles {sorted(declared_set)}"
            )
        # Check for orphan declared index tiles with no children
        orphans = declared_set - calculated_parents
        if orphans:
            raise ParentChildCoverageError(
                f"Declared index tiles {sorted(orphans)} have no corresponding child tiles"
            )

    return sorted(calculated_parents)


def compile_sqlite_catalog(
    db_path: Path,
    document: dict[str, Any],
) -> None:
    """Create an SQLite catalog coverage database and verify its integrity."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE layer_coverage (
                layer_name TEXT NOT NULL,
                building_block_id INTEGER NOT NULL,
                level INTEGER NOT NULL,
                tile_id INTEGER NOT NULL,
                PRIMARY KEY (layer_name, tile_id)
            )
        """)
        cur.execute("""
            CREATE TABLE catalog_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Insert metadata
        cur.execute("INSERT INTO catalog_metadata VALUES (?, ?)", ("profile_id", document["profile_id"]))
        cur.execute("INSERT INTO catalog_metadata VALUES (?, ?)", ("region_id", str(document["region_id"])))

        # Insert coverage tiles
        for layer_name, cov in document.get("layer_coverage", {}).items():
            bb_id = cov.get("building_block_id", 0)
            level = cov.get("level") or cov.get("payload_level", 0)
            for tile_id in cov.get("tile_ids", []):
                cur.execute(
                    "INSERT INTO layer_coverage VALUES (?, ?, ?, ?)",
                    (layer_name, bb_id, level, tile_id),
                )
        conn.commit()

        # PRAGMA integrity check
        cur.execute("PRAGMA integrity_check")
        row = cur.fetchone()
        if not row or row[0] != "ok":
            raise SQLiteIntegrityError(f"SQLite integrity check failed on {db_path}: {row}")
    finally:
        conn.close()


def verify_sqlite_catalog_integrity(db_path: Path) -> None:
    """Verify that an existing SQLite catalog file passes integrity check."""
    if not db_path.is_file():
        raise SQLiteIntegrityError(f"Catalog SQLite database not found: {db_path}")
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA integrity_check")
        row = cur.fetchone()
        if not row or row[0] != "ok":
            raise SQLiteIntegrityError(f"SQLite integrity check failed on {db_path}: {row}")
    except sqlite3.DatabaseError as exc:
        raise SQLiteIntegrityError(f"SQLite database error on {db_path}: {exc}") from exc
    finally:
        conn.close()


def _poi_coverage_tiles(records: list[dict], bmd_tiles: list[int], payload_level: int,
                        outside_policy: str = "reject") -> tuple[list[int], list[dict[str, Any]]]:
    """Resolve accepted POI locations without inferring coverage from BMD."""
    available = set(bmd_tiles)
    occupied = set()
    outside: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise CatalogError(f"POI record {index} must be an object")
        try:
            if "tile_id" in record:
                value = record["tile_id"]
                if isinstance(value, bool) or not isinstance(value, (int, str)):
                    raise ValueError("tile_id must be an integer")
                tile = int(value)
            else:
                lon, lat = record["longitude"], record["latitude"]
                if isinstance(lon, bool) or isinstance(lat, bool):
                    raise ValueError("coordinates must be numeric")
                lon, lat = float(lon), float(lat)
                if (not math.isfinite(lon) or not math.isfinite(lat) or
                        not -180 <= lon <= 180 or not -90 <= lat <= 90):
                    raise ValueError("coordinates must be finite WGS84 longitude/latitude")
                tile = tile_for(lon, lat, payload_level)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise CatalogError(f"POI record {index} has no valid tile_id or coordinates: {exc}") from exc
        if tile not in available:
            if outside_policy == "reject":
                raise CatalogError(f"POI record {index} tile {tile} is outside BMD coverage")
            outside.append({"index": index, "id": record.get("id"),
                            "source_ref": record.get("source_ref"), "tile_id": tile})
            continue
        occupied.add(tile)
    return sorted(occupied), outside


def build_coverage_catalogs(
    bmd_dir: Path,
    names_dir: Path,
    poi_dir: Path | None,
    route_dir: Path,
    aux_dir: Path,
    output_dir: Path,
    profile: CatalogProfileConfig | dict[str, Any],
    declared_route_index_tiles: list[int] | None = None,
    generate_sqlite_index: bool = True,
) -> dict[str, Any]:
    """Build tile indexes and coverage matrices with independent per-layer profile rules.

    Enforces:
    - Missing/extra tile validation between interdependent layers (ROUTE vs AUX).
    - Stale reference detection (SHA-256 checks, profile mismatch).
    - Parent-child index containment validation.
    - Graceful handling of empty optional layers vs fail-closed required layers.
    - SQLite catalog index creation and PRAGMA integrity check.
    - Deterministic JSON output.
    """
    if isinstance(profile, dict):
        cfg = CatalogProfileConfig.from_dict(profile)
    else:
        cfg = profile

    route_rule = cfg.layer_rules.get("route", LayerRule("route", 2, 13, 4, 9, False))
    bmd_rule = cfg.layer_rules.get("bmd", LayerRule("bmd", 1, 11, 4, None, False))
    poi_rule = cfg.layer_rules.get("poi", LayerRule("poi", 4, 11, 4, None, True))
    _names_rule = cfg.layer_rules.get("names", LayerRule("names", 3, 0, 0, None, False))

    # 1. Read input artifacts and check existence
    bmd_manifest_path = bmd_dir / "bmd-manifest.json"
    names_records_path = names_dir / "name-records.json"
    route_manifest_path = route_dir / "route-manifest.json"
    aux_manifest_path = aux_dir / "aux-manifest.json"

    bmd = _read_json(bmd_manifest_path)
    names = _read_json(names_records_path)
    route = _read_json(route_manifest_path)
    aux = _read_json(aux_manifest_path)

    # 2. Stale reference checks
    if cfg.source_sha256 is not None:  # noqa: SIM102 -- Keep nested guards to make validation branches explicit while preserving short-circuit order
        if route.get("source_sha256") and route.get("source_sha256") != cfg.source_sha256:
            raise StaleReferenceError(
                f"ROUTE source_sha256 ({route.get('source_sha256')}) does not match "
                f"configured source ({cfg.source_sha256})"
            )

    aux_profile = aux.get("profile_id")
    if aux_profile and aux_profile != cfg.profile_id:
        raise StaleReferenceError(
            f"AUX profile_id ({aux_profile}) does not match configured profile ({cfg.profile_id})"
        )

    # Verify route level and shift against profile rule
    if route.get("level") is not None and int(route["level"]) != route_rule.payload_level:
        raise CatalogError(
            f"ROUTE manifest level ({route.get('level')}) does not match profile rule "
            f"payload_level ({route_rule.payload_level})"
        )
    if route.get("shift") is not None and int(route["shift"]) != route_rule.payload_shift:
        raise CatalogError(
            f"ROUTE manifest shift ({route.get('shift')}) does not match profile rule "
            f"payload_shift ({route_rule.payload_shift})"
        )

    # 3. Missing / Extra Tile Checks
    # BMD Tiles
    raw_bmd_tiles = [int(item["tile_id"]) for item in bmd.get("tiles", [])]
    if len(set(raw_bmd_tiles)) != len(raw_bmd_tiles):
        raise CatalogError("BMD manifest contains duplicate tile entries")
    if not raw_bmd_tiles and not bmd_rule.is_optional:
        raise EmptyLayerError("BMD layer is empty but marked required")
    bmd_tiles = sorted(raw_bmd_tiles)

    # ROUTE Tiles
    raw_route_tiles = [int(item["id"]) for item in route.get("tiles", [])]
    if len(set(raw_route_tiles)) != len(raw_route_tiles):
        raise CatalogError("ROUTE manifest contains duplicate tile entries")
    if not raw_route_tiles and not route_rule.is_optional:
        raise EmptyLayerError("ROUTE layer is empty but marked required")
    route_tiles = sorted(raw_route_tiles)

    # AUX Tiles vs ROUTE Tiles
    raw_aux_tiles = [int(item["tile_id"]) for item in aux.get("tiles", [])]
    if len(set(raw_aux_tiles)) != len(raw_aux_tiles):
        raise CatalogError("AUX manifest contains duplicate tile entries")
    aux_tiles_set = set(raw_aux_tiles)
    route_tiles_set = set(route_tiles)

    extra_aux = aux_tiles_set - route_tiles_set
    if extra_aux:
        raise ExtraTileError(f"AUX has extra tile(s) not in ROUTE: {sorted(extra_aux)}")

    missing_aux = route_tiles_set - aux_tiles_set
    if missing_aux:
        raise MissingTileError(f"AUX is missing tile(s) present in ROUTE: {sorted(missing_aux)}")

    # 4. Parent-Child Coverage Checks
    route_index_tiles: list[int] = []
    if route_rule.index_level is not None:
        route_index_tiles = compute_parent_index_tiles(
            child_tiles=route_tiles,
            payload_level=route_rule.payload_level,
            index_level=route_rule.index_level,
            declared_index_tiles=declared_route_index_tiles,
        )

    # 5. POI Layer (Empty Optional vs Required)
    poi_tiles: list[int] = []
    poi_outside_bmd: list[dict[str, Any]] = []
    poi_count = 0
    poi_status = "present"
    poi_manifest_sha = None

    if poi_dir is not None and (poi_dir / "poi-records.json").is_file():
        poi_manifest_path = poi_dir / "poi-records.json"
        poi = _read_json(poi_manifest_path)
        poi_manifest_sha = _sha256(poi_manifest_path)
        poi_records = poi.get("records", [])
        if not isinstance(poi_records, list):
            raise CatalogError("POI records must be an array")
        poi_count = len(poi_records)
        if poi.get("counts", {}).get("accepted", poi_count) != poi_count:
            raise CatalogError("POI accepted count does not match the actual records")
        if poi_count > 0:
            poi_tiles, poi_outside_bmd = _poi_coverage_tiles(
                poi_records, bmd_tiles, bmd_rule.payload_level, cfg.poi_outside_bmd_policy)
        else:
            if not poi_rule.is_optional:
                raise EmptyLayerError("POI layer has 0 accepted records and is marked required")
            poi_status = "empty_optional"
    else:
        if not poi_rule.is_optional:
            raise EmptyLayerError("POI layer artifact missing and marked required")
        poi_status = "empty_optional"

    # 6. Construct Deterministic Document
    document: dict[str, Any] = {
        "schema_version": 1,
        "step": "catalogs.build",
        "maturity": "experimental",
        "validation_status": "unvalidated",
        "profile_id": cfg.profile_id,
        "region_id": cfg.region_id,
        "layer_coverage": {
            "bmd": {
                "building_block_id": bmd_rule.building_block_id,
                "level": bmd_rule.payload_level,
                "shift": bmd_rule.payload_shift,
                "tile_ids": bmd_tiles,
            },
            "route": {
                "building_block_id": route_rule.building_block_id,
                "payload_level": route_rule.payload_level,
                "payload_shift": route_rule.payload_shift,
                "index_level": route_rule.index_level,
                "index_tile_ids": route_index_tiles,
                "tile_ids": route_tiles,
                "link_count": int(route.get("links", 0)),
                "intersection_count": int(route.get("intersections", 0)),
                "aux_geometry_count": int(aux.get("counts", {}).get("geometry_rows", 0)),
            },
            "poi": {
                "building_block_id": poi_rule.building_block_id,
                "level": poi_rule.payload_level,
                "tile_ids": poi_tiles,
                "record_count": poi_count,
                "outside_bmd_policy": cfg.poi_outside_bmd_policy,
                "outside_bmd_count": len(poi_outside_bmd),
                "outside_bmd_records": poi_outside_bmd,
                "status": poi_status,
            },
        },
        "counts": {
            "bmd_tiles": len(bmd_tiles),
            "route_tiles": len(route_tiles),
            "route_index_tiles": len(route_index_tiles),
            "street_records": int(names.get("counts", {}).get("street_records", 0)),
            "admin_records": int(names.get("counts", {}).get("admin_records", 0)),
            "poi_records": poi_count,
            "poi_outside_bmd": len(poi_outside_bmd),
        },
        "source_artifacts": {
            "bmd_manifest_sha256": _sha256(bmd_manifest_path),
            "names_sha256": _sha256(names_records_path),
            "route_manifest_sha256": _sha256(route_manifest_path),
            "aux_manifest_sha256": _sha256(aux_manifest_path),
        },
        "capabilities_produced": ["profile_tile_coverage_plan", "per_layer_rules"],
        "capabilities_missing": ["national_catalog_ids", "address_coverage", "native_or_device_validation"],
    }
    if poi_manifest_sha is not None:
        document["source_artifacts"]["poi_sha256"] = poi_manifest_sha

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "catalogs.json"
    out_path.write_text(json.dumps(document, sort_keys=True, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 7. SQLite Catalog Generation & Integrity Check
    sqlite_outputs: dict[str, Any] = {}
    if generate_sqlite_index:
        sqlite_path = output_dir / "catalog-index.db"
        compile_sqlite_catalog(sqlite_path, document)
        sqlite_outputs["catalog-index.db"] = {
            "bytes": sqlite_path.stat().st_size,
            "sha256": _sha256(sqlite_path),
        }

    return {
        "outputs": {
            "catalogs.json": {
                "bytes": out_path.stat().st_size,
                "sha256": _sha256(out_path),
            },
            **sqlite_outputs,
        },
        "counts": document["counts"],
        "maturity": "experimental",
        "validation_status": "unvalidated",
        "capabilities_produced": document["capabilities_produced"],
        "capabilities_missing": document["capabilities_missing"],
    }


def _execute(context: StepContext) -> dict[str, Any]:
    config = context.config.neusoft
    return build_coverage_catalogs(
        bmd_dir=context.dependency_outputs["geometry.bmd"],
        names_dir=context.dependency_outputs["names.compile"],
        poi_dir=context.dependency_outputs.get("poi.compile"),
        route_dir=context.dependency_outputs["routing.main"],
        aux_dir=context.dependency_outputs["routing.aux"],
        output_dir=context.output_dir,
        profile={
            **config,
            "profile_id": config.get("profile_id", "generic-profile"),
            "region_id": config.get("names", {}).get("region_id", 0),
            "source_sha256": context.config.source.get("sha256"),
        },
    )


def register_catalog_step() -> None:
    replace_step(
        Step(
            "catalogs.build",
            EXPERIMENTAL,
            ("geometry.bmd", "names.compile", "poi.compile", "routing.main", "routing.aux"),
            "catalogs",
            "Build coverage indices with independent per-layer profile rules",
            inputs=(
                "geometry.bmd/bmd-manifest.json",
                "names.compile/name-records.json",
                "poi.compile/poi-records.json",
                "routing.main/route-manifest.json",
                "routing.aux/aux-manifest.json",
                "neusoft.grid",
                "neusoft.index_levels",
            ),
            outputs=("catalogs.json",),
            capabilities=("profile_tile_coverage_plan", "per_layer_rules"),
        ),
        _execute,
    )
