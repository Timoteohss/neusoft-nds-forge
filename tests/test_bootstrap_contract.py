from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from nds_forge.bootstrap import BootstrapError, fetch_bundles
from nds_forge.config import load_config
from nds_forge.pipeline import PipelineError, _resolve_secret_provider


class BootstrapContractTests(unittest.TestCase):
    """Offline contracts for release inputs; archives here are synthetic only."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config_path = self.root / "profile.toml"
        self.config_path.write_text(
            'schema_version = 1\nproject_id = "synthetic-bootstrap"\n'
            'workspace = "./work"\n\n'
            '[region]\nid = "test"\nname = "test"\nsource = "bbox"\n'
            'bbox = [0.0, 0.0, 1.0, 1.0]\n\n'
            '[source]\nkind = "pbf"\npath = "./unused.pbf"\n'
            'license = "ODbL-1.0"\nattribution = "synthetic"\n\n'
            '[pipeline]\ntarget = "source.acquire"\n',
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _archive(entries: dict[str, bytes]) -> bytes:
        payloads = {name: data for name, data in entries.items() if name != "manifest.json"}
        manifest = {
            "schema_version": 1,
            "files": [
                {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                for name, data in payloads.items()
            ],
        }
        all_entries = {"manifest.json": json.dumps(manifest).encode(), **entries}
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            for name, data in all_entries.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        return output.getvalue()

    def _bundle(self, archive: bytes, destination: str = "inputs/bundle") -> list[dict]:
        return [{
            "id": "synthetic-release-fixture",
            "url": "https://example.invalid/synthetic.tar.gz",
            "sha256": hashlib.sha256(archive).hexdigest(),
            "destination": destination,
        }]

    def test_network_opt_in_is_required_before_any_filesystem_or_network_effect(self) -> None:
        destination = self.root / "inputs" / "bundle"
        with patch("nds_forge.bootstrap.urllib.request.urlopen") as urlopen:  # noqa: SIM117 -- Keep nested contexts to preserve the documented test/resource scope and cleanup order
            with self.assertRaisesRegex(BootstrapError, "allow-network"):
                fetch_bundles(self.config_path, self._bundle(b"irrelevant"), False)
        urlopen.assert_not_called()
        self.assertFalse(destination.exists())
        self.assertFalse(destination.parent.exists())

    def test_valid_bundle_is_verified_and_idempotently_reused(self) -> None:
        archive = self._archive({"asset.txt": b"SYNTHETIC RELEASE FIXTURE\n"})
        destination = self.root / "inputs" / "bundle"
        with patch("nds_forge.bootstrap.urllib.request.urlopen", return_value=io.BytesIO(archive)) as urlopen:
            first = fetch_bundles(self.config_path, self._bundle(archive), True)
            second = fetch_bundles(self.config_path, self._bundle(archive), True)
        self.assertFalse(first["bundles"][0]["reused"])
        self.assertTrue(second["bundles"][0]["reused"])
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual((destination / "asset.txt").read_bytes(), b"SYNTHETIC RELEASE FIXTURE\n")
        marker = (destination / ".nds-forge-archive-sha256").read_text().strip()
        self.assertEqual(marker, hashlib.sha256(archive).hexdigest())

    def test_archive_hash_mismatch_does_not_publish_destination(self) -> None:
        archive = self._archive({"asset.txt": b"fixture"})
        bundle = self._bundle(archive)
        bundle[0]["sha256"] = "0" * 64
        with patch("nds_forge.bootstrap.urllib.request.urlopen", return_value=io.BytesIO(archive)):  # noqa: SIM117 -- Keep nested contexts to preserve the documented test/resource scope and cleanup order
            with self.assertRaisesRegex(BootstrapError, "SHA-256 divergente"):
                fetch_bundles(self.config_path, bundle, True)
        self.assertFalse((self.root / "inputs" / "bundle").exists())

    def test_manifest_member_hash_mismatch_does_not_publish_destination(self) -> None:
        archive = self._archive({"asset.txt": b"synthetic member"})
        # Keep the archive pin valid but corrupt its internal manifest's file digest.
        tampered = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as source:
            records = {m.name: source.extractfile(m).read() for m in source.getmembers() if m.isfile()}
        manifest = json.loads(records["manifest.json"])
        manifest["files"][0]["sha256"] = "f" * 64
        records["manifest.json"] = json.dumps(manifest).encode()
        with tarfile.open(fileobj=tampered, mode="w:gz") as target:
            for name, data in records.items():
                info = tarfile.TarInfo(name); info.size = len(data)
                target.addfile(info, io.BytesIO(data))
        invalid_archive = tampered.getvalue()
        with patch("nds_forge.bootstrap.urllib.request.urlopen", return_value=io.BytesIO(invalid_archive)):  # noqa: SIM117 -- Keep nested contexts to preserve the documented test/resource scope and cleanup order
            with self.assertRaisesRegex(BootstrapError, "SHA-256 diverge"):
                fetch_bundles(self.config_path, self._bundle(invalid_archive), True)
        self.assertFalse((self.root / "inputs" / "bundle").exists())

    def test_tar_path_traversal_and_links_fail_closed(self) -> None:
        archive = self._archive({"../escape.txt": b"synthetic"})
        with patch("nds_forge.bootstrap.urllib.request.urlopen", return_value=io.BytesIO(archive)):  # noqa: SIM117 -- Keep nested contexts to preserve the documented test/resource scope and cleanup order
            with self.assertRaisesRegex(BootstrapError, "Caminho inseguro"):
                fetch_bundles(self.config_path, self._bundle(archive), True)
        self.assertFalse((self.root / "escape.txt").exists())
        self.assertFalse((self.root / "inputs" / "bundle").exists())

        linked = io.BytesIO()
        with tarfile.open(fileobj=linked, mode="w:gz") as archive_file:
            item = tarfile.TarInfo("manifest.json")
            item.type = tarfile.SYMTYPE
            item.linkname = "outside"
            archive_file.addfile(item)
        link_archive = linked.getvalue()
        with patch("nds_forge.bootstrap.urllib.request.urlopen", return_value=io.BytesIO(link_archive)):  # noqa: SIM117 -- Keep nested contexts to preserve the documented test/resource scope and cleanup order
            with self.assertRaisesRegex(BootstrapError, "Tipo de membro não permitido"):
                fetch_bundles(self.config_path, self._bundle(link_archive, "inputs/link-bundle"), True)
        self.assertFalse((self.root / "inputs" / "link-bundle").exists())

    def test_profile_pins_public_release_inputs_without_key_material(self) -> None:
        profile = Path(__file__).resolve().parents[1] / "examples" / "neusoft-curitiba-v9.toml"
        config = tomllib.loads(profile.read_text(encoding="utf-8"))
        bundles = {entry["id"]: entry for entry in config["downloads"]["bundles"]}
        self.assertEqual(bundles["israel-original-base-v1.0.0"]["sha256"],
                         "0b564acdd834087753d8e1b7f17cdfa737015d9a0b073c4f6af8378b2019445b")
        self.assertEqual(bundles["curitiba-v9-inputs-v1.0.1"]["sha256"],
                         "4436e697b45335a7c41b91ac1ebc062c957f0e6c75630495101ab389cff6c877")
        self.assertIn("nds-israel-base-v1.0.0", bundles["israel-original-base-v1.0.0"]["url"])
        self.assertIn("nds-curitiba-v9-inputs-v1.0.1", bundles["curitiba-v9-inputs-v1.0.1"]["url"])
        self.assertEqual(config["neusoft"]["profile_id"], "neusoft-curitiba-v9")
        self.assertTrue(all(value.startswith(("env:", "file:"))
                            for value in config["neusoft"]["container_keys"].values()))
        self.assertNotIn("key_bytes", json.dumps(config))


class RuntimeSecretProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config_path = self.root / "profile.toml"
        self.config_path.write_text(
            'schema_version = 1\nproject_id = "secret-test"\nworkspace = "./work"\n'
            '[region]\nid="r"\nname="r"\nsource="bbox"\nbbox=[0,0,1,1]\n'
            '[source]\nkind="pbf"\npath="input.pbf"\n'
            '[pipeline]\ntarget="source.acquire"\n', encoding="utf-8")
        self.config = load_config(self.config_path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_file_and_environment_providers_resolve_exactly_16_synthetic_bytes(self) -> None:
        synthetic = bytes(range(16))
        key_file = self.root / "synthetic-key.fixture"
        key_file.write_bytes(synthetic)
        self.assertEqual(_resolve_secret_provider(self.config, f"file:{key_file}"), synthetic)
        env_name = "NDS_FORGE_TEST_SYNTHETIC_KEY"
        with patch.dict(os.environ, {env_name: synthetic.hex()}):
            self.assertEqual(_resolve_secret_provider(self.config, f"env:{env_name}"), synthetic)

    def test_invalid_secret_reference_or_length_does_not_echo_value(self) -> None:
        env_name = "NDS_FORGE_TEST_INVALID_KEY"
        sentinel = "a1" * 15
        with patch.dict(os.environ, {env_name: sentinel}):  # noqa: SIM117 -- Keep nested contexts to preserve the documented test/resource scope and cleanup order
            with self.assertRaises(PipelineError) as raised:
                _resolve_secret_provider(self.config, f"env:{env_name}")
        self.assertNotIn(sentinel, str(raised.exception))
        with patch.dict(os.environ, {env_name: "not-hex-secret"}):  # noqa: SIM117 -- Keep nested contexts to preserve the documented test/resource scope and cleanup order
            with self.assertRaises(PipelineError) as raised:
                _resolve_secret_provider(self.config, f"env:{env_name}")
        self.assertNotIn("not-hex-secret", str(raised.exception))
        with self.assertRaises(PipelineError) as raised:
            _resolve_secret_provider(self.config, "env:INVALID-NAME")
        self.assertNotIn("INVALID-NAME", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
