"""Derived database input lineage, using only temporary synthetic SQLite files."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from nds_forge.layers.package_assemble import _write_product, _write_shared


class CatalogInputLineageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalog_dir = self.root / "catalogs"
        self.catalog_dir.mkdir()
        route_manifest = self.root / "route-manifest.json"
        route_manifest.write_text('{"synthetic": "upstream route"}\n', encoding="utf-8")
        self.catalog = {
            "layer_coverage": {
                "bmd": {"level": 11, "shift": 4, "tile_ids": [1]},
                "route": {"payload_level": 13, "payload_shift": 4,
                          "index_level": 9, "index_tile_ids": [2]},
            },
            "source_artifacts": {
                "route_manifest_sha256": hashlib.sha256(route_manifest.read_bytes()).hexdigest(),
            },
        }
        self.catalog_path = self.catalog_dir / "catalogs.json"
        self.catalog_path.write_text(json.dumps(self.catalog) + "\n", encoding="utf-8")
        self.profile = {"route": {"region_id": 439}}

    def assert_catalog_input_resolves(self, origin: dict) -> None:
        self.assertEqual(origin["origin"], "derived")
        self.assertEqual(origin["producer_step"], "package.assemble")
        self.assertNotIn("validation_status", origin)
        self.assertEqual(len(origin["inputs"]), 1)
        reference = origin["inputs"][0]
        self.assertEqual(reference["artifact"], "catalogs.build")
        self.assertEqual(reference["path"], "catalogs.json")
        # Resolve the declared artifact/path, rather than checking digest shape.
        source = {"catalogs.build": self.catalog_dir}[reference["artifact"]] / reference["path"]
        self.assertEqual(source, self.catalog_path)
        self.assertEqual(reference["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertNotEqual(reference["sha256"], self.catalog["source_artifacts"]["route_manifest_sha256"])

    def test_shared_input_hash_matches_catalogs_json_bytes(self) -> None:
        path = self.root / "SHARED.sqlite"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE levelMetadataTable(level, buildingBlockId, shift, a, b, c)")
            db.execute("CREATE TABLE regionMetadataTable(isoCountryCode)")
            db.execute("INSERT INTO regionMetadataTable VALUES('ISR')")
        origin = _write_shared(path, self.catalog, self.profile, catalog_dir=self.catalog_dir)
        self.assert_catalog_input_resolves(origin)

    def test_product_input_hash_matches_catalogs_json_bytes(self) -> None:
        path = self.root / "PRODUCT.sqlite"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE tileContentIndexTable(region, buildingBlockId, level, sequence, tile, a, b, c)")
            db.execute("CREATE TABLE updateRegionNameTable(nameString, updateRegionId)")
            db.execute("INSERT INTO updateRegionNameTable VALUES('fixture',439)")
        origin = _write_product(path, self.catalog, self.profile, catalog_dir=self.catalog_dir)
        self.assert_catalog_input_resolves(origin)
