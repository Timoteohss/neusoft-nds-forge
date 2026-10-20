"""Unit tests for OSM POI layer compiler (Issue #20 / [4.2]).

Validates:
1. Extraction and compilation of POIs from synthetic nodes, ways, and relations.
2. Explicit tag-category mappings and include/exclude filtering rules.
3. Spatial and name deduplication (e.g. way footprint vs entrance node).
4. Spatial tile references and Morton codes.
5. Rejection auditing and counts reconciliation.
6. Absence of hardcoded Curitiba 10-POI restrictions.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from nds_forge.layers.poi import (
    POICategoryRule,
    compile_curated_pois,
    compile_osm_pois,
)
from nds_forge.osm_model import (
    ClipLineage,
    OSMDataset,
    OSMNode,
    OSMProvenance,
    OSMRelation,
    OSMWay,
    RelationMember,
)


class OSMPOITests(unittest.TestCase):
    """Test suite for compile_osm_pois compiler."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temp_dir.name)

        self.provenance = OSMProvenance(
            source="synthetic_osm_fixture",
            license="ODbL-1.0",
            attribution="© OpenStreetMap contributors",
        )
        self.dataset = OSMDataset(
            schema_version=1,
            provenance=self.provenance,
        )

        # Profile specifying region 100, POI tile level 11
        self.profile = {
            "profile_id": "test-generic-profile",
            "region_id": 100,
            "grid": {"bmd": {"level": 11, "shift": 4}},
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_synthetic_nodes_ways_and_relations_extraction(self) -> None:
        # 1. Node POI: Fuel station
        self.dataset.add_node(OSMNode(
            id=101, lat=-25.43, lon=-49.27,
            tags={"amenity": "fuel", "name": "Posto Shell Central"},
        ))

        # 2. Way POI: Supermarket polygon
        self.dataset.add_node(OSMNode(id=1, lat=-25.431, lon=-49.271))
        self.dataset.add_node(OSMNode(id=2, lat=-25.431, lon=-49.272))
        self.dataset.add_node(OSMNode(id=3, lat=-25.432, lon=-49.272))
        self.dataset.add_node(OSMNode(id=4, lat=-25.432, lon=-49.271))
        self.dataset.add_way(OSMWay(
            id=201, nodes=[1, 2, 3, 4],
            tags={"shop": "supermarket", "name": "Supermercado Festval"},
        ))

        # 3. Relation POI: Hospital multipolygon
        self.dataset.add_way(OSMWay(
            id=202, nodes=[1, 2, 3, 4],
            coordinates=[(-49.26, -25.44), (-49.26, -25.45), (-49.25, -25.45)],
            tags={"building": "yes"},
        ))
        self.dataset.add_relation(OSMRelation(
            id=301,
            members=[RelationMember(type="way", ref=202, role="outer")],
            tags={"amenity": "hospital", "name": "Hospital de Clínicas"},
        ))

        report = compile_osm_pois(self.dataset, self.output_dir, self.profile)

        # Output file exists
        poi_file = self.output_dir / "poi-records.json"
        self.assertTrue(poi_file.is_file())
        data = json.loads(poi_file.read_text(encoding="utf-8"))

        # Reconcile counts
        self.assertEqual(report["counts"]["accepted"], 3)
        self.assertEqual(report["counts"]["rejected"], 0)
        self.assertEqual(len(data["records"]), 3)

        records_by_type = {r["source_type"]: r for r in data["records"]}
        self.assertIn("node", records_by_type)
        self.assertEqual(records_by_type["node"]["name"], "Posto Shell Central")
        self.assertEqual(records_by_type["node"]["category"], "fuel")
        self.assertEqual(records_by_type["node"]["nds_category_id"], 1001)
        self.assertGreater(records_by_type["node"]["tile_id"], 0)

        self.assertIn("way", records_by_type)
        self.assertEqual(records_by_type["way"]["name"], "Supermercado Festval")
        self.assertEqual(records_by_type["way"]["category"], "supermarket")
        self.assertEqual(records_by_type["way"]["nds_category_id"], 1011)

        self.assertIn("relation", records_by_type)
        self.assertEqual(records_by_type["relation"]["name"], "Hospital de Clínicas")
        self.assertEqual(records_by_type["relation"]["category"], "hospital")
        self.assertEqual(records_by_type["relation"]["nds_category_id"], 1005)

    def test_category_inclusion_and_exclusion_rules(self) -> None:
        self.dataset.add_node(OSMNode(
            id=101, lat=-25.43, lon=-49.27,
            tags={"amenity": "fuel", "name": "Posto Ipiranga"},
        ))
        self.dataset.add_node(OSMNode(
            id=102, lat=-25.43, lon=-49.27,
            tags={"amenity": "pharmacy", "name": "Farmácia Nissei"},
        ))
        self.dataset.add_node(OSMNode(
            id=103, lat=-25.43, lon=-49.27,
            tags={"amenity": "cafe", "name": "Café do Centro"},
        ))

        # Test include rules: only fuel and pharmacy
        rules_inc = POICategoryRule(include_categories={"fuel", "pharmacy"})
        report_inc = compile_osm_pois(self.dataset, self.output_dir / "inc", self.profile, rules_inc)
        self.assertEqual(report_inc["counts"]["accepted"], 2)
        self.assertEqual(report_inc["counts"]["rejected"], 1)
        self.assertEqual(report_inc["counts"]["rejected_by_reason"]["excluded_category"], 1)

        # Test exclude rules: exclude pharmacy
        rules_exc = POICategoryRule(exclude_categories={"pharmacy"})
        report_exc = compile_osm_pois(self.dataset, self.output_dir / "exc", self.profile, rules_exc)
        self.assertEqual(report_exc["counts"]["accepted"], 2)
        self.assertEqual(report_exc["counts"]["rejected"], 1)
        self.assertEqual(report_exc["counts"]["rejected_by_reason"]["excluded_category"], 1)

    def test_missing_name_and_unmapped_category_rejections(self) -> None:
        # POI without name
        self.dataset.add_node(OSMNode(
            id=101, lat=-25.43, lon=-49.27,
            tags={"amenity": "fuel"},  # Missing name
        ))
        # POI with unmapped category
        self.dataset.add_node(OSMNode(
            id=102, lat=-25.43, lon=-49.27,
            tags={"amenity": "rocket_launchpad", "name": "Spaceport One"},
        ))

        report = compile_osm_pois(self.dataset, self.output_dir, self.profile)
        self.assertEqual(report["counts"]["accepted"], 0)
        self.assertEqual(report["counts"]["rejected"], 2)
        self.assertEqual(report["counts"]["rejected_by_reason"]["missing_name"], 1)
        self.assertEqual(report["counts"]["rejected_by_reason"]["unmapped_category"], 1)

    def test_spatial_and_name_deduplication(self) -> None:
        # Way representing restaurant building footprint
        self.dataset.add_node(OSMNode(id=1, lat=-25.4300, lon=-49.2700))
        self.dataset.add_node(OSMNode(id=2, lat=-25.4302, lon=-49.2700))
        self.dataset.add_node(OSMNode(id=3, lat=-25.4302, lon=-49.2702))
        self.dataset.add_node(OSMNode(id=4, lat=-25.4300, lon=-49.2702))
        self.dataset.add_way(OSMWay(
            id=201, nodes=[1, 2, 3, 4],
            tags={"amenity": "restaurant", "name": "Madalosso"},
        ))

        # Node representing entrance for the same restaurant situated ~15m away
        self.dataset.add_node(OSMNode(
            id=101, lat=-25.4301, lon=-49.2701,
            tags={"amenity": "restaurant", "name": "Madalosso"},
        ))

        # Another distinct restaurant with same name but far away (> 50m)
        self.dataset.add_node(OSMNode(
            id=102, lat=-25.4400, lon=-49.2800,
            tags={"amenity": "restaurant", "name": "Madalosso"},
        ))

        report = compile_osm_pois(
            self.dataset, self.output_dir, self.profile,
            rules=POICategoryRule(dedup_distance_meters=50.0),
        )

        # 2 distinct restaurants accepted, 1 spatial duplicate rejected
        self.assertEqual(report["counts"]["accepted"], 2)
        self.assertEqual(report["counts"]["rejected"], 1)
        self.assertEqual(report["counts"]["rejected_by_reason"]["duplicate_spatial_name"], 1)

        data = json.loads((self.output_dir / "poi-records.json").read_text(encoding="utf-8"))
        accepted_ids = {r["id"] for r in data["records"]}
        # The way footprint is prioritized over the entrance node
        self.assertIn("way_201", accepted_ids)
        self.assertIn("node_102", accepted_ids)
        self.assertNotIn("node_101", accepted_ids)

    def test_tile_references_and_morton_encoding(self) -> None:
        self.dataset.add_node(OSMNode(
            id=101, lat=-25.4297, lon=-49.2714,
            tags={"amenity": "cafe", "name": "Praça Café"},
        ))
        report = compile_osm_pois(self.dataset, self.output_dir, self.profile)
        data = json.loads((self.output_dir / "poi-records.json").read_text(encoding="utf-8"))
        record = data["records"][0]

        self.assertIn("tile_id", record)
        self.assertIn("morton", record)
        self.assertIsInstance(record["tile_id"], int)
        self.assertIsInstance(record["morton"], int)
        self.assertEqual(record["region_id"], 100)


if __name__ == "__main__":
    unittest.main()
