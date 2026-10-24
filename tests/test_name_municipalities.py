"""Contract tests for municipal assignment and dynamic NAME/FTS records."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from nds_forge.contracts import RegionSpec
from nds_forge.layers.municipalities import compile_municipalities
from nds_forge.layers.names import compile_name_records
from nds_forge.layers.package_assemble import _write_names

try:
    import osmium
except ImportError:
    osmium = None


class MunicipalityAssignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.clip = self.root / "clip"
        self.bmd = self.root / "bmd"
        self.names = self.root / "names"
        self.municipalities = self.root / "municipalities.json"
        self.clip.mkdir()
        self.bmd.mkdir()
        (self.bmd / "street_names.json").write_text(
            json.dumps({"Rua Teste": 10001}), encoding="utf-8")
        (self.clip / "roads.geojson").write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"name": "Rua Teste", "highway": "residential"},
                "geometry": {"type": "LineString",
                             "coordinates": [[-49.0, -25.0], [-48.999, -25.0]]},
            }],
        }), encoding="utf-8")
        self.municipalities.write_text(json.dumps({
            "schema_version": 1,
            "records": [{
                "id": 123,
                "ibge_code": "4100000",
                "name": "Município Teste",
                "admin_level": "8",
                "postal_code": "80000-000",
                "longitude": -49.0,
                "latitude": -25.0,
                "geometry": {"type": "Polygon", "coordinates": [[
                    [-49.1, -25.1], [-48.9, -25.1], [-48.9, -24.9],
                    [-49.1, -24.9], [-49.1, -25.1],
                ]]},
            }],
        }), encoding="utf-8")
        self.profile = {
            "admin_scope": "state_municipalities",
            "admin_hierarchy": [
                {"id": 1, "name": "Brasil", "class": 0, "morton": 1},
                {"id": 2, "name": "Paraná", "class": 2, "morton": 2},
            ],
            "criterion_a": "A", "criterion_b": "B", "criterion_c": "",
            "criterion_d": "", "criterion_j": "",
            "admin_substitutions": ["Brasil", "Paraná"],
            "alias_policy": {"include_unaccented": True},
            "street_class": 11,
            "region_id": 439,
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_street_centroid_gets_city_criteria_and_city_records(self) -> None:
        result = compile_name_records(
            self.clip / "roads.geojson", self.bmd, self.names,
            self.profile, self.municipalities)
        document = json.loads((self.names / "name-records.json").read_text())
        records = document["records"]
        self.assertEqual(result["counts"]["municipality_records"], 1)
        self.assertEqual(result["counts"]["assigned_streets"], 1)
        self.assertEqual(result["counts"]["unassigned_streets"], 0)
        street = next(record for record in records if record["class"] == 11)
        self.assertEqual(street["criterion_d"], "Município Teste")
        self.assertEqual(street["criterion_j"], "80000")
        self.assertEqual(street["ibge_code"], "4100000")
        cities = [record for record in records if record["class"] in (9, 10)]
        self.assertEqual(len(cities), 2)
        self.assertEqual({record["criterion_d"] for record in cities if "criterion_d" in record},
                         {"Município Teste"})
        self.assertEqual(document["substitutions"][0]["display"], "Município Teste")

    def test_writer_persists_dynamic_city_criteria_and_substitution(self) -> None:
        compile_name_records(
            self.clip / "roads.geojson", self.bmd, self.names,
            self.profile, self.municipalities)
        document = json.loads((self.names / "name-records.json").read_text())
        street = next(record for record in document["records"] if record["class"] == 11)
        city = next(record for record in document["records"] if record["class"] == 10)

        name_db = self.root / "NAME.sqlite"
        fts_db = self.root / "FTS.sqlite"
        with sqlite3.connect(name_db) as db:
            db.execute("CREATE TABLE namedObjectTable(id INTEGER, objectType INTEGER, nameBlob BLOB)")
        with sqlite3.connect(fts_db) as db:
            db.execute("""CREATE VIRTUAL TABLE nameFtsTable USING fts5(
                namedObjectId UNINDEXED, namedObjectClass UNINDEXED, mortonCode UNINDEXED,
                updateRegionId UNINDEXED, criterionA, criterionB, criterionC, criterionD,
                criterionI, criterionJ, searchTags)""")
            db.execute("""CREATE VIRTUAL TABLE poiFtsTable USING fts5(
                poiId UNINDEXED, mortonCode UNINDEXED, poiName, poiCategoryId UNINDEXED,
                regionId UNINDEXED, updateRegionId UNINDEXED, criterionA, criterionB,
                criterionC, criterionD, searchTags)""")
            for table in ("namedObject2HouseNRTable", "houseNR2NamedObjectTable",
                          "ftsHouseNumberRangeTable", "nameFtsSubstitutionTable",
                          "poiFtsSubstitutionTable"):
                db.execute(f"CREATE TABLE {table}(a TEXT,b TEXT,c TEXT,d INTEGER,e TEXT,f INTEGER)")

        poi_dir = self.root / "poi"
        poi_dir.mkdir()
        (poi_dir / "poi-records.json").write_text(json.dumps({
            "schema_version": 1, "records": [], "counts": {"accepted": 0},
        }), encoding="utf-8")
        _write_names(name_db, fts_db, self.names, poi_dir, {"names": self.profile})

        with sqlite3.connect(fts_db) as db:
            self.assertEqual(db.execute(
                "SELECT criterionC,criterionD,criterionJ FROM nameFtsTable "
                "WHERE namedObjectId=?", (street["id"],)).fetchone(),
                (street["criterion_c"], "Município Teste", "80000"))
            self.assertEqual(db.execute(
                "SELECT criterionC,criterionD FROM nameFtsTable "
                "WHERE namedObjectId=?", (city["id"],)).fetchone(),
                (city["criterion_c"], "Município Teste"))
            self.assertEqual(db.execute(
                "SELECT count(*) FROM nameFtsSubstitutionTable WHERE b='Município Teste'"
            ).fetchone(), (1,))


@unittest.skipUnless(osmium is not None, "requires optional osmium dependency")
class MunicipalityExtractionTests(unittest.TestCase):
    def test_closed_administrative_way_becomes_municipality(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pbf = root / "municipality.osm.pbf"
            writer = osmium.SimpleWriter(str(pbf))
            for node_id, (lon, lat) in enumerate(
                    [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)], 1):
                writer.add_node(osmium.osm.mutable.Node(
                    id=node_id, location=(lon, lat), version=1))
            writer.add_way(osmium.osm.mutable.Way(
                id=10, nodes=[1, 2, 3, 4, 1], version=1,
                tags={"boundary": "administrative", "admin_level": "8",
                      "name": "Município Sintético", "IBGE:GEOCODIGO": "4100000"}))
            writer.close()
            result = compile_municipalities(
                pbf, RegionSpec("synthetic", "synthetic", (-1, -1, 2, 2), None, True),
                root / "output", ibge_prefix="41")
            document = json.loads((root / "output/municipalities.json").read_text())
            self.assertEqual(result["counts"]["assembled"], 1)
            self.assertEqual(document["records"][0]["ibge_code"], "4100000")
            self.assertEqual(document["records"][0]["name"], "Município Sintético")


if __name__ == "__main__":
    unittest.main()