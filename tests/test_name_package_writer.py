from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from nds_forge.layers.names import validate_admin_scope
from nds_forge.layers.package_assemble import _write_names
from nds_forge.registry import StepContext, get_executor


class NamePackageWriterTests(unittest.TestCase):
    """Synthetic SQL fixtures verify NAME/FTS id and lineage joins only."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.name_db = self.root / "NAME.sqlite"
        self.fts_db = self.root / "FTS.sqlite"
        self.names_dir = self.root / "names"
        self.poi_dir = self.root / "poi"
        self.names_dir.mkdir()
        self.poi_dir.mkdir()
        self._seed_schemas()
        self.name_records = {
            "schema_version": 1,
            "records": [
                {"id": 1, "name": "Brasil", "class": 0, "morton": 1},
                {"id": 2, "name": "Paraná", "class": 2, "morton": 2},
                {"id": 3, "name": "Curitiba", "class": 9, "morton": 3},
                {"id": 4, "name": "Curitiba", "class": 10, "morton": 4},
                {"id": 10001, "name": "Rua São João", "class": 11, "morton": 100,
                 "aliases": "Rua São João\x1cSao Joao", "search_tags": "Sao Joao",
                 "region_id": 439, "origin": "synthetic_fixture"},
            ],
        }
        self.poi_records = {
            "schema_version": 1,
            "records": [
                {"id": "501", "name": "Praça Teste", "category": "fixture",
                 "aliases": "Praça Teste\x1cTeste", "search_tags": "Praca Teste",
                 "morton": 501},
                {"id": "502", "name": "Calçadão de Teste", "category": "fixture",
                 "aliases": ["Calçadão de Teste", "Calcadao Teste"],
                 "search_tags": "Calcadao Teste", "morton": 502},
            ],
        }
        self.name_path = self.names_dir / "name-records.json"
        self.poi_path = self.poi_dir / "poi-records.json"
        self.name_path.write_text(json.dumps(self.name_records, ensure_ascii=False), encoding="utf-8")
        self.poi_path.write_text(json.dumps(self.poi_records, ensure_ascii=False), encoding="utf-8")
        self.profile = {
            "names": {
                "region_id": 439,
                "admin_hierarchy": [{"id": i, "name": f"Admin {i}", "class": cls, "morton": i}
                                    for i, cls in enumerate((0, 2, 9, 10), 1)],
                "criterion_a": "A", "criterion_b": "B", "criterion_c": "C",
                "criterion_d": "D", "criterion_j": "J",
                "admin_substitutions": ["Brasil", "Paraná", "Curitiba", "Curitiba"],
            }
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _seed_schemas(self) -> None:
        with sqlite3.connect(self.name_db) as db:
            db.execute("CREATE TABLE namedObjectTable(id INTEGER, objectType INTEGER, nameBlob BLOB)")
        with sqlite3.connect(self.fts_db) as db:
            db.execute("""CREATE VIRTUAL TABLE nameFtsTable USING fts5(
                namedObjectId UNINDEXED, namedObjectClass UNINDEXED, mortonCode UNINDEXED,
                updateRegionId UNINDEXED, criterionA, criterionB, criterionC, criterionD,
                criterionI, criterionJ, searchTags)""")
            db.execute("""CREATE VIRTUAL TABLE poiFtsTable USING fts5(
                poiId UNINDEXED, mortonCode UNINDEXED, poiName, poiCategoryId UNINDEXED,
                regionId UNINDEXED, updateRegionId UNINDEXED, criterionA, criterionB,
                criterionC, criterionD, searchTags)""")
            for table, columns in {
                "namedObject2HouseNRTable": "id INTEGER",
                "houseNR2NamedObjectTable": "id INTEGER",
                "ftsHouseNumberRangeTable": "id INTEGER",
                "nameFtsSubstitutionTable": "a TEXT,b TEXT,c TEXT,d INTEGER,e TEXT,f INTEGER",
                "poiFtsSubstitutionTable": "a TEXT,b TEXT,c TEXT,d INTEGER,e TEXT,f INTEGER",
            }.items():
                db.execute(f"CREATE TABLE {table}({columns})")

    def test_name_and_fts_outputs_keep_bmd_ids_and_poifta_references(self) -> None:
        name_origin, fts_origin = _write_names(
            self.name_db, self.fts_db, self.names_dir, self.poi_dir, self.profile)

        with sqlite3.connect(self.name_db) as db:
            rows = db.execute("SELECT id,nameBlob FROM namedObjectTable ORDER BY id").fetchall()
        by_id = dict(rows)
        self.assertEqual(set(by_id), {1, 2, 3, 4, 10001})
        self.assertTrue(by_id[10001].startswith(b"\x00\x0b\x00\x00\x60\x80\x01"))
        for item in self.name_records["records"]:
            self.assertEqual(int.from_bytes(by_id[item["id"]][:2], "big"), item["class"])
        self.assertIn("Rua São João".encode(), by_id[10001])

        with sqlite3.connect(self.fts_db) as db:
            name_rows = db.execute(
                "SELECT namedObjectId,criterionI,searchTags FROM nameFtsTable "
                "WHERE namedObjectId=10001").fetchall()
            # Mirror the native getter's identity expression; checking only
            # namedObjectId previously missed the broken FTS -> NAME join.
            sdk_ids = db.execute(
                "SELECT (rowid & 4294967295),namedObjectId FROM nameFtsTable "
                "WHERE nameFtsTable MATCH 'Sao'").fetchall()
            poi_rows = db.execute(
                "SELECT poiId,poiName,poiCategoryId FROM poiFtsTable").fetchall()
            sdk_poi_ids = db.execute(
                "SELECT (rowid & 4294967295),poiId FROM poiFtsTable "
                "WHERE poiFtsTable MATCH 'Praça'").fetchall()
            accented_poi_matches = db.execute(
                "SELECT poiId FROM poiFtsTable WHERE poiFtsTable MATCH 'Praça' ORDER BY poiId"
            ).fetchall()
            alias_poi_matches = db.execute(
                "SELECT poiId FROM poiFtsTable WHERE poiFtsTable MATCH 'Calçadão' ORDER BY poiId"
            ).fetchall()
            alias_name_matches = db.execute(
                "SELECT namedObjectId FROM nameFtsTable WHERE nameFtsTable MATCH 'Sao' ORDER BY namedObjectId"
            ).fetchall()
            admin_subs = db.execute("SELECT b FROM nameFtsSubstitutionTable ORDER BY rowid").fetchall()
            house_count = db.execute("SELECT count(*) FROM namedObject2HouseNRTable").fetchone()[0]
        self.assertEqual(name_rows, [(10001, "Rua São João Sao Joao", "Sao Joao")])
        self.assertEqual(sdk_ids, [(10001, 10001)])
        self.assertEqual(sdk_poi_ids, [(501, 501)])
        self.assertEqual(poi_rows, [
            (501, "Praça Teste \x1cTeste", "fixture"),
            (502, "Calçadão de Teste \x1cCalcadao Teste", "fixture"),
        ])
        self.assertEqual(accented_poi_matches, [(501,)])
        self.assertEqual(alias_poi_matches, [(502,)])
        self.assertEqual(alias_name_matches, [(10001,)])
        self.assertEqual(admin_subs, [("Brasil",), ("Paraná",), ("Curitiba",), ("Curitiba",)])
        self.assertEqual(house_count, 0, "NAME/FTS writer must not imply house-number support")

        self.assertEqual(name_origin["origin"], "generated")
        self.assertEqual(name_origin["inputs"][0]["sha256"], hashlib.sha256(self.name_path.read_bytes()).hexdigest())
        self.assertEqual(fts_origin["origin"], "generated")
        self.assertEqual({item["artifact"] for item in fts_origin["inputs"]},
                         {"names.compile", "poi.compile"})
        self.assertTrue(all(item["sha256"] for item in fts_origin["inputs"]))

    def _state_profile(self) -> None:
        names = self.profile["names"]
        names["admin_scope"] = "state"
        names["admin_hierarchy"] = names["admin_hierarchy"][:2]
        names["admin_substitutions"] = ["Brasil", "Paraná"]
        for key in ("criterion_c", "criterion_d", "criterion_j"):
            names[key] = ""
        self.name_records["records"] = [r for r in self.name_records["records"] if r["class"] not in (9, 10)]
        self.name_path.write_text(json.dumps(self.name_records), encoding="utf-8")

    def test_state_fallback_preserves_names_without_invented_city_or_postcode(self) -> None:
        self._state_profile()
        _write_names(self.name_db, self.fts_db, self.names_dir, self.poi_dir, self.profile)
        with sqlite3.connect(self.fts_db) as db:
            self.assertEqual(db.execute(
                "SELECT criterionA,criterionB,criterionC,criterionD,criterionJ "
                "FROM nameFtsTable WHERE namedObjectId=10001").fetchone(), ("A", "B", "", "", ""))
            self.assertEqual(db.execute(
                "SELECT count(*) FROM nameFtsTable WHERE namedObjectClass IN (9,10)").fetchone(), (0,))
            self.assertEqual(db.execute("SELECT b FROM nameFtsSubstitutionTable").fetchall(),
                             [("Brasil",), ("Paraná",)])
            self.assertEqual(db.execute(
                "SELECT namedObjectId FROM nameFtsTable WHERE nameFtsTable MATCH 'Sao'").fetchall(), [(10001,)])

    def test_state_fallback_refuses_municipality_or_fixed_postcode(self) -> None:
        self._state_profile()
        names = self.profile["names"]
        names["criterion_j"] = "80000"
        with self.assertRaisesRegex(ValueError, "municipality/postcode"):
            validate_admin_scope(names)
        names["criterion_j"] = ""
        names["admin_hierarchy"].append({"id": 3, "name": "Curitiba", "class": 9, "morton": 3})
        with self.assertRaisesRegex(ValueError, "country/state"):
            validate_admin_scope(names)

    def test_state_fallback_rejects_stale_municipality_records(self) -> None:
        self._state_profile()
        self.name_records["records"].append({"id": 3, "name": "Curitiba", "class": 9, "morton": 3})
        self.name_path.write_text(json.dumps(self.name_records), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "fixed municipality"):
            _write_names(self.name_db, self.fts_db, self.names_dir, self.poi_dir, self.profile)

    def test_registered_package_executor_rejects_bad_state_before_external_io(self) -> None:
        self._state_profile()
        self.profile["names"]["criterion_j"] = "80000"
        config = SimpleNamespace(neusoft={"profile_id": "neusoft-curitiba-v9", **self.profile})
        resolver = Mock(side_effect=AssertionError("must reject before secret resolution"))
        output = self.root / "package"
        context = StepContext(config, output, {}, {}, False, True, resolver)
        with self.assertRaisesRegex(ValueError, "municipality/postcode"):
            get_executor("package.assemble")(context)
        resolver.assert_not_called()
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
