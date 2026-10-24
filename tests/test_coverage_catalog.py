"""Unit tests for the generic per-layer coverage catalog builder.

Validates:
1. Missing and extra tile detection between interdependent layers (ROUTE vs AUX).
2. Stale reference detection (SHA-256 mismatch or profile mismatch).
3. Bad parent-child index containment detection and orphan index tile detection.
4. Graceful handling of empty optional layers vs fail-closed required layers.
5. SQLite catalog index generation and PRAGMA integrity verification.
6. Deterministic JSON report structure.
7. Support for arbitrary profiles beyond Curitiba (different levels/shifts/index levels).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from nds_forge.layers.bmd_codec import tile_for
from nds_forge.layers.catalog import (
    CatalogError,
    EmptyLayerError,
    ExtraTileError,
    MissingTileError,
    ParentChildCoverageError,
    SQLiteIntegrityError,
    StaleReferenceError,
    build_coverage_catalogs,
    compute_parent_index_tiles,
    verify_sqlite_catalog_integrity,
)


class CoverageCatalogBuilderTests(unittest.TestCase):
    """Test suite for layer coverage catalog builder contracts."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.bmd_dir = self.root / "bmd"
        self.names_dir = self.root / "names"
        self.poi_dir = self.root / "poi"
        self.route_dir = self.root / "route"
        self.aux_dir = self.root / "aux"
        self.output_dir = self.root / "catalogs"

        for d in (self.bmd_dir, self.names_dir, self.poi_dir, self.route_dir, self.aux_dir, self.output_dir):
            d.mkdir(parents=True, exist_ok=True)

        self._setup_valid_fixtures()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _setup_valid_fixtures(self) -> None:
        # Curitiba tiles around (-49.27, -25.43): level 13 tile is e.g. 540673410
        # Let's use realistic tiles: tile 540673410 at level 13
        # BMD tiles: level 11 tile e.g. 135168352
        self.tile_id_1 = 540673410
        self.tile_id_2 = 540673411

        (self.bmd_dir / "bmd-manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "tiles": [{"tile_id": 135168352}],
        }), encoding="utf-8")

        (self.names_dir / "name-records.json").write_text(json.dumps({
            "schema_version": 1,
            "counts": {"street_records": 10, "admin_records": 4},
        }), encoding="utf-8")

        (self.poi_dir / "poi-records.json").write_text(json.dumps({
            "schema_version": 1,
            "counts": {"accepted": 5},
            "records": [{"id": ident, "tile_id": 135168352} for ident in range(1, 6)],
        }), encoding="utf-8")

        (self.route_dir / "route-manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "level": 13,
            "shift": 4,
            "source_sha256": "abcdef1234567890",
            "tiles": [{"id": self.tile_id_1}, {"id": self.tile_id_2}],
            "links": 50,
            "intersections": 30,
        }), encoding="utf-8")

        (self.aux_dir / "aux-manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "profile_id": "test-curitiba-profile",
            "tiles": [{"tile_id": self.tile_id_1}, {"tile_id": self.tile_id_2}],
            "counts": {"geometry_rows": 50},
        }), encoding="utf-8")

        self.curitiba_profile = {
            "profile_id": "test-curitiba-profile",
            "region_id": 439,
            "source_sha256": "abcdef1234567890",
            "grid": {
                "route_level": 13,
                "route_shift": 4,
                "bmd": {"level": 11, "shift": 4},
            },
            "index_levels": {"route": 9},
        }

    def test_valid_curitiba_profile_builds_deterministic_report(self) -> None:
        _report = build_coverage_catalogs(
            bmd_dir=self.bmd_dir,
            names_dir=self.names_dir,
            poi_dir=self.poi_dir,
            route_dir=self.route_dir,
            aux_dir=self.aux_dir,
            output_dir=self.output_dir,
            profile=self.curitiba_profile,
        )

        catalog_json = self.output_dir / "catalogs.json"
        self.assertTrue(catalog_json.is_file())
        doc = json.loads(catalog_json.read_text(encoding="utf-8"))

        self.assertEqual(doc["profile_id"], "test-curitiba-profile")
        self.assertEqual(doc["region_id"], 439)
        self.assertEqual(doc["counts"]["route_tiles"], 2)
        self.assertEqual(doc["counts"]["bmd_tiles"], 1)
        self.assertEqual(doc["counts"]["poi_records"], 5)
        self.assertIn("route", doc["layer_coverage"])
        self.assertEqual(doc["layer_coverage"]["route"]["tile_ids"], [self.tile_id_1, self.tile_id_2])
        self.assertGreater(len(doc["layer_coverage"]["route"]["index_tile_ids"]), 0)

        # SQLite index was compiled and is verifiable
        sqlite_db = self.output_dir / "catalog-index.db"
        self.assertTrue(sqlite_db.is_file())
        verify_sqlite_catalog_integrity(sqlite_db)

    def test_missing_and_extra_tile_detection(self) -> None:
        # Case 1: AUX missing a tile present in ROUTE
        (self.aux_dir / "aux-manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "profile_id": "test-curitiba-profile",
            "tiles": [{"tile_id": self.tile_id_1}],  # missing self.tile_id_2
            "counts": {"geometry_rows": 25},
        }), encoding="utf-8")

        with self.assertRaises(MissingTileError):
            build_coverage_catalogs(
                bmd_dir=self.bmd_dir,
                names_dir=self.names_dir,
                poi_dir=self.poi_dir,
                route_dir=self.route_dir,
                aux_dir=self.aux_dir,
                output_dir=self.output_dir,
                profile=self.curitiba_profile,
            )

        # Case 2: AUX has an extra tile not in ROUTE
        (self.aux_dir / "aux-manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "profile_id": "test-curitiba-profile",
            "tiles": [{"tile_id": self.tile_id_1}, {"tile_id": self.tile_id_2}, {"tile_id": 999999}],
            "counts": {"geometry_rows": 75},
        }), encoding="utf-8")

        with self.assertRaises(ExtraTileError):
            build_coverage_catalogs(
                bmd_dir=self.bmd_dir,
                names_dir=self.names_dir,
                poi_dir=self.poi_dir,
                route_dir=self.route_dir,
                aux_dir=self.aux_dir,
                output_dir=self.output_dir,
                profile=self.curitiba_profile,
            )

    def test_stale_reference_detection(self) -> None:
        # Mismatched source SHA-256
        bad_source_profile = dict(self.curitiba_profile)
        bad_source_profile["source_sha256"] = "different_hash_000000"

        with self.assertRaises(StaleReferenceError):
            build_coverage_catalogs(
                bmd_dir=self.bmd_dir,
                names_dir=self.names_dir,
                poi_dir=self.poi_dir,
                route_dir=self.route_dir,
                aux_dir=self.aux_dir,
                output_dir=self.output_dir,
                profile=bad_source_profile,
            )

        # Mismatched profile_id on AUX
        (self.aux_dir / "aux-manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "profile_id": "stale-profile-v8",
            "tiles": [{"tile_id": self.tile_id_1}, {"tile_id": self.tile_id_2}],
            "counts": {"geometry_rows": 50},
        }), encoding="utf-8")

        with self.assertRaises(StaleReferenceError):
            build_coverage_catalogs(
                bmd_dir=self.bmd_dir,
                names_dir=self.names_dir,
                poi_dir=self.poi_dir,
                route_dir=self.route_dir,
                aux_dir=self.aux_dir,
                output_dir=self.output_dir,
                profile=self.curitiba_profile,
            )

    def test_parent_child_coverage_validation(self) -> None:
        # Calculate actual parent for tile_id_1 at level 13 with index level 9
        computed_parents = compute_parent_index_tiles([self.tile_id_1], payload_level=13, index_level=9)
        real_parent = computed_parents[0]

        # Case 1: Declared index tiles omit real parent
        with self.assertRaises(ParentChildCoverageError):
            compute_parent_index_tiles(
                [self.tile_id_1],
                payload_level=13,
                index_level=9,
                declared_index_tiles=[real_parent + 100],  # wrong parent
            )

        # Case 2: Declared index tiles contain orphan with no child tiles
        with self.assertRaises(ParentChildCoverageError):
            compute_parent_index_tiles(
                [self.tile_id_1],
                payload_level=13,
                index_level=9,
                declared_index_tiles=[real_parent, real_parent + 50],  # orphan
            )

    def test_empty_optional_layer_graceful_handling(self) -> None:
        # When POI layer is empty and marked optional
        (self.poi_dir / "poi-records.json").write_text(json.dumps({
            "schema_version": 1,
            "counts": {"accepted": 0},
            "records": [],
        }), encoding="utf-8")

        _report = build_coverage_catalogs(
            bmd_dir=self.bmd_dir,
            names_dir=self.names_dir,
            poi_dir=self.poi_dir,
            route_dir=self.route_dir,
            aux_dir=self.aux_dir,
            output_dir=self.output_dir,
            profile=self.curitiba_profile,
        )

        catalog_json = self.output_dir / "catalogs.json"
        doc = json.loads(catalog_json.read_text(encoding="utf-8"))
        self.assertEqual(doc["layer_coverage"]["poi"]["record_count"], 0)
        self.assertEqual(doc["layer_coverage"]["poi"]["status"], "empty_optional")
        self.assertEqual(doc["layer_coverage"]["poi"]["tile_ids"], [])

    def test_empty_required_layer_fails(self) -> None:
        # When BMD layer is required and has 0 tiles
        (self.bmd_dir / "bmd-manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "tiles": [],
        }), encoding="utf-8")

        with self.assertRaises(EmptyLayerError):
            build_coverage_catalogs(
                bmd_dir=self.bmd_dir,
                names_dir=self.names_dir,
                poi_dir=self.poi_dir,
                route_dir=self.route_dir,
                aux_dir=self.aux_dir,
                output_dir=self.output_dir,
                profile=self.curitiba_profile,
            )

    def _build_poi_coverage(self, records, bmd_tiles, *, accepted=None, profile=None, full=False):
        (self.bmd_dir / "bmd-manifest.json").write_text(json.dumps({
            "schema_version": 1, "tiles": [{"tile_id": tile} for tile in bmd_tiles],
        }))
        (self.poi_dir / "poi-records.json").write_text(json.dumps({
            "schema_version": 1, "records": records,
            "counts": {"accepted": len(records) if accepted is None else accepted},
        }))
        build_coverage_catalogs(
            self.bmd_dir, self.names_dir, self.poi_dir, self.route_dir,
            self.aux_dir, self.output_dir, profile or self.curitiba_profile)
        document = json.loads((self.output_dir / "catalogs.json").read_text())
        return document if full else document["layer_coverage"]["poi"]

    def test_sparse_osm_pois_only_cover_their_own_sorted_unique_tiles(self):
        coverage = self._build_poi_coverage(
            [{"id": 1, "tile_id": 135168354}, {"id": 2, "tile_id": "135168352"},
             {"id": 3, "tile_id": 135168354}], [135168352, 135168353, 135168354])
        self.assertEqual(coverage["tile_ids"], [135168352, 135168354])
        self.assertEqual(coverage["record_count"], 3)

    def test_curated_coordinates_and_osm_tile_ids_can_share_coverage(self):
        lon, lat = -49.27, -25.43
        expected_tile = tile_for(lon, lat, self.curitiba_profile["grid"]["bmd"]["level"])
        other_tile = expected_tile + 1
        coverage = self._build_poi_coverage([
            {"id": 1, "longitude": lon, "latitude": lat},
            {"id": 2, "tile_id": other_tile},
        ], [expected_tile, other_tile, other_tile + 1])
        self.assertEqual(coverage["tile_ids"], sorted([expected_tile, other_tile]))

    def test_curated_only_coordinates_use_configured_payload_level(self):
        lon, lat = -49.27, -25.43
        self.curitiba_profile["grid"]["bmd"]["level"] = 10
        tile = tile_for(lon, lat, 10)
        coverage = self._build_poi_coverage(
            [{"id": 1, "longitude": str(lon), "latitude": str(lat)}], [tile, tile + 1])
        self.assertEqual(coverage["tile_ids"], [tile])

    def test_poi_outside_bmd_coverage_is_rejected(self):
        for record in ({"tile_id": 135168353}, {"longitude": 0, "latitude": 0}):
            with self.subTest(record=record), self.assertRaises(CatalogError):
                self._build_poi_coverage([record], [135168352])

    def test_explicit_policy_excludes_outside_pois_from_coverage_but_keeps_audit(self):
        profile = {**self.curitiba_profile, "poi": {"outside_bmd_policy": "exclude"}}
        document = self._build_poi_coverage(
            [{"id": 1, "tile_id": 135168353}, {"id": 2, "tile_id": 135168352}],
            [135168352], profile=profile, full=True)
        poi = document["layer_coverage"]["poi"]
        self.assertEqual(poi["tile_ids"], [135168352])
        self.assertEqual(poi["outside_bmd_policy"], "exclude")
        self.assertEqual(poi["outside_bmd_count"], 1)
        self.assertEqual(poi["outside_bmd_records"][0]["id"], 1)
        self.assertEqual(poi["outside_bmd_records"][0]["tile_id"], 135168353)
        self.assertEqual(document["counts"]["poi_outside_bmd"], 1)

    def test_missing_or_invalid_poi_location_is_rejected(self):
        invalid = [
            {}, {"longitude": 1}, {"longitude": None, "latitude": 0},
            {"longitude": "bad", "latitude": 0}, {"longitude": 181, "latitude": 0},
            {"longitude": 0, "latitude": -91}, {"longitude": float("nan"), "latitude": 0},
            {"longitude": float("inf"), "latitude": 0}, {"longitude": True, "latitude": 0},
            {"tile_id": None}, {"tile_id": "bad"}, {"tile_id": True}, {"tile_id": 135168352.5},
        ]
        for record in invalid:
            with self.subTest(record=record), self.assertRaises(CatalogError):
                self._build_poi_coverage([record], [135168352])

    def test_inconsistent_poi_count_cannot_hide_records(self):
        with self.assertRaises(CatalogError):
            self._build_poi_coverage([{"tile_id": 135168352}], [135168352], accepted=0)

    def test_sqlite_integrity_check(self) -> None:
        db_path = self.output_dir / "corrupted.db"
        # Write corrupted header / file
        db_path.write_bytes(b"SQLite format 3\0" + b"\xff" * 200)

        with self.assertRaises(SQLiteIntegrityError):
            verify_sqlite_catalog_integrity(db_path)

    def test_arbitrary_profile_with_different_levels(self) -> None:
        # Test that catalog builder works with other profiles (e.g. route level 12, index 8)
        arbitrary_profile = {
            "profile_id": "custom-regional-profile",
            "region_id": 999,
            "grid": {
                "route_level": 12,
                "route_shift": 3,
                "bmd": {"level": 10, "shift": 3},
            },
            "index_levels": {"route": 8},
        }

        # Update manifests to match custom levels
        (self.route_dir / "route-manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "level": 12,
            "shift": 3,
            "tiles": [{"id": self.tile_id_1}],
            "links": 10,
            "intersections": 5,
        }), encoding="utf-8")

        (self.aux_dir / "aux-manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "profile_id": "custom-regional-profile",
            "tiles": [{"tile_id": self.tile_id_1}],
            "counts": {"geometry_rows": 10},
        }), encoding="utf-8")

        _report = build_coverage_catalogs(
            bmd_dir=self.bmd_dir,
            names_dir=self.names_dir,
            poi_dir=self.poi_dir,
            route_dir=self.route_dir,
            aux_dir=self.aux_dir,
            output_dir=self.output_dir,
            profile=arbitrary_profile,
        )

        doc = json.loads((self.output_dir / "catalogs.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["profile_id"], "custom-regional-profile")
        self.assertEqual(doc["layer_coverage"]["route"]["payload_level"], 12)
        self.assertEqual(doc["layer_coverage"]["route"]["index_level"], 8)


if __name__ == "__main__":
    unittest.main()
