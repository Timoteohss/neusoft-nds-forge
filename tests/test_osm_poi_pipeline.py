"""Real tiny-PBF pipeline regression, with synthetic explicit category IDs."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

try:
    import osmium
    import shapely
except ImportError:
    osmium = None

from nds_forge.config import load_config
from nds_forge.pipeline import PipelineError, run


@unittest.skipUnless(osmium is not None, "requires optional osm dependencies")
class OSMPoiPipelineTests(unittest.TestCase):
    def test_pbf_to_numeric_poi_records_without_curated_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pbf = root / "fixture.osm.pbf"
            with osmium.SimpleWriter(str(pbf)) as writer:
                for ident, xy, tags in [
                    (1, (-.5, 0), {}), (2, (.5, 0), {}),
                    (3, (0, .1), {"amenity": "pharmacy", "name": "Farmácia Teste"}),
                    (4, (5, 5), {"amenity": "pharmacy", "name": "Outside"}),
                    (5, (-.2, -.2), {}), (6, (.2, -.2), {}),
                    (7, (.2, .2), {}), (8, (-.2, .2), {}),
                ]:
                    writer.add_node(osmium.osm.mutable.Node(id=ident, location=xy, tags=tags))
                writer.add_way(osmium.osm.mutable.Way(
                    id=10, nodes=[1, 2], tags={"highway": "residential", "name": "Rua Teste"}))
                writer.add_way(osmium.osm.mutable.Way(
                    id=11, nodes=[5, 6, 7, 8, 5], tags={"amenity": "pharmacy", "name": "Farmácia Footprint"}))
            config_path = root / "config.toml"
            config_path.write_text('''schema_version = 1
project_id = "poi-fixture"
workspace = "work"
[region]
id = "fixture"
source = "bbox"
bbox = [-1.0, -1.0, 1.0, 1.0]
[source]
kind = "pbf"
path = "fixture.osm.pbf"
license = "ODbL-1.0"
attribution = "© OpenStreetMap contributors"
[pipeline]
target = "poi.compile"
[neusoft]
profile_id = "synthetic-poi"
[neusoft.poi]
source = "osm"
region_id = 439
osm_tag_categories = { "amenity=pharmacy" = "synthetic-native-category" }
poi_table_category_ids = { "synthetic-native-category" = 1006 }
''', encoding="utf-8")
            config = load_config(config_path)
            result = run(config, "poi.compile", allow_experimental=True)
            directory = root / "work/artifacts/poi-records"
            output = directory / "poi-records.json"
            document = json.loads(output.read_text())
            rows = document["records"]
            self.assertEqual({row["source_ref"] for row in rows}, {"node_3", "way_11"})
            self.assertEqual(len({int(row["id"]) for row in rows}), 2)
            self.assertTrue(all(row["category"] == "synthetic-native-category" for row in rows))
            roads = json.loads((root / "work/artifacts/clipped-osm/roads.geojson").read_text())
            self.assertEqual([feature["properties"]["osm_id"] for feature in roads["features"]], [10])
            manifest = json.loads((root / "work/state/poi__compile.json").read_text())
            self.assertEqual(manifest["result"]["outputs"]["poi-records.json"]["sha256"],
                             hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertEqual(result["overall_validation"], "unvalidated")
            cached = run(config, "poi.compile", allow_experimental=True, resume=True)
            self.assertEqual(cached["results"][-1]["status"], "cached")

            # Curated compatibility remains available; same-path input edits
            # must invalidate only the consuming stage, not reuse stale POIs.
            config_path.write_text(config_path.read_text().replace('source = "osm"',
                'source = "profile_curated"\ncurated_poi_file = "curated.json"\n'
                'poi_categories = { "synthetic-native-category" = "synthetic-native-category" }'))
            curated = {"schema_version": 1, "profile_id": "synthetic-poi", "records": [{
                "id": "501", "category": "synthetic-native-category", "name": "Original",
                "lon": 0, "lat": 0, "aliases": []}]}
            curated_path = root / "curated.json"
            curated_path.write_text(json.dumps(curated))
            config = load_config(config_path)
            before = run(config, "poi.compile", allow_experimental=True, resume=True)
            curated["records"][0]["name"] = "Updated"
            curated_path.write_text(json.dumps(curated))
            after = run(config, "poi.compile", allow_experimental=True, resume=True)
            self.assertEqual(after["results"][-1]["status"], "completed")
            self.assertNotEqual(before["results"][-1]["fingerprint"], after["results"][-1]["fingerprint"])
            self.assertEqual(json.loads(output.read_text())["records"][0]["name"], "Updated")

            # Unsupported features must fail before any new workspace appears.
            config_path.write_text(config_path.read_text().replace(
                '[pipeline]', '[pipeline]\nrequired_capabilities = ["address_search", "native_turn_restrictions"]'
            ).replace('workspace = "work"', 'workspace = "blocked-work"'))
            with self.assertRaisesRegex(PipelineError, "not implemented"):
                run(load_config(config_path), "poi.compile", allow_experimental=True)
            self.assertFalse((root / "blocked-work").exists())

    def test_osm_mode_rejects_implicit_native_categories(self):
        from nds_forge.layers.steps import validate_poi_profile
        with self.assertRaisesRegex(ValueError, "explicit osm_tag_categories"):
            validate_poi_profile({"source": "osm"})

    def test_pinned_native_categories_reach_active_poi_and_fts_writers(self):
        """Tiny valid PBF tests the real registered compiler and active SQL writers."""
        import sqlite3
        from nds_forge.layers.package_assemble import _write_names, _write_pois
        from nds_forge.layers.poi_categories import NATIVE_CATEGORY_PROFILE

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pbf = root / "fixture.osm.pbf"
            mapping = NATIVE_CATEGORY_PROFILE["osm_mappings"]
            tags = list(sorted(mapping))
            with osmium.SimpleWriter(str(pbf)) as writer:
                writer.add_node(osmium.osm.mutable.Node(id=1, location=(-49.34, -25.40)))
                writer.add_node(osmium.osm.mutable.Node(id=2, location=(-49.26, -25.40)))
                for index, tag in enumerate(tags + ["amenity=hospital", "amenity=charging_station"], 100):
                    key, value = tag.split("=", 1)
                    writer.add_node(osmium.osm.mutable.Node(id=index,
                        location=(-49.33 + (index - 100) * .002, -25.39),
                        tags={key: value, "name": f"Lugar {index}"}))
                writer.add_way(osmium.osm.mutable.Way(id=10, nodes=[1, 2],
                    tags={"highway": "residential", "name": "Rua Teste"}))
            example = Path(__file__).parents[1] / "examples/neusoft-parana-v9.toml"
            text = example.read_text(encoding="utf-8")
            text = text.replace('workspace = "./work/neusoft-parana-v9"', 'workspace = "work"')
            text = text.replace('bbox = [-54.62, -26.72, -48.02, -22.51]',
                                'bbox = [-49.35, -25.45, -49.25, -25.35]')
            import re
            text = re.sub(r'^path = .*$', 'path = "fixture.osm.pbf"', text, flags=re.M)
            text = re.sub(r'^sha256 = .*$', 'sha256 = "' + hashlib.sha256(pbf.read_bytes()).hexdigest() + '"', text, flags=re.M)
            config_path = root / "config.toml"
            config_path.write_text(text, encoding="utf-8")
            config = load_config(config_path)
            result = run(config, "poi.compile", allow_experimental=True)
            poi_dir = root / "work/artifacts/poi-records"
            document = json.loads((poi_dir / "poi-records.json").read_text())
            self.assertEqual(document["counts"]["accepted"], len(tags))
            self.assertEqual(document["counts"]["rejected_by_reason"]["unmapped_category"], 2)
            self.assertEqual({row["category"] for row in document["records"]}, set(mapping.values()))
            evidence = document["source"]["native_category_evidence"]
            self.assertEqual(evidence["id"], NATIVE_CATEGORY_PROFILE["id"])
            self.assertEqual(evidence["source_hashes"], NATIVE_CATEGORY_PROFILE["source_hashes"])
            self.assertEqual(document["source"]["osm_provenance"]["sha256"],
                             hashlib.sha256(pbf.read_bytes()).hexdigest())

            # Schemas below are synthetic. The functions are the writers called
            # by the CLI's registered package.assemble, not the unused assembler.
            name_db, fts_db, poi_db = (root / name for name in ("NAME.db", "FTS.db", "POI.db"))
            with sqlite3.connect(name_db) as db:
                db.execute("CREATE TABLE namedObjectTable(id INTEGER, type INTEGER, name BLOB)")
            with sqlite3.connect(fts_db) as db:
                db.execute("CREATE VIRTUAL TABLE nameFtsTable USING fts5(namedObjectId UNINDEXED, namedObjectClass UNINDEXED, mortonCode UNINDEXED, updateRegionId UNINDEXED, criterionA,criterionB,criterionC,criterionD,criterionI,criterionJ,searchTags)")
                db.execute("CREATE VIRTUAL TABLE poiFtsTable USING fts5(poiId UNINDEXED,mortonCode UNINDEXED,poiName,poiCategoryId UNINDEXED,regionId UNINDEXED,updateRegionId UNINDEXED,criterionA,criterionB,criterionC,criterionD,searchTags)")
                for table in ("namedObject2HouseNRTable", "houseNR2NamedObjectTable", "ftsHouseNumberRangeTable", "poiFtsSubstitutionTable"):
                    db.execute(f"CREATE TABLE {table}(id INTEGER)")
                db.execute("CREATE TABLE nameFtsSubstitutionTable(a,b,c,d,e,f)")
            with sqlite3.connect(poi_db) as db:
                db.execute("CREATE TABLE poiTable(poiId INTEGER,categoryId INTEGER,morton INTEGER,a,b,c,d,e,f,g,h,i)")
                db.execute("CREATE TABLE poiVirtualTileTable(tileId INTEGER,a,b,c,d)")
                db.execute("CREATE TABLE poiToNameStringCollTable(poiId INTEGER,nameId INTEGER)")
                db.execute("CREATE TABLE poiNameStringCollTable(poiId INTEGER,a,b,c,d,name,e)")
                for table in ("poiLinkAccessTable", "poiRelationTable", "poiNamedObjectRelationTable"):
                    db.execute(f"CREATE TABLE {table}(id INTEGER)")
            names_dir, bmd_dir = root / "names", root / "bmd"
            names_dir.mkdir()
            bmd_dir.mkdir()
            (names_dir / "name-records.json").write_text(json.dumps({"records": config.neusoft["names"]["admin_hierarchy"]}))
            (bmd_dir / "bmd-manifest.json").write_text('{"tiles": []}')
            _, fts_origin = _write_names(name_db, fts_db, names_dir, poi_dir, config.neusoft)
            poi_origin = _write_pois(poi_db, fts_db, poi_dir, bmd_dir, config.neusoft["poi"])
            with sqlite3.connect(fts_db) as db:
                fts_rows = dict(db.execute("SELECT poiId,poiCategoryId FROM poiFtsTable"))
                self.assertEqual(db.execute("SELECT count(*) FROM poiFtsTable WHERE poiFtsTable MATCH 'Lugar'").fetchone()[0], len(tags))
            with sqlite3.connect(poi_db) as db:
                poi_rows = dict(db.execute("SELECT poiId,categoryId FROM poiTable"))
            expected_ids = NATIVE_CATEGORY_PROFILE["poi_table_category_ids"]
            self.assertEqual({key: expected_ids[value] for key, value in fts_rows.items()}, poi_rows)
            self.assertEqual(set(fts_rows.values()), set(expected_ids))
            self.assertEqual(poi_origin["provenance"]["compiler_source"]["native_category_evidence"], evidence)
            self.assertEqual(fts_origin["provenance"]["poi"]["compiler_source"]["native_category_evidence"], evidence)
            self.assertIn("OSM", poi_origin["provenance"]["source"])
            self.assertEqual(result["overall_validation"], "unvalidated")
