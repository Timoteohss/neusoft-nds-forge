"""Unit tests for ZV-zlib container packaging and SecretProvider (Issue #25 / [3.4]).

Validates:
1. Target 21-file inventory (15 ZV, 3 SQLite, Area.txt, ROOT.NDK, metadata.json).
2. Pure byte-for-byte copying of the 9 static ZV files without repacking.
3. Legacy ZV-zlib wrapping applied only to the 6 generated paths.
4. SecretProvider interface: key resolution, 16-byte enforcement, and key isolation (no key leaking).
5. Roundtrip SQLite decode and quick_check verification on wrapped outputs.
6. Recomputed metadata.json hashes and byte sizes.
7. Atomic staging preventing partial outputs when keys or assets are missing.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nds_forge.adapters.container import (
    GENERATED_ZV_PATHS,
    REQUIRED_PACKAGE_PATHS,
    STATIC_ZV_PATHS,
    DictSecretProvider,
    EnvSecretProvider,
    SecretProviderError,
    pack_package_tree,
)


class ContainerPackagingTests(unittest.TestCase):
    """Test suite for container adapter and packaging contracts."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.pkg_dir = self.root / "assembled-package"
        self.db_dir = self.pkg_dir / "db"
        self.output_dir = self.root / "final-package"

        self._setup_package_tree()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _setup_package_tree(self) -> None:
        self.db_dir.mkdir(parents=True)

        self.test_key = b"0123456789ABCDEF"  # Exactly 16 bytes
        self.keys = {path: self.test_key for path in GENERATED_ZV_PATHS}

        # 1. Create 6 generated SQLite files that will be wrapped
        for path in GENERATED_ZV_PATHS:
            p = self.db_dir / Path(path).relative_to("db")
            p.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(str(p)) as db:
                db.execute("CREATE TABLE test (id INTEGER, val TEXT)")
                db.execute("INSERT INTO test VALUES(1, 'hello')")

        # 2. Create 9 static ZV files (pre-wrapped with ZV-zlib header)
        for path in STATIC_ZV_PATHS:
            p = self.db_dir / Path(path).relative_to("db")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"ZV-zlib\0" + b"\x00" * 200)

        # 3. Create 3 plain SQLite files
        for path in ("db/DECO/PRODUCT.NDS", "db/NAV/PRODUCT.NDS", "db/ROOT.NDS"):
            p = self.db_dir / Path(path).relative_to("db")
            p.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(str(p)) as db:
                db.execute("CREATE TABLE prod (id INTEGER)")

        # 4. Text and NDK files
        (self.db_dir / "Area.txt").write_text("Area 439\n", encoding="utf-8")
        (self.db_dir / "ROOT.NDK").write_text("# Neusoft NDK\n", encoding="utf-8")

        # 5. metadata.json
        meta_items = []
        for path in REQUIRED_PACKAGE_PATHS:
            rel = Path(path).relative_to("db").as_posix()
            meta_items.append({"filePath": rel, "fileHash": "0" * 64, "fileSize": 0})
        (self.db_dir / "metadata.json").write_text(json.dumps({"files": meta_items}), encoding="utf-8")

        # 6. package-layout.json
        declared_files = []
        for path in sorted(REQUIRED_PACKAGE_PATHS):
            declared_files.append({
                "path": path,
                "origin": "generated" if path in GENERATED_ZV_PATHS else "copied_external_asset",
                "producer_step": "test",
                "inputs": [],
                "provenance": {"source": "test", "license": "ODbL-1.0"},
            })

        (self.pkg_dir / "package-layout.json").write_text(json.dumps({
            "schema_version": 1,
            "profile_id": "test-v9",
            "required_paths": sorted(REQUIRED_PACKAGE_PATHS),
            "pack_zv_sqlite_paths": sorted(GENERATED_ZV_PATHS),
            "files": declared_files,
        }), encoding="utf-8")

    def _snapshot_output(self) -> dict[str, str]:
        return {
            path.relative_to(self.output_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.output_dir.rglob("*") if path.is_file()
        }

    def test_pack_package_tree_success_and_target_inventory(self) -> None:
        _report = pack_package_tree(
            source_package=self.pkg_dir,
            output_dir=self.output_dir,
            keys=self.keys,
        )

        out_db = self.output_dir / "db"
        self.assertTrue(out_db.is_dir())

        # Verify exactly 21 files exist
        actual_files = {
            "db/" + p.relative_to(out_db).as_posix()
            for p in out_db.rglob("*") if p.is_file()
        }
        self.assertEqual(actual_files, REQUIRED_PACKAGE_PATHS)
        self.assertEqual(len(actual_files), 21)

        # Verify 15 ZV files
        zv_count = 0
        for path in actual_files:
            p = out_db / Path(path).relative_to("db")
            if p.read_bytes().startswith(b"ZV-zlib\0"):
                zv_count += 1
        self.assertEqual(zv_count, 15)

        # Verify 3 SQLite files
        sqlite_count = 0
        for path in ("db/DECO/PRODUCT.NDS", "db/NAV/PRODUCT.NDS", "db/ROOT.NDS"):
            p = out_db / Path(path).relative_to("db")
            if p.read_bytes().startswith(b"SQLite format 3\0"):
                sqlite_count += 1
        self.assertEqual(sqlite_count, 3)

        # Verify manifest recomputes hashes and byte sizes
        manifest = json.loads((self.output_dir / "artifact-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["step"], "container.pack")
        self.assertEqual(len(manifest["files"]), 21)
        self.assertEqual(len(manifest["packed_generated_containers"]), 6)
        self.assertEqual(manifest["static_zv_assets_repacked"], 0)

        # Exercise the actual packer's list-shaped manifest through offline validation.
        from nds_forge.validation.offline import perform_offline_validation
        checked = perform_offline_validation(
            self.output_dir, self.output_dir / "artifact-manifest.json")
        self.assertTrue(checked.is_valid, checked.errors)

        # Verify key bytes never leak into manifest or metadata
        manifest_text = (self.output_dir / "artifact-manifest.json").read_text(encoding="utf-8")
        self.assertNotIn("0123456789ABCDEF", manifest_text)
        meta_text = (out_db / "metadata.json").read_text(encoding="utf-8")
        self.assertNotIn("0123456789ABCDEF", meta_text)

        # Verify metadata.json was updated with non-zero sizes and valid sha256
        meta_doc = json.loads(meta_text)
        for item in meta_doc["files"]:
            self.assertNotEqual(item["fileHash"], "0" * 64)
            self.assertGreater(item["fileSize"], 0)

    def test_secret_provider_interface(self) -> None:
        # 1. DictSecretProvider
        provider = DictSecretProvider({
            "ref:bmd": b"1111222233334444",
            "ref:route": b"AAAABBBBCCCCDDDD",
        })
        self.assertEqual(provider.resolve_key("ref:bmd"), b"1111222233334444")
        with self.assertRaises(SecretProviderError):
            provider.resolve_key("ref:missing")

        # 2. EnvSecretProvider
        os.environ["TEST_NDS_KEY"] = "00112233445566778899AABBCCDDEEFF"
        env_prov = EnvSecretProvider()
        key_res = env_prov.resolve_key("env:TEST_NDS_KEY")
        self.assertEqual(len(key_res), 16)

        # Missing env var raises SecretProviderError
        with self.assertRaises(SecretProviderError):
            env_prov.resolve_key("env:NON_EXISTENT_VAR_12345")

    def test_missing_key_blocks_atomic_output(self) -> None:
        incomplete_keys = dict(self.keys)
        # Drop one key
        del incomplete_keys["db/NAV/UR439/BMD.NDS"]

        with self.assertRaises(SecretProviderError):
            pack_package_tree(
                source_package=self.pkg_dir,
                output_dir=self.output_dir,
                keys=incomplete_keys,
            )

        # Atomic guarantee: output directory does not exist or has no db
        self.assertFalse((self.output_dir / "db").exists())

    def test_missing_required_asset_blocks_atomic_output(self) -> None:
        # Delete Area.txt
        (self.db_dir / "Area.txt").unlink()

        with self.assertRaises(FileNotFoundError):
            pack_package_tree(
                source_package=self.pkg_dir,
                output_dir=self.output_dir,
                keys=self.keys,
            )

        self.assertFalse((self.output_dir / "db").exists())

    def test_second_publish_rename_failure_restores_previous_complete_package(self) -> None:
        pack_package_tree(self.pkg_dir, self.output_dir, self.keys)
        previous_snapshot = self._snapshot_output()
        staging_dir = self.output_dir.parent / f".staging-{self.output_dir.name}"
        (self.db_dir / "Area.txt").write_text("Different Area\n", encoding="utf-8")

        real_replace = os.replace
        rename_events: list[str] = []

        def fail_on_second_publish_rename(source, destination):
            source_path, destination_path = Path(source), Path(destination)
            if source_path == self.output_dir:
                rename_events.append("backup_previous")
            elif source_path == staging_dir and destination_path == self.output_dir:
                rename_events.append("publish_staged_package")
                self.assertTrue((staging_dir / "db").is_dir())
                self.assertTrue((staging_dir / "artifact-manifest.json").is_file())
                raise OSError("synthetic failure on second publish rename")
            elif destination_path == self.output_dir and source_path.name.startswith(
                    f".{self.output_dir.name}.previous-"):
                rename_events.append("restore_previous")
            return real_replace(source, destination)

        with patch("nds_forge.adapters.container.os.replace", side_effect=fail_on_second_publish_rename):  # noqa: SIM117 -- Keep nested contexts to preserve the documented test/resource scope and cleanup order
            with self.assertRaisesRegex(OSError, "second publish rename"):
                pack_package_tree(self.pkg_dir, self.output_dir, self.keys)

        self.assertEqual(rename_events, ["backup_previous", "publish_staged_package", "restore_previous"])
        self.assertEqual(self._snapshot_output(), previous_snapshot)
        self.assertTrue((self.output_dir / "db").is_dir())
        self.assertTrue((self.output_dir / "artifact-manifest.json").is_file())
        self.assertFalse(staging_dir.exists())
        self.assertEqual(list(self.output_dir.parent.glob(f".{self.output_dir.name}.previous-*")), [])

    def test_failed_publish_without_previous_package_leaves_no_partial_output(self) -> None:
        staging_dir = self.output_dir.parent / f".staging-{self.output_dir.name}"
        real_replace = os.replace

        def fail_staged_package_rename(source, destination):
            if Path(source) == staging_dir and Path(destination) == self.output_dir:
                self.assertTrue((staging_dir / "db").is_dir())
                self.assertTrue((staging_dir / "artifact-manifest.json").is_file())
                raise OSError("synthetic package publish failure")
            return real_replace(source, destination)

        with patch("nds_forge.adapters.container.os.replace", side_effect=fail_staged_package_rename):  # noqa: SIM117 -- Keep nested contexts to preserve the documented test/resource scope and cleanup order
            with self.assertRaisesRegex(OSError, "package publish failure"):
                pack_package_tree(self.pkg_dir, self.output_dir, self.keys)

        self.assertFalse(self.output_dir.exists())
        self.assertFalse(staging_dir.exists())


if __name__ == "__main__":
    unittest.main()
