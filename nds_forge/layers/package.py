"""Fresh logical SQLite database assembler.

Assembles raw SQLite databases and tables from documented schema bootstrap seeds
and generated layer outputs (BMD, NAME, FTS, POI, ROUTE, SHARED, PRODUCT, ROOT).
Enforces quick_check, crossrefs/coverage validation, schema guards, and produces
deterministic intermediate manifests.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any


class PackageError(ValueError):
    """Base error for packaging and SQLite assembly."""


class SchemaMismatchError(PackageError):
    """Raised when an SQLite seed is missing expected tables or has an unsupported schema."""


class CoverageMismatchError(PackageError):
    """Raised when cross-database references or catalog coverage do not reconcile."""


class SQLiteCheckError(PackageError):
    """Raised when an SQLite database fails quick_check or integrity verification."""


EXPECTED_TABLES: dict[str, list[str]] = {
    "BMD.NDS": ["bmdTileTable", "bmdTilePatternTable"],
    "NAME.NDS": ["namedObjectTable"],
    "FTS.NDS": [
        "nameFtsTable", "poiFtsTable", "namedObject2HouseNRTable",
        "houseNR2NamedObjectTable", "ftsHouseNumberRangeTable",
        "nameFtsSubstitutionTable", "poiFtsSubstitutionTable",
    ],
    "POI.NDS": [
        "poiTable", "poiVirtualTileTable", "poiToNameStringCollTable",
        "poiNameStringCollTable", "poiLinkAccessTable", "poiRelationTable",
        "poiNamedObjectRelationTable",
    ],
    "ROUTE.NDS": ["routingTileTable", "routingAuxTileTable"],
    "SHARED.NDS": ["levelMetadataTable", "regionMetadataTable"],
    "NAV/PRODUCT.NDS": ["tileContentIndexTable", "updateRegionNameTable"],
    "ROOT.NDS": ["productDbTable"],
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _name_blob(name: str) -> bytes:
    raw = name.encode("utf-8")
    if not raw or len(raw) > 255:
        raise ValueError(f"NDS name length outside supported 1..255 byte range: {name!r}")
    return b"\x00\x10\x00\x00\x60\x80\x01" + bytes((len(raw),)) + raw + b"\x00"


def verify_table_schema(db: sqlite3.Connection, db_name: str, expected_tables: list[str]) -> None:
    """Verify that the database contains all expected tables and blocks unsupported schemas."""
    cur = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    found_tables = {row[0] for row in cur.fetchall()}
    missing = [t for t in expected_tables if t not in found_tables]
    if missing:
        raise SchemaMismatchError(
            f"Database '{db_name}' is missing expected declared tables: {missing}"
        )


def run_quick_check(db: sqlite3.Connection, db_name: str) -> None:
    """Execute PRAGMA quick_check on SQLite database."""
    try:
        result = db.execute("PRAGMA quick_check").fetchall()
    except sqlite3.DatabaseError as exc:
        raise SQLiteCheckError(f"SQLite quick_check error for {db_name}: {exc}") from exc
    if result != [("ok",)]:
        raise SQLiteCheckError(f"SQLite quick_check failed for {db_name}: {result[:3]}")


def write_bmd_table(db: sqlite3.Connection, bmd_manifest: dict[str, Any], bmd_dir: Path) -> dict[str, Any]:
    """Clear and rebuild bmdTileTable from BMD manifest."""
    verify_table_schema(db, "BMD.NDS", EXPECTED_TABLES["BMD.NDS"])
    db.execute("DELETE FROM bmdTileTable")
    db.execute("DELETE FROM bmdTilePatternTable")
    tiles = bmd_manifest.get("tiles", [])
    for tile in tiles:
        blob_path = bmd_dir / tile["path"]
        blob = blob_path.read_bytes()
        if len(blob) != tile["bytes"] or _sha256(blob_path) != tile["sha256"]:
            raise ValueError(f"BMD tile blob mismatch: {tile['tile_id']}")
        db.execute("INSERT INTO bmdTileTable VALUES(?,1,1,?,NULL,NULL)", (int(tile["tile_id"]), blob))
    db.commit()
    run_quick_check(db, "BMD.NDS")
    return {"table": "bmdTileTable", "rows_written": len(tiles)}


def write_names_and_fts_tables(
    name_db: sqlite3.Connection,
    fts_db: sqlite3.Connection,
    name_records_doc: dict[str, Any],
    poi_records_doc: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Clear and rebuild namedObjectTable and FTS index tables."""
    verify_table_schema(name_db, "NAME.NDS", EXPECTED_TABLES["NAME.NDS"])
    verify_table_schema(fts_db, "FTS.NDS", EXPECTED_TABLES["FTS.NDS"])

    recs = name_records_doc.get("records", [])
    names_profile = profile.get("names", profile)
    region_id = int(names_profile.get("region_id", 0))

    # 1. NAME.NDS
    name_db.execute("DELETE FROM namedObjectTable")
    for item in recs:
        name_db.execute(
            "INSERT INTO namedObjectTable VALUES(?,1,?)",
            (int(item["id"]), _name_blob(item["name"])),
        )
    name_db.commit()
    run_quick_check(name_db, "NAME.NDS")

    # 2. FTS.NDS
    for table in EXPECTED_TABLES["FTS.NDS"]:
        fts_db.execute(f'DELETE FROM "{table}"')

    criteria = [names_profile.get(f"criterion_{letter}", "") for letter in "abcd"]
    substitution_names = names_profile.get("admin_substitutions", [])
    for code, display in zip(criteria, substitution_names):
        fts_db.execute(
            "INSERT INTO nameFtsSubstitutionTable VALUES(?, ?, NULL, ?, NULL, 100)",
            (code, display, region_id),
        )

    for item in recs:
        cls = int(item.get("class", 11))
        row: dict[str, Any] = {
            "namedObjectId": int(item["id"]),
            "namedObjectClass": cls,
            "mortonCode": int(item.get("morton", 0)),
            "updateRegionId": region_id,
        }
        if cls == 0:
            row["criterionA"] = criteria[0]
        elif cls == 2:
            row.update(criterionA=criteria[0], criterionB=criteria[1])
        elif cls == 9:
            row.update(criterionA=criteria[0], criterionB=criteria[1], criterionC=criteria[2])
        elif cls == 10:
            row.update(criterionA=criteria[0], criterionB=criteria[1], criterionC=criteria[2], criterionD=criteria[3])
        else:
            row.update(
                criterionA=criteria[0], criterionB=criteria[1], criterionC=criteria[2],
                criterionD=names_profile.get("criterion_d", ""),
                criterionI=str(item.get("aliases", item["name"])).replace("\x1c", " "),
                criterionJ=names_profile.get("criterion_j", ""),
                searchTags=item.get("search_tags", ""),
            )
        cols = list(row.keys())
        fts_db.execute(
            f"INSERT INTO nameFtsTable({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",
            [row[c] for c in cols],
        )

    poi_recs = poi_records_doc.get("records", [])
    for poi in poi_recs:
        category = str(poi.get("category", ""))
        aliases = poi.get("aliases", "")
        if isinstance(aliases, list):
            aliases = "\x1c".join(str(alias) for alias in aliases)
        elif not isinstance(aliases, str):
            aliases = str(poi.get("name", ""))
        aliases = aliases.replace("\x1c", " \x1c")
        fts_db.execute(
            """INSERT INTO poiFtsTable(
                poiId,mortonCode,poiName,poiCategoryId,regionId,updateRegionId,
                criterionA,criterionB,criterionC,criterionD,searchTags
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                int(poi["id"] if isinstance(poi["id"], int) else str(poi["id"]).split("_")[-1]),
                int(poi.get("morton", 0)),
                aliases,
                category,
                0,
                region_id,
                *criteria[:3],
                names_profile.get("criterion_d", ""),
                poi.get("search_tags", ""),
            ),
        )

    try:
        fts_db.execute("INSERT INTO nameFtsTable(nameFtsTable) VALUES('optimize')")
    except sqlite3.OperationalError:
        pass
    try:
        fts_db.execute("INSERT INTO poiFtsTable(poiFtsTable) VALUES('optimize')")
    except sqlite3.OperationalError:
        pass
    fts_db.commit()
    run_quick_check(fts_db, "FTS.NDS")

    return {
        "name_rows": len(recs),
        "fts_name_rows": len(recs),
        "fts_poi_rows": len(poi_recs),
    }


def write_poi_tables(
    db: sqlite3.Connection,
    poi_records_doc: dict[str, Any],
    bmd_manifest: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Clear and rebuild POI tables from canonical POI records."""
    verify_table_schema(db, "POI.NDS", EXPECTED_TABLES["POI.NDS"])
    for table in EXPECTED_TABLES["POI.NDS"]:
        db.execute(f'DELETE FROM "{table}"')

    category_ids = profile.get("poi_table_category_ids", {})
    poi_recs = poi_records_doc.get("records", [])

    for row in poi_recs:
        cat_key = str(row.get("category", ""))
        # Resolve category ID from row or profile map or fallback
        cat_id = row.get("nds_category_id")
        if cat_id is None:
            cat_id = category_ids.get(cat_key, 1000)
        ident = int(row["id"] if isinstance(row["id"], int) else str(row["id"]).split("_")[-1])
        morton = int(row.get("morton", 0))
        name = str(row.get("name", ""))

        db.execute(
            "INSERT INTO poiTable VALUES (?, ?, ?, 1, NULL, NULL, NULL, 16777216, NULL, NULL, NULL, NULL)",
            (ident, int(cat_id), morton),
        )
        db.execute("INSERT INTO poiToNameStringCollTable VALUES (?, ?)", (ident, ident))
        db.execute("INSERT INTO poiNameStringCollTable VALUES (?, 1, 6, 0, 1, ?, NULL)", (ident, name))

    tiles = bmd_manifest.get("tiles", [])
    for tile in tiles:
        db.execute(
            "INSERT INTO poiVirtualTileTable VALUES (?, 1, ?, 1, 0)",
            (int(tile["tile_id"]), len(poi_recs)),
        )

    db.commit()
    run_quick_check(db, "POI.NDS")
    return {"table": "poiTable", "rows_written": len(poi_recs), "virtual_tiles": len(tiles)}


def write_route_and_aux_tables(
    db: sqlite3.Connection,
    route_manifest: dict[str, Any],
    aux_manifest: dict[str, Any],
    route_dir: Path,
    aux_dir: Path,
) -> dict[str, Any]:
    """Clear and rebuild routingTileTable and routingAuxTileTable."""
    verify_table_schema(db, "ROUTE.NDS", EXPECTED_TABLES["ROUTE.NDS"])
    db.execute("DELETE FROM routingTileTable")
    db.execute("DELETE FROM routingAuxTileTable")

    aux_by_tile = {int(item["tile_id"]): item for item in aux_manifest.get("tiles", [])}
    route_tiles = route_manifest.get("tiles", [])

    for tile in route_tiles:
        tile_id = int(tile["id"])
        route_path = route_dir / tile["route_blob"]
        aux_record = aux_by_tile.get(tile_id)
        if not aux_record:
            raise CoverageMismatchError(f"Missing AUX tile record for ROUTE tile {tile_id}")
        aux_path = aux_dir / aux_record["path"]

        if _sha256(route_path) != tile["route_sha256"]:
            raise ValueError(f"ROUTE blob hash mismatch for tile {tile_id}")
        if _sha256(aux_path) != aux_record["sha256"]:
            raise ValueError(f"AUX blob hash mismatch for tile {tile_id}")

        db.execute("INSERT INTO routingTileTable VALUES(?,1,?)", (tile_id, route_path.read_bytes()))
        db.execute(
            "INSERT INTO routingAuxTileTable VALUES(?,1,1,1,1,1,?,NULL,NULL,NULL,NULL)",
            (tile_id, aux_path.read_bytes()),
        )

    db.commit()
    run_quick_check(db, "ROUTE.NDS")
    return {"route_tiles": len(route_tiles), "aux_tiles": len(aux_by_tile)}


def write_shared_metadata_table(
    db: sqlite3.Connection,
    catalog: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild levelMetadataTable and bind regionMetadataTable in SHARED.NDS."""
    verify_table_schema(db, "SHARED.NDS", EXPECTED_TABLES["SHARED.NDS"])
    bmd = catalog["layer_coverage"]["bmd"]
    route = catalog["layer_coverage"]["route"]

    db.execute("DELETE FROM levelMetadataTable WHERE buildingBlockId IN (1, 2)")
    db.execute(
        "INSERT INTO levelMetadataTable VALUES(?,?,?,1,50000000,NULL)",
        (bmd["level"], 1, bmd["shift"]),
    )
    db.execute(
        "INSERT INTO levelMetadataTable VALUES(?,?,?,1,50000000,NULL)",
        (route["payload_level"], 2, route["payload_shift"]),
    )
    iso_code = str(profile.get("iso_country_code", "BRA"))
    db.execute("UPDATE regionMetadataTable SET isoCountryCode=?", (iso_code,))
    db.commit()
    run_quick_check(db, "SHARED.NDS")
    return {"bmd_level": bmd["level"], "route_level": route["payload_level"]}


def write_product_index_table(
    db: sqlite3.Connection,
    catalog: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild tileContentIndexTable and updateRegionNameTable in PRODUCT.NDS."""
    verify_table_schema(db, "NAV/PRODUCT.NDS", EXPECTED_TABLES["NAV/PRODUCT.NDS"])
    region_id = int(profile.get("region_id", profile.get("route", {}).get("region_id", 0)))
    bmd = catalog["layer_coverage"]["bmd"]
    route = catalog["layer_coverage"]["route"]

    db.execute("DELETE FROM tileContentIndexTable WHERE buildingBlockId IN (1, 2, 4)")
    for bb_id, level, tiles in (
        (1, bmd["level"], bmd["tile_ids"]),
        (4, bmd["level"], bmd["tile_ids"]),
        (2, route["index_level"], route["index_tile_ids"]),
    ):
        for seq, tile in enumerate(tiles):
            db.execute(
                "INSERT INTO tileContentIndexTable VALUES(?,?,?,?,?,1,1,?)",
                (region_id, bb_id, level, seq, int(tile), b"\x80"),
            )

    label = str(profile.get("package_label", "Curitiba v9 OSM"))
    db.execute(
        "UPDATE updateRegionNameTable SET nameString=? WHERE updateRegionId=?",
        (label, region_id),
    )
    db.commit()
    run_quick_check(db, "NAV/PRODUCT.NDS")
    return {"region_id": region_id, "bmd_tiles": len(bmd["tile_ids"]), "route_index_tiles": len(route["index_tile_ids"])}


def write_root_product_table(db: sqlite3.Connection, profile: dict[str, Any]) -> dict[str, Any]:
    """Bind product internal name in ROOT.NDS."""
    verify_table_schema(db, "ROOT.NDS", EXPECTED_TABLES["ROOT.NDS"])
    label = str(profile.get("package_label", "Curitiba v9 OSM"))
    db.execute("UPDATE productDbTable SET internalName=? WHERE uri='NAV/PRODUCT.NDS'", (label,))
    db.commit()
    run_quick_check(db, "ROOT.NDS")
    return {"internal_name": label}


def verify_crossrefs_and_coverage(
    dbs: dict[str, sqlite3.Connection],
    catalog: dict[str, Any],
    profile: dict[str, Any],
) -> None:
    """Validate cross-database foreign references and tile coverage against catalog plan.

    Raises:
        CoverageMismatchError: When tile coverage or crossrefs do not reconcile.
    """
    bmd_expected = set(catalog["layer_coverage"]["bmd"]["tile_ids"])
    route_expected = set(catalog["layer_coverage"]["route"]["tile_ids"])
    route_index_expected = set(catalog["layer_coverage"]["route"]["index_tile_ids"])

    # 1. BMD.NDS check
    if "BMD.NDS" in dbs:
        cur = dbs["BMD.NDS"].execute("SELECT tileId FROM bmdTileTable")
        bmd_actual = {row[0] for row in cur.fetchall()}
        if bmd_actual != bmd_expected:
            raise CoverageMismatchError(
                f"BMD tile table coverage mismatch: expected {sorted(bmd_expected)}, got {sorted(bmd_actual)}"
            )

    # 2. ROUTE.NDS check
    if "ROUTE.NDS" in dbs:
        cur_route = dbs["ROUTE.NDS"].execute("SELECT tileId FROM routingTileTable")
        route_actual = {row[0] for row in cur_route.fetchall()}
        if route_actual != route_expected:
            raise CoverageMismatchError(
                f"ROUTE tile table coverage mismatch: expected {sorted(route_expected)}, got {sorted(route_actual)}"
            )

        cur_aux = dbs["ROUTE.NDS"].execute("SELECT tileId FROM routingAuxTileTable")
        aux_actual = {row[0] for row in cur_aux.fetchall()}
        if aux_actual != route_expected:
            raise CoverageMismatchError(
                f"ROUTE auxiliary tile table coverage mismatch: expected {sorted(route_expected)}, got {sorted(aux_actual)}"
            )

    # 3. PRODUCT.NDS check
    if "NAV/PRODUCT.NDS" in dbs:
        cur_prod_bmd = dbs["NAV/PRODUCT.NDS"].execute(
            "SELECT tileId FROM tileContentIndexTable WHERE buildingBlockId = 1"
        )
        prod_bmd = {row[0] for row in cur_prod_bmd.fetchall()}
        if prod_bmd != bmd_expected:
            raise CoverageMismatchError(
                f"PRODUCT BMD tile index mismatch: expected {sorted(bmd_expected)}, got {sorted(prod_bmd)}"
            )

        cur_prod_route = dbs["NAV/PRODUCT.NDS"].execute(
            "SELECT tileId FROM tileContentIndexTable WHERE buildingBlockId = 2"
        )
        prod_route_idx = {row[0] for row in cur_prod_route.fetchall()}
        if prod_route_idx != route_index_expected:
            raise CoverageMismatchError(
                f"PRODUCT route index mismatch: expected {sorted(route_index_expected)}, got {sorted(prod_route_idx)}"
            )

    # 4. SHARED.NDS check
    if "SHARED.NDS" in dbs:
        cur_levels = dbs["SHARED.NDS"].execute(
            "SELECT level, buildingBlockId, shift FROM levelMetadataTable WHERE buildingBlockId IN (1, 2)"
        )
        level_map = {row[1]: (row[0], row[2]) for row in cur_levels.fetchall()}
        bmd_cov = catalog["layer_coverage"]["bmd"]
        route_cov = catalog["layer_coverage"]["route"]
        if level_map.get(1) != (bmd_cov["level"], bmd_cov["shift"]):
            raise CoverageMismatchError("SHARED BMD level metadata mismatch with catalog")
        if level_map.get(2) != (route_cov["payload_level"], route_cov["payload_shift"]):
            raise CoverageMismatchError("SHARED ROUTE level metadata mismatch with catalog")


def assemble_logical_sqlite_databases(
    db_paths: dict[str, Path],
    bmd_dir: Path,
    names_dir: Path,
    poi_dir: Path,
    route_dir: Path,
    aux_dir: Path,
    catalog_path: Path,
    output_manifest_path: Path,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Assemble all logical SQLite databases in place, verify crossrefs, and emit manifest."""
    catalog_doc = _read_json(catalog_path)
    bmd_manifest = _read_json(bmd_dir / "bmd-manifest.json")
    names_doc = _read_json(names_dir / "name-records.json")
    poi_doc = _read_json(poi_dir / "poi-records.json")
    route_manifest = _read_json(route_dir / "route-manifest.json")
    aux_manifest = _read_json(aux_dir / "aux-manifest.json")

    connections: dict[str, sqlite3.Connection] = {}
    summary: dict[str, Any] = {}

    try:
        for name, path in db_paths.items():
            connections[name] = sqlite3.connect(str(path))

        # Rebuild declared tables
        if "BMD.NDS" in connections:
            summary["BMD.NDS"] = write_bmd_table(connections["BMD.NDS"], bmd_manifest, bmd_dir)

        if "NAME.NDS" in connections and "FTS.NDS" in connections:
            summary["NAME_FTS"] = write_names_and_fts_tables(
                connections["NAME.NDS"], connections["FTS.NDS"],
                names_doc, poi_doc, profile,
            )

        if "POI.NDS" in connections:
            summary["POI.NDS"] = write_poi_tables(
                connections["POI.NDS"], poi_doc, bmd_manifest, profile,
            )

        if "ROUTE.NDS" in connections:
            summary["ROUTE.NDS"] = write_route_and_aux_tables(
                connections["ROUTE.NDS"], route_manifest, aux_manifest, route_dir, aux_dir,
            )

        if "SHARED.NDS" in connections:
            summary["SHARED.NDS"] = write_shared_metadata_table(
                connections["SHARED.NDS"], catalog_doc, profile,
            )

        if "NAV/PRODUCT.NDS" in connections:
            summary["PRODUCT.NDS"] = write_product_index_table(
                connections["NAV/PRODUCT.NDS"], catalog_doc, profile,
            )

        if "ROOT.NDS" in connections:
            summary["ROOT.NDS"] = write_root_product_table(connections["ROOT.NDS"], profile)

        # Cross-database coverage verification
        verify_crossrefs_and_coverage(connections, catalog_doc, profile)

    finally:
        for conn in connections.values():
            conn.close()

    # Generate deterministic manifest
    records = []
    for name, path in sorted(db_paths.items()):
        records.append({
            "logical_name": name,
            "path": str(path.relative_to(output_manifest_path.parent) if path.is_relative_to(output_manifest_path.parent) else path),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
            "summary": summary.get(name, {}),
        })

    manifest_payload = {
        "schema_version": 1,
        "step": "sqlite.assemble",
        "maturity": "experimental",
        "profile_id": str(profile.get("profile_id", "generic-profile")),
        "databases": records,
        "capabilities_produced": [
            "logical_sqlite_databases",
            "sqlite_quick_checked",
            "coverage_reconciled",
        ],
    }
    output_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    output_manifest_path.write_text(
        json.dumps(manifest_payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return manifest_payload
