from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tests.e2e.package_inventory import InventoryError, load_contract, validate_package


class PackageInventoryTests(unittest.TestCase):
    def _synthetic_package(self, root: Path) -> tuple[Path, Path]:
        contract = load_contract()
        package = root / "synthetic-package"
        package.mkdir()
        records = []
        for index, (relative, origin) in enumerate(contract["files"].items()):
            path = package / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            format_name = contract["formats"][relative]
            payload = {
                "zv_zlib": b"ZV-zlib\0SYNTHETIC TEST DATA ONLY\n",
                "sqlite3": b"SQLite format 3\0SYNTHETIC TEST DATA ONLY\n",
                "ndk_text": b"# Neusoft NDK synthetic fixture\n",
                "text": f"SYNTHETIC TEST DATA ONLY {index}\n".encode(),
                "json": b'{"synthetic_fixture":true}\n',
            }[format_name]
            path.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            records.append({
                "path": relative,
                "origin": origin,
                "format": format_name,
                "producer_step": "fixture.synthetic",
                "bytes": len(payload),
                "sha256": digest,
                "inputs": [{"artifact": "fixture.input", "path": "fixture.json",
                            "sha256": hashlib.sha256(b"fixture").hexdigest()}],
                "provenance": {"source": "synthetic test fixture",
                               "license": "CC0-1.0"},
            })
        manifest = root / "artifact-manifest.json"
        manifest.write_text(json.dumps({
            "schema_version": 1,
            "profile_id": contract["profile_id"],
            "target": "container.pack",
            "execution_status": "completed",
            "validation_status": "unvalidated",
            "files": records,
        }), encoding="utf-8")
        return package, manifest

    def test_inventory_fixture_has_exact_21_paths_and_role_counts(self) -> None:
        files = load_contract()["files"]
        self.assertEqual(len(files), 21)
        self.assertEqual(list(files.values()).count("generated"), 5)
        self.assertEqual(list(files.values()).count("derived"), 4)
        self.assertEqual(list(files.values()).count("copied_external_asset"), 12)

    def test_synthetic_manifest_checks_inventory_lineage_and_real_file_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package, manifest = self._synthetic_package(Path(directory))
            report = validate_package(package, manifest)
            self.assertEqual(report["result"], "inventory_and_hashes_pass")
            self.assertEqual(report["files_verified"], 21)
            self.assertEqual(report["compatibility"], "not_assessed")
            self.assertEqual(report["validation_status"], "unvalidated")
            self.assertEqual(report["by_format"], {
                "zv_zlib": 15, "sqlite3": 3, "ndk_text": 1, "text": 1, "json": 1,
            })

    def test_wrong_hash_or_role_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package, manifest_path = self._synthetic_package(Path(directory))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"][0]["sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(InventoryError, "do not match"):
                validate_package(package, manifest_path)

    def test_validation_cannot_be_promoted_by_package_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package, manifest_path = self._synthetic_package(Path(directory))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["validation_status"] = "validated"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(InventoryError, "must not claim external validation"):
                validate_package(package, manifest_path)


if __name__ == "__main__":
    unittest.main()
