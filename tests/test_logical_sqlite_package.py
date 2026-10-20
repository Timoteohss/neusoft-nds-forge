"""Unit tests for fresh logical SQLite database assembler (Issue #22 / [3.3]).

Validates:
1. Assembly of pure SQLite databases from layer outputs (BMD, NAME, FTS, POI, ROUTE, SHARED, PRODUCT, ROOT).
2. PRAGMA quick_check execution on every assembled database.
3. Cross-database references and tile coverage reconciliation against catalog plan.
4. Schema validation blocking missing or unsupported table structures.
5. Derived rows in PRODUCT, SHARED, and ROOT dynamically bound to profile and catalog outputs.
6. Deterministic intermediate sqlite-manifest.json generation.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from nds_forge.layers.package import (
    CoverageMismatchError,
    EXPECTED_TABLES,
    SchemaMismatchError,
    assemble_logical_sqlite_databases,
    run_quick_check,
    verify_crossrefs_and_coverage,
    verify_table_schema,
)


class LogicalSQLiteAssemblerTests(unittest.TestCase):
    """Test suite for assemble_logical_sqlite_databases and schema guards."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

        self.db_dir = self.root / "dbs"
        self.bmd_dir = self.root / "bmd"
        self.names_dir = self.root / "names"
        self.poi_dir = self.root / "poi"
        self.route_dir = self.root / "route"
        self.aux_dir = self.root / "aux"
        self.cat_dir = self.root / "catalogs"

        for d in (self.db_dir, self.bmd_dir, self.names_dir, self.poi_dir, self.route_dir, self.aux_dir, self.cat_dir):
            d.mkdir(parents=True)

        self._setup_fixtures()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _setup_fixtures(self) -> None:
        self.tile_1 = 540673410
        self.tile_2 = 540673411
        self.bmd_tile = 135168352
        self.route_index_tile = 33792088

        # 1. Layer Manifests
        bmd_blob = self.bmd_dir / "tile_135168352.bin"
        bmd_blob.write_bytes(b"\x00\x01\x02\x03BMD_TEST_BLOB")
        import hashlib
        bmd_sha = hashlib.sha256(bmd_blob.read_bytes()).hexdigest()
        (self.bmd_dir / "bmd-manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "tiles": [{
                "tile_id": self.bmd_tile,
                "path": "tile_135168352.bin",
                "bytes": len(bmd_blob.read_bytes()),
                "sha256": bmd_sha,
            }],
        }), encoding="utf-8")

        (self.names_dir / "name-records.json").write_text(json.dumps({
            "schema_version": 1,
            "records": [
                {"id": 1, "name": "Brasil", "class": 0, "morton": 1},
                {"id": 100, "name": "Rua XV de Novembro", "class": 11, "morton": 500, "search_tags": "XV"},
            ],
        }), encoding="utf-8")

        (self.poi_dir / "poi-records.json").write_text(json.dumps({
            "schema_version": 1,
            "records": [
                {"id": 501, "name": "Restaurante Central", "category": "restaurant", "morton": 600, "search_tags": "Central"},
            ],
        }), encoding="utf-8")

        route_blob_1 = self.route_dir / "route_540673410.bin"
        route_blob_1.write_bytes(b"ROUTE_BLOB_1")
        route_blob_2 = self.route_dir / "route_540673411.bin"
        route_blob_2.write_bytes(b"ROUTE_BLOB_2")
        r1_sha = hashlib.sha256(route_blob_1.read_bytes()).hexdigest()
        r2_sha = hashlib.sha256(route_blob_2.read_bytes()).hexdigest()

        (self.route_dir / "route-manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "level": 13,
            "shift": 4,
            "tiles": [
                {"id": self.tile_1, "route_blob": "route_540673410.bin", "route_sha256": r1_sha},
                {"id": self.tile_2, "route_blob": "route_540673411.bin", "route_sha256": r2_sha},
            ],
        }), encoding="utf-8")

        aux_blob_1 = self.aux_dir / "aux_540673410.bin"
        aux_blob_1.write_bytes(b"AUX_BLOB_1")
        aux_blob_2 = self.aux_dir / "aux_540673411.bin"
        aux_blob_2.write_bytes(b"AUX_BLOB_2")
        a1_sha = hashlib.sha256(aux_blob_1.read_bytes()).hexdigest()
        a2_sha = hashlib.sha256(aux_blob_2.read_bytes()).hexdigest()

        (self.aux_dir / "aux-manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "tiles": [
                {"tile_id": self.tile_1, "path": "aux_540673410.bin", "sha256": a1_sha},
                {"tile_id": self.tile_2, "path": "aux_540673411.bin", "sha256": a2_sha},
            ],
        }), encoding="utf-8")

        # 2. Catalogs Document
        self.catalog_path = self.cat_dir / "catalogs.json"
        self.catalog_path.write_text(json.dumps({
            "schema_version": 1,
            "profile_id": "test-curitiba-v9",
            "region_id": 439,
            "layer_coverage": {
                "bmd": {"level": 11, "shift": 4, "tile_ids": [self.bmd_tile]},
                "route": {
                    "payload_level": 13,
                    "payload_shift": 4,
                    "index_level": 9,
                    "index_tile_ids": [self.route_index_tile],
                    "tile_ids": [self.tile_1, self.tile_2],
                },
            },
        }), encoding="utf-8")

        # 3. Create Seed SQLite Databases with Schemas
        self.db_paths = {
            "BMD.NDS": self.db_dir / "BMD.NDS",
            "NAME.NDS": self.db_dir / "NAME.NDS",
            "FTS.NDS": self.db_dir / "FTS.NDS",
            "POI.NDS": self.db_dir / "POI.NDS",
            "ROUTE.NDS": self.db_dir / "ROUTE.NDS",
            "SHARED.NDS": self.db_dir / "SHARED.NDS",
            "NAV/PRODUCT.NDS": self.db_dir / "PRODUCT.NDS",
            "ROOT.NDS": self.db_dir / "ROOT.NDS",
        }

        self._init_seed_databases()

        self.profile = {
            "profile_id": "test-curitiba-v9",
            "region_id": 439,
            "package_label": "Curitiba v9 Test Package",
            "iso_country_code": "BRA",
            "names": {
                "region_id": 439,
                "criterion_a": "A",
                "criterion_b": "B",
                "criterion_c": "C",
                "criterion_d": "D",
                "criterion_j": "80000",
                "admin_substitutions": ["Brasil BR"],
            },
            "poi_table_category_ids": {"restaurant": 1002},
        }

    def _init_seed_databases(self) -> None:
        # BMD
        with sqlite3.connect(self.db_paths["BMD.NDS"]) as db:
            db.execute("CREATE TABLE bmdTileTable (tileId INTEGER, format INTEGER, version INTEGER, data BLOB, checksum INTEGER, patternId INTEGER)")
            db.execute("CREATE TABLE bmdTilePatternTable (patternId INTEGER, pattern BLOB)")

        # NAME
        with sqlite3.connect(self.db_paths["NAME.NDS"]) as db:
            db.execute("CREATE TABLE namedObjectTable (namedObjectId INTEGER, stringCollId INTEGER, nameBlob BLOB)")

        # FTS
        with sqlite3.connect(self.db_paths["FTS.NDS"]) as db:
            db.execute("CREATE TABLE nameFtsTable (namedObjectId INTEGER, namedObjectClass INTEGER, mortonCode INTEGER, updateRegionId INTEGER, criterionA TEXT, criterionB TEXT, criterionC TEXT, criterionD TEXT, criterionI TEXT, criterionJ TEXT, searchTags TEXT)")
            db.execute("CREATE TABLE poiFtsTable (poiId INTEGER, mortonCode INTEGER, poiName TEXT, poiCategoryId TEXT, regionId INTEGER, updateRegionId INTEGER, criterionA TEXT, criterionB TEXT, criterionC TEXT, criterionD TEXT, searchTags TEXT)")
            db.execute("CREATE TABLE namedObject2HouseNRTable (namedObjectId INTEGER, houseNRId INTEGER)")
            db.execute("CREATE TABLE houseNR2NamedObjectTable (houseNRId INTEGER, namedObjectId INTEGER)")
            db.execute("CREATE TABLE ftsHouseNumberRangeTable (houseNumberRangeId INTEGER, namedObjectId INTEGER, rangeStart INTEGER, rangeEnd INTEGER)")
            db.execute("CREATE TABLE nameFtsSubstitutionTable (substitutionCode TEXT, displayString TEXT, phoneticString TEXT, updateRegionId INTEGER, priority INTEGER, weight INTEGER)")
            db.execute("CREATE TABLE poiFtsSubstitutionTable (substitutionCode TEXT, displayString TEXT, phoneticString TEXT, updateRegionId INTEGER, priority INTEGER, weight INTEGER)")

        # POI
        with sqlite3.connect(self.db_paths["POI.NDS"]) as db:
            db.execute("CREATE TABLE poiTable (poiId INTEGER, poiCategoryId INTEGER, mortonCode INTEGER, format INTEGER, col5 INTEGER, col6 INTEGER, col7 INTEGER, col8 INTEGER, col9 INTEGER, col10 INTEGER, col11 INTEGER, col12 INTEGER)")
            db.execute("CREATE TABLE poiVirtualTileTable (tileId INTEGER, level INTEGER, count INTEGER, col4 INTEGER, col5 INTEGER)")
            db.execute("CREATE TABLE poiToNameStringCollTable (poiId INTEGER, nameId INTEGER)")
            db.execute("CREATE TABLE poiNameStringCollTable (poiId INTEGER, col2 INTEGER, col3 INTEGER, col4 INTEGER, col5 INTEGER, name TEXT, col7 TEXT)")
            db.execute("CREATE TABLE poiLinkAccessTable (poiId INTEGER, linkId INTEGER)")
            db.execute("CREATE TABLE poiRelationTable (poiId INTEGER, relId INTEGER)")
            db.execute("CREATE TABLE poiNamedObjectRelationTable (poiId INTEGER, namedObjectId INTEGER)")

        # ROUTE
        with sqlite3.connect(self.db_paths["ROUTE.NDS"]) as db:
            db.execute("CREATE TABLE routingTileTable (tileId INTEGER, format INTEGER, data BLOB)")
            db.execute("CREATE TABLE routingAuxTileTable (tileId INTEGER, format INTEGER, col3 INTEGER, col4 INTEGER, col5 INTEGER, col6 INTEGER, data BLOB, col8 INTEGER, col9 INTEGER, col10 INTEGER, col11 INTEGER)")

        # SHARED
        with sqlite3.connect(self.db_paths["SHARED.NDS"]) as db:
            db.execute("CREATE TABLE levelMetadataTable (level INTEGER, buildingBlockId INTEGER, shift INTEGER, col4 INTEGER, col5 INTEGER, col6 INTEGER)")
            db.execute("CREATE TABLE regionMetadataTable (regionId INTEGER, isoCountryCode TEXT)")
            db.execute("INSERT INTO regionMetadataTable VALUES(439, 'ISR')")

        # PRODUCT
        with sqlite3.connect(self.db_paths["NAV/PRODUCT.NDS"]) as db:
            db.execute("CREATE TABLE tileContentIndexTable (updateRegionId INTEGER, buildingBlockId INTEGER, level INTEGER, sequence INTEGER, tileId INTEGER, col6 INTEGER, col7 INTEGER, col8 BLOB)")
            db.execute("CREATE TABLE updateRegionNameTable (updateRegionId INTEGER, nameString TEXT)")
            db.execute("INSERT INTO updateRegionNameTable VALUES(439, 'Old Name')")

        # ROOT
        with sqlite3.connect(self.db_paths["ROOT.NDS"]) as db:
            db.execute("CREATE TABLE productDbTable (uri TEXT PRIMARY KEY, internalName TEXT)")
            db.execute("INSERT INTO productDbTable VALUES('NAV/PRODUCT.NDS', 'Old Root Name')")

    def test_assemble_logical_sqlite_databases_success(self) -> None:
        manifest_path = self.root / "sqlite-manifest.json"
        manifest = assemble_logical_sqlite_databases(
            db_paths=self.db_paths,
            bmd_dir=self.bmd_dir,
            names_dir=self.names_dir,
            poi_dir=self.poi_dir,
            route_dir=self.route_dir,
            aux_dir=self.aux_dir,
            catalog_path=self.catalog_path,
            output_manifest_path=manifest_path,
            profile=self.profile,
        )

        self.assertTrue(manifest_path.is_file())
        self.assertEqual(manifest["step"], "sqlite.assemble")
        self.assertEqual(len(manifest["databases"]), 8)

        # 1. Verify PRAGMA quick_check passes on all generated databases
        for name, db_path in self.db_paths.items():
            with sqlite3.connect(str(db_path)) as db:
                run_quick_check(db, name)

        # 2. Verify dynamic profile bindings in SHARED, PRODUCT, and ROOT
        with sqlite3.connect(str(self.db_paths["SHARED.NDS"])) as db:
            cur = db.execute("SELECT isoCountryCode FROM regionMetadataTable WHERE regionId=439")
            self.assertEqual(cur.fetchone()[0], "BRA")

        with sqlite3.connect(str(self.db_paths["NAV/PRODUCT.NDS"])) as db:
            cur = db.execute("SELECT nameString FROM updateRegionNameTable WHERE updateRegionId=439")
            self.assertEqual(cur.fetchone()[0], "Curitiba v9 Test Package")

        with sqlite3.connect(str(self.db_paths["ROOT.NDS"])) as db:
            cur = db.execute("SELECT internalName FROM productDbTable WHERE uri='NAV/PRODUCT.NDS'")
            self.assertEqual(cur.fetchone()[0], "Curitiba v9 Test Package")

    def test_unsupported_or_missing_table_blocks(self) -> None:
        # Create a database missing bmdTileTable
        bad_bmd = self.db_dir / "bad_bmd.db"
        with sqlite3.connect(bad_bmd) as db:
            db.execute("CREATE TABLE someArbitraryTable (id INTEGER)")

        with self.assertRaises(SchemaMismatchError):
            verify_table_schema(sqlite3.connect(str(bad_bmd)), "BMD.NDS", EXPECTED_TABLES["BMD.NDS"])

    def test_coverage_and_crossrefs_mismatch_fails(self) -> None:
        # Populate databases successfully first
        manifest_path = self.root / "sqlite-manifest.json"
        assemble_logical_sqlite_databases(
            db_paths=self.db_paths,
            bmd_dir=self.bmd_dir,
            names_dir=self.names_dir,
            poi_dir=self.poi_dir,
            route_dir=self.route_dir,
            aux_dir=self.aux_dir,
            catalog_path=self.catalog_path,
            output_manifest_path=manifest_path,
            profile=self.profile,
        )

        # Create a catalog with mismatched BMD tile IDs
        mismatched_catalog = {
            "layer_coverage": {
                "bmd": {"level": 11, "shift": 4, "tile_ids": [99999999]},  # mismatched
                "route": {
                    "payload_level": 13,
                    "payload_shift": 4,
                    "index_level": 9,
                    "index_tile_ids": [self.route_index_tile],
                    "tile_ids": [self.tile_1, self.tile_2],
                },
            },
        }

        conns = {name: sqlite3.connect(str(p)) for name, p in self.db_paths.items()}
        try:
            with self.assertRaises(CoverageMismatchError):
                verify_crossrefs_and_coverage(conns, mismatched_catalog, self.profile)
        finally:
            for c in conns.values():
                c.close()


if __name__ == "__main__":
    unittest.main()
