"""Fresh schema generation must not smuggle any source rows into a new DB."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from nds_forge.schema_contract import create_empty_database, export_contract


class SchemaContractTests(unittest.TestCase):
    def test_rows_and_source_database_are_not_build_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.db"
            with closing(sqlite3.connect(source)) as db:
                db.execute("PRAGMA user_version=7")
                db.execute("CREATE TABLE roads(id INTEGER PRIMARY KEY, name TEXT UNIQUE)")
                db.execute("CREATE INDEX road_index ON roads(name)")
                db.execute("INSERT INTO roads VALUES(1,'foreign geographic record')")
                db.commit()
            contract = export_contract(source)
            self.assertNotIn("foreign geographic record", json.dumps(contract))
            source.unlink()
            output = Path(temp) / "fresh.db"
            result = create_empty_database(contract, output)
            self.assertEqual(result["table_rows"], {"roads": 0})
            self.assertFalse(result["installable_nds_package"])
            with closing(sqlite3.connect(output)) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 7)
                self.assertEqual(db.execute("SELECT count(*) FROM roads").fetchone()[0], 0)
            original = output.read_bytes()
            with self.assertRaises(FileExistsError):
                create_empty_database(contract, output)
            self.assertEqual(output.read_bytes(), original)

    def test_fts_shadow_tables_are_created_by_sqlite_without_source_content(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.db"
            with closing(sqlite3.connect(source)) as db:
                db.execute("CREATE VIRTUAL TABLE names USING fts4(name)")
                db.execute("INSERT INTO names VALUES('source place name')")
                db.commit()
            contract = export_contract(source)
            self.assertEqual([obj["name"] for obj in contract["objects"]], ["names"])
            output = Path(temp) / "fresh.db"
            self.assertEqual(create_empty_database(contract, output)["table_rows"], {"names": 0})

    def test_missing_virtual_module_does_not_publish_partial_output(self):
        with tempfile.TemporaryDirectory() as temp:
            contract = {"schema_version": 1, "kind": "sqlite-ddl-only", "objects": [
                {"type": "table", "name": "roads", "sql": "CREATE TABLE roads(id)"},
                {"type": "table", "name": "names", "sql": "CREATE VIRTUAL TABLE names USING missing_nds_module"}]}
            output = Path(temp) / "fresh.db"
            with self.assertRaises(sqlite3.OperationalError):
                create_empty_database(contract, output)
            self.assertFalse(output.exists())
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_data_producing_ddl_and_dml_are_rejected(self):
        for sql in ("CREATE TABLE records AS SELECT 'foreign data' AS name", "INSERT INTO records VALUES(1)"):
            with self.subTest(sql=sql), tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / "fresh.db"
                contract = {"schema_version": 1, "kind": "sqlite-ddl-only", "objects": [
                    {"type": "table", "name": "records", "sql": sql}]}
                with self.assertRaises(ValueError):
                    create_empty_database(contract, output)
                self.assertFalse(output.exists())
