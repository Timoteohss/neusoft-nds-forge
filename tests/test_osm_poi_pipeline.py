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

