"""Unit tests for offline validators and capability matrix (Issue #36 / [6.1])."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from nds_forge.validation.offline import (
    InvalidValidationClaimError,
    ValidationTier,
    perform_offline_validation,
    sha256_file,
    validate_capability_matrix_claim,
    validate_sqlite_database,
)


class OfflineValidationTests(unittest.TestCase):
    """Test suite for offline artifact verification, SQLite checks, and capability boundaries."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.artifact_dir = self.root / "artifacts"
        self.artifact_dir.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_valid_offline_artifact_passes(self) -> None:
        # 1. Create a healthy SQLite database
        db_path = self.artifact_dir / "test.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE streets(id INTEGER PRIMARY KEY, name TEXT)")
            conn.execute("INSERT INTO streets VALUES(1, 'Rua XV')")
            conn.commit()

        # 2. Create another artifact file
        file_path = self.artifact_dir / "meta.txt"
        file_path.write_text("sample content", encoding="utf-8")

        # 3. Create valid manifest
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps({
            "step_id": "test.step",
            "output_hashes": {
                "test.db": sha256_file(db_path),
                "meta.txt": sha256_file(file_path),
            },
        }), encoding="utf-8")

        report = perform_offline_validation(
            artifact_dir=self.artifact_dir,
            manifest_path=manifest_path,
            sqlite_checks={"test.db": ["streets"]},
            is_synthetic=True,
            claimed_tier=ValidationTier.GENERATED_OFFLINE.value,
        )

        self.assertTrue(report.is_valid)
        self.assertEqual(report.tier, "generated_offline")
        self.assertEqual(len(report.errors), 0)

    def test_corrupted_sqlite_fails_integrity(self) -> None:
        db_path = self.artifact_dir / "corrupted.db"
        # Write 200 bytes of garbage that claims to be SQLite
        db_path.write_bytes(b"SQLite format 3\x00" + b"\xff" * 200)

        errs = validate_sqlite_database(db_path)
        self.assertTrue(len(errs) > 0)
        self.assertTrue(any("SQLite" in e for e in errs))

    def test_missing_expected_table_fails(self) -> None:
        db_path = self.artifact_dir / "empty.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE foo(id INTEGER)")

        errs = validate_sqlite_database(db_path, expected_tables=["foo", "missing_table"])
        self.assertEqual(len(errs), 1)
        self.assertIn("Missing expected tables in empty.db: ['missing_table']", errs[0])

    def test_hash_mismatch_fails_validation(self) -> None:
        file_path = self.artifact_dir / "data.bin"
        file_path.write_bytes(b"actual content")

        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps({
            "step_id": "test.hash",
            "output_hashes": {
                "data.bin": "0000000000000000000000000000000000000000000000000000000000000000",
            },
        }), encoding="utf-8")

        report = perform_offline_validation(
            artifact_dir=self.artifact_dir,
            manifest_path=manifest_path,
        )
        self.assertFalse(report.is_valid)
        self.assertTrue(any("Hash mismatch" in e for e in report.errors))

    def test_missing_file_fails_validation(self) -> None:
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps({
            "step_id": "test.missing",
            "output_hashes": {
                "does_not_exist.bin": "abc",
            },
        }), encoding="utf-8")

        report = perform_offline_validation(
            artifact_dir=self.artifact_dir,
            manifest_path=manifest_path,
        )
        self.assertFalse(report.is_valid)
        self.assertTrue(any("Missing expected artifact file" in e for e in report.errors))

    def test_osm_provenance_and_odbl_enforcement(self) -> None:
        # OSM artifact lacking attribution and license
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps({
            "step_id": "osm.clip",
            "source_type": "osm",
            "output_hashes": {},
        }), encoding="utf-8")

        report = perform_offline_validation(
            artifact_dir=self.artifact_dir,
            manifest_path=manifest_path,
        )
        self.assertFalse(report.is_valid)
        self.assertTrue(any("OpenStreetMap attribution" in e for e in report.errors))
        self.assertTrue(any("ODbL license" in e for e in report.errors))

    def test_synthetic_claiming_real_validated_is_strictly_rejected(self) -> None:
        forbidden_claims = [
            "real validated",
            "real_validated",
            "in_car_validated",
            "vehicle_accepted",
        ]
        for claim in forbidden_claims:
            with self.subTest(claim=claim):
                with self.assertRaises(InvalidValidationClaimError) as ctx:
                    validate_capability_matrix_claim(claim, is_synthetic_or_offline=True)
                self.assertIn("Synthetic or offline fixtures must never be promoted to", str(ctx.exception))

    def test_valid_tiers_accepted(self) -> None:
        valid_tiers = [
            "generated_offline",
            "native_parser",
            "sdk_evaluation",
            "ui_preview",
            "guidance_in_car",
            "partial",
            "unvalidated",
        ]
        for t in valid_tiers:
            with self.subTest(tier=t):
                self.assertEqual(validate_capability_matrix_claim(t, is_synthetic_or_offline=True), t)


if __name__ == "__main__":
    unittest.main()
