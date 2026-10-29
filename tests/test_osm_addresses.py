"""House-number compilation from OSM address objects into the verified FTS schema."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from nds_forge.layers.address import (
    compile_osm_addresses,
    query_house_number_native,
    write_address_tables,
    write_house_index,
)


def _dataset() -> dict:
    return {
        "nodes": [
            {"id": 1, "lat": -25.45, "lon": -49.27,
             "tags": {"addr:street": "Rua Alfa", "addr:housenumber": "10"}},
            {"id": 2, "lat": -25.46, "lon": -49.28,
             "tags": {"addr:street": "Rua Alfa", "addr:housenumber": "10"}},
            {"id": 3, "tags": {"addr:street": "Rua Alfa", "addr:housenumber": "11"}},
            {"id": 4, "tags": {"addr:street": "rua alfa", "addr:housenumber": " 11 "}},
            {"id": 5, "tags": {"addr:street": "Rua Alfa", "addr:housenumber": "S/N"}},
            {"id": 6, "tags": {"addr:street": "Rua Desconhecida", "addr:housenumber": "20"}},
            {"id": 7, "tags": {"addr:housenumber": "30"}},
        ],
        "ways": [
            {"id": 10, "coordinates": [[-49.30, -25.40], [-49.32, -25.42]],
             "tags": {"addr:street": "Rua Beta", "addr:housenumber": "7A"}},
        ],
    }


class OSMAddressCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.dataset_path = root / "osm-dataset.json"
        self.dataset_path.write_text(json.dumps(_dataset()), encoding="utf-8")
        self.names_path = root / "street_names.json"
        self.names_path.write_text(json.dumps({"Rua Alfa": 1001, "Rua Beta": 1002}), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_compiles_only_resolvable_addresses_with_shared_ranges(self) -> None:
        doc = compile_osm_addresses(self.dataset_path, self.names_path, 439)
        audit = doc["audit"]
        self.assertEqual(audit["address_objects"], 8)
        self.assertEqual(audit["unknown_street"], 1)
        self.assertEqual(audit["missing_street"], 1)
        self.assertEqual(audit["duplicate_bindings"], 2)
        self.assertEqual(audit["resolved"], 4)
        # 10/even, 11/odd, S/N point and 7A point; duplicate and case-folded
        # repeats share the same range row.
        self.assertEqual(audit["ranges"], 4)
        self.assertEqual(audit["bindings"], 4)
        self.assertEqual(audit["with_coordinates"], 2)
        self.assertEqual(len({(b["named_object_id"], b["range_id"]) for b in doc["bindings"]}), 4)
        even = next(item for item in doc["ranges"] if item["type"] == 1)
        first = next(b for b in doc["bindings"]
                     if b["named_object_id"] == 1001 and b["range_id"] == even["range_id"])
        self.assertEqual((first["lat"], first["lon"]), (-25.45, -49.27))
        beta = next(b for b in doc["bindings"] if b["named_object_id"] == 1002)
        self.assertEqual((beta["lat"], beta["lon"]), (-25.41, -49.31))

    def test_native_query_resolves_street_and_number(self) -> None:
        doc = compile_osm_addresses(self.dataset_path, self.names_path, 439)
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.executescript(
            """
            CREATE TABLE ftsHouseNumberRangeTable(
                rangeId INTEGER NOT NULL, type INTEGER NOT NULL, minHnr INTEGER,
                maxHnr INTEGER, languageCode INTEGER, prefix TEXT, suffix TEXT,
                addressPoint TEXT, primary key(rangeId),
                check(minHnr is not NULL or addressPoint is not NULL));
            CREATE TABLE namedObject2HouseNRTable(
                namedObject2HouseNumberRangeId INTEGER PRIMARY KEY, updateRegionId INTEGER);
            CREATE TABLE houseNR2NamedObjectTable(
                houseNumberRange2NamedObjectId INTEGER PRIMARY KEY, updateRegionId INTEGER);
            """
        )
        write_address_tables(db, doc)

        self.assertEqual(len(query_house_number_native(db, 1001, 10)), 1)
        self.assertEqual(len(query_house_number_native(db, 1001, 11)), 1)
        self.assertEqual(len(query_house_number_native(db, 1001, "S/N")), 1)
        self.assertEqual(query_house_number_native(db, 1001, 12), [])
        self.assertEqual(query_house_number_native(db, 1002, 7), [])
        self.assertEqual(len(query_house_number_native(db, 1002, "7A")), 1)
        self.assertEqual(query_house_number_native(db, 1003, 10), [])

    def test_house_index_lists_only_resolvable_numbers(self) -> None:
        doc = compile_osm_addresses(self.dataset_path, self.names_path, 439)
        out = Path(self.tmp.name) / "house-number-index.tsv"
        stats = write_house_index(doc, self.names_path, out)
        self.assertEqual(stats["streets"], 2)
        self.assertEqual(stats["entries"], 2)
        lines = out.read_text(encoding="utf-8").splitlines()
        by_name = {line.split("\t")[0]: line.split("\t") for line in lines}
        self.assertIn("rua alfa", by_name)
        self.assertIn("rua beta", by_name)
        self.assertEqual(by_name["rua alfa"][1], "Rua Alfa")
        self.assertIn("10:", by_name["rua alfa"][2])
        self.assertNotIn("11:", by_name["rua alfa"][2])
        self.assertIn("7A:", by_name["rua beta"][2])


if __name__ == "__main__":
    unittest.main()
