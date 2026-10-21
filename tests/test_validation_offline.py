"""Unit tests for offline validators and capability matrix (Issue #36 / [6.1])."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from nds_forge.validation.offline import (
    InvalidValidationClaimError,
    ValidationTier,
    perform_offline_validation,
    sha256_file,
    validate_capability_matrix_claim,
    validate_provenance_and_licensing,
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

    def _validate_manifest(self, metadata):
        file_path = self.artifact_dir / "fixture.bin"
        file_path.write_bytes(b"synthetic provenance fixture")
        manifest = {"output_hashes": {"fixture.bin": sha256_file(file_path)}, **metadata}
        if "files" in metadata:
            del manifest["output_hashes"]
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        return perform_offline_validation(self.artifact_dir, manifest_path)

    def test_missing_provenance_is_not_assessed_instead_of_verified(self):
        report = self._validate_manifest({"step_id": "synthetic.fixture"})
        self.assertTrue(report.is_valid, report.errors)
        self.assertNotIn("Provenance and license declarations verified", report.passed_checks)
        self.assertTrue(any("not assessed" in warning for warning in report.warnings))
        self.assertEqual(report.details["provenance"]["status"], "not_assessed")
        self.assertEqual(report.details["provenance"]["checked_items"], 0)
        result = validate_provenance_and_licensing({"step_id": "synthetic.fixture"})
        self.assertFalse(result.applicable)
        self.assertEqual(result.errors, [])

    def test_applicable_osm_declarations_are_checked_without_tier_promotion(self):
        for metadata in ({"step_id": "osm.clip"}, {"source_type": "osm"}):
            with self.subTest(metadata=metadata):
                report = self._validate_manifest(metadata)
                self.assertFalse(report.is_valid)
                self.assertTrue(any("OpenStreetMap attribution" in error for error in report.errors))
                self.assertTrue(any("ODbL license" in error for error in report.errors))
        report = self._validate_manifest({
            "source_type": "osm", "attribution": "© OpenStreetMap contributors", "license": "ODbL-1.0"})
        self.assertTrue(report.is_valid, report.errors)
        self.assertEqual(report.details["provenance"]["status"], "declarations_checked")
        self.assertEqual(report.details["provenance"]["checked_items"], 1)
        self.assertEqual(report.tier, "generated_offline")

    def test_osm_source_block_declarations_are_assessed(self):
        report = self._validate_manifest({"step": "osm.clip", "source": {
            "attribution": "© OpenStreetMap contributors", "license": "ODbL-1.0"}})
        self.assertTrue(report.is_valid, report.errors)
        self.assertEqual(report.details["provenance"]["status"], "declarations_checked")
        self.assertTrue(any("declarations only" in warning for warning in report.warnings))

    def test_explicit_provenance_record_requires_source_and_license(self):
        for provenance in ({"source": "synthetic source", "license": "synthetic-license"},
                           {"license": "synthetic-license"}):
            report = self._validate_manifest({"provenance": provenance})
            with self.subTest(provenance=provenance):
                self.assertEqual(report.is_valid, "source" in provenance)
                self.assertTrue(report.details["provenance"]["applicable"])

    def _container_records(self):
        records = []
        for name, provenance in [
            ("copied.bin", {"source": "synthetic external asset", "license": "synthetic-license"}),
            ("osm.bin", {"source": "OSM road geometry", "license": "ODbL-1.0; © OpenStreetMap contributors"}),
        ]:
            path = self.artifact_dir / name
            path.write_bytes(b"synthetic package provenance fixture")
            records.append({"path": name, "sha256": sha256_file(path), "provenance": provenance})
        return records

    def test_container_provenance_is_checked_for_every_file(self):
        records = self._container_records()
        report = self._validate_manifest({"step": "container.pack", "files": records})
        self.assertTrue(report.is_valid, report.errors)
        self.assertEqual(report.details["provenance"]["checked_items"], 2)
        self.assertEqual(report.details["provenance"]["status"], "declarations_checked")
        self.assertEqual(report.tier, "generated_offline")
        for provenance in (None, {}, {"source": "synthetic"},
                           {"source": "synthetic", "license": " "},
                           {"source": "synthetic", "license": []}):
            records[1]["provenance"] = provenance
            with self.subTest(provenance=provenance):
                report = self._validate_manifest({"step": "container.pack", "files": records})
                self.assertFalse(report.is_valid)
                self.assertTrue(any("osm.bin" in error for error in report.errors))
                self.assertNotIn("Provenance and license declarations verified", report.passed_checks)

    def test_container_osm_file_requires_odbl_and_attribution(self):
        for license_text in ("ODbL-1.0", "© OpenStreetMap contributors"):
            records = self._container_records()
            records[1]["provenance"]["license"] = license_text
            report = self._validate_manifest({"step": "container.pack", "files": records})
            with self.subTest(license=license_text):
                self.assertFalse(report.is_valid)
                self.assertTrue(any("osm.bin" in error for error in report.errors))

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
                "does_not_exist.bin": "a" * 64,
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

    def test_offline_cannot_claim_runtime_or_vehicle_tiers(self) -> None:
        for tier in ("native_parser", "sdk_evaluation", "ui_preview", "guidance_in_car"):
            with self.subTest(tier=tier):
                with self.assertRaises(InvalidValidationClaimError):
                    validate_capability_matrix_claim(tier, is_synthetic_or_offline=True)
                report = perform_offline_validation(
                    self.artifact_dir, is_synthetic=False, claimed_tier=tier)
                self.assertFalse(report.is_valid)
                self.assertEqual(report.tier, "rejected")

    def test_offline_tiers_accepted(self) -> None:
        for tier in ("generated_offline", "partial", "unvalidated"):
            self.assertEqual(validate_capability_matrix_claim(tier), tier)

    def test_final_package_manifest_list_roundtrip_and_tamper(self) -> None:
        file_path = self.artifact_dir / "db" / "Area.txt"
        file_path.parent.mkdir()
        file_path.write_bytes(b"synthetic package bytes")
        manifest_path = self.root / "artifact-manifest.json"
        manifest_path.write_text(json.dumps({"files": [{
            "path": "db/Area.txt", "bytes": file_path.stat().st_size,
            "sha256": sha256_file(file_path),
        }]}))
        report = perform_offline_validation(self.artifact_dir, manifest_path)
        self.assertTrue(report.is_valid, report.errors)
        file_path.write_bytes(b"corrupted")
        report = perform_offline_validation(self.artifact_dir, manifest_path)
        self.assertFalse(report.is_valid)
        self.assertTrue(any("Hash mismatch" in error for error in report.errors))

    def test_missing_or_empty_manifest_cannot_pass(self) -> None:
        self.assertFalse(perform_offline_validation(self.artifact_dir).is_valid)
        path = self.root / "manifest.json"
        for manifest in ({}, {"files": []}, {"output_hashes": {}}, []):
            path.write_text(json.dumps(manifest))
            with self.subTest(manifest=manifest):
                self.assertFalse(perform_offline_validation(self.artifact_dir, path).is_valid)

    def test_final_manifest_duplicate_or_unsafe_paths_fail(self) -> None:
        path = self.root / "manifest.json"
        for records in ([{"path": "../outside", "sha256": "a" * 64}],
                        [{"path": "db/Area.txt", "sha256": "a" * 64}] * 2):
            path.write_text(json.dumps({"files": records}))
            self.assertFalse(perform_offline_validation(self.artifact_dir, path).is_valid)


if __name__ == "__main__":
    unittest.main()
