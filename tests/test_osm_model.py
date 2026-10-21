"""Tests for the canonical loss-aware OSM model (Issue #8 / [2.1]).

Verifies schema adherence, roundtrip deserialization, deterministic ordering,
version enforcement, explicit missing reference auditing, and failure modes on
malformed inputs.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from nds_forge.osm_model import (
    ClipLineage,
    OSMDataset,
    OSMModelError,
    OSMNode,
    OSMProvenance,
    OSMRelation,
    OSMWay,
    RelationMember,
    SCHEMA_VERSION,
)


class OSMModelTests(unittest.TestCase):

    def setUp(self) -> None:
        self.provenance = OSMProvenance(
            source="synthetic_test_pbf",
            license="ODbL-1.0",
            attribution="© OpenStreetMap contributors",
            path="synthetic/test.pbf",
            sha256="a" * 64,
            timestamp="2026-10-20T12:00:00Z",
        )
        self.clip = ClipLineage(
            region_id="test-region",
            boundary_sha256="b" * 64,
            policy={"highways": ["primary", "secondary"]},
        )
        self.schema_path = Path(__file__).resolve().parents[1] / "schemas" / "osm-model.schema.json"

    def test_schema_file_exists_and_is_valid_json(self) -> None:
        self.assertTrue(self.schema_path.is_file(), "Schema file must exist")
        schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        self.assertEqual(schema.get("title"), "NDS Forge Canonical Loss-Aware OSM Model")
        self.assertIn("required", schema)

    def test_deterministic_serialization_independent_of_insertion_order(self) -> None:
        ds1 = OSMDataset(provenance=self.provenance, clip_lineage=self.clip)
        ds1.add_node(OSMNode(id=20, lat=-25.4, lon=-49.2, tags={"name": "Second", "highway": "crossing"}))
        ds1.add_node(OSMNode(id=10, lat=-25.3, lon=-49.1, tags={"zebra": "yes", "amenity": "bench"}))
        ds1.add_way(OSMWay(id=200, nodes=[10, 20], tags={"highway": "primary", "oneway": "yes"}))
        ds1.add_way(OSMWay(id=100, nodes=[20, 10], tags={"highway": "residential"}))
        ds1.add_relation(OSMRelation(
            id=50,
            members=[RelationMember(type="way", ref=100, role="from"),
                     RelationMember(type="way", ref=200, role="to")],
            tags={"type": "restriction", "restriction": "no_left_turn"}
        ))

        ds2 = OSMDataset(provenance=self.provenance, clip_lineage=self.clip)
        # Add in reverse order
        ds2.add_relation(OSMRelation(
            id=50,
            members=[RelationMember(type="way", ref=100, role="from"),
                     RelationMember(type="way", ref=200, role="to")],
            tags={"restriction": "no_left_turn", "type": "restriction"}
        ))
        ds2.add_way(OSMWay(id=100, nodes=[20, 10], tags={"highway": "residential"}))
        ds2.add_way(OSMWay(id=200, nodes=[10, 20], tags={"oneway": "yes", "highway": "primary"}))
        ds2.add_node(OSMNode(id=10, lat=-25.3, lon=-49.1, tags={"amenity": "bench", "zebra": "yes"}))
        ds2.add_node(OSMNode(id=20, lat=-25.4, lon=-49.2, tags={"highway": "crossing", "name": "Second"}))

        json1 = ds1.to_json()
        json2 = ds2.to_json()
        self.assertEqual(json1, json2, "Serialization must be completely deterministic regardless of insertion order")

    def test_roundtrip_fidelity(self) -> None:
        ds = OSMDataset(provenance=self.provenance, clip_lineage=self.clip)
        ds.add_node(OSMNode(id=1, lat=-25.4284, lon=-49.2733, tags={"name": "Praça Tiradentes"}))
        ds.add_node(OSMNode(id=2, lat=-25.4290, lon=-49.2740, tags={"highway": "traffic_signals"}))
        ds.add_way(OSMWay(
            id=101,
            nodes=[1, 2],
            tags={"highway": "secondary", "name": "Rua Marechal Deodoro"},
            coordinates=[(-49.2733, -25.4284), (-49.2740, -25.4290)],
            is_boundary_cut=True,
        ))
        ds.add_relation(OSMRelation(
            id=1001,
            members=[
                RelationMember(type="node", ref=1, role="via"),
                RelationMember(type="way", ref=101, role="to"),
            ],
            tags={"type": "restriction", "restriction": "no_u_turn"},
            is_partial=False,
        ))

        # Test to_dict -> from_dict
        d = ds.to_dict()
        reconstructed = OSMDataset.from_dict(d)
        self.assertEqual(reconstructed.to_dict(), d)
        self.assertEqual(len(reconstructed.nodes), 2)
        self.assertEqual(len(reconstructed.ways), 1)
        self.assertEqual(len(reconstructed.relations), 1)
        self.assertTrue(reconstructed.ways[101].is_boundary_cut)

        # Test to_json -> from_json
        j = ds.to_json()
        reconstructed_json = OSMDataset.from_json(j)
        self.assertEqual(reconstructed_json.to_dict(), d)

    def test_schema_validation_runs_on_valid_model(self) -> None:
        ds = OSMDataset(provenance=self.provenance, clip_lineage=self.clip)
        ds.add_node(OSMNode(id=1, lat=-25.4, lon=-49.2, tags={"name": "Node 1"}))
        ds.add_way(OSMWay(id=10, nodes=[1], tags={"highway": "residential"}))
        ds.add_relation(OSMRelation(id=100, members=[RelationMember(type="way", ref=10, role="outer")]))
        # Should execute cleanly without schema violation
        self.assertIs(ds.validate_against_schema(self.schema_path), True)

    def test_invalid_model_fixture_fails_json_schema_validation(self) -> None:
        ds = OSMDataset(provenance=self.provenance, clip_lineage=self.clip)
        ds.schema_version = 2
        with self.assertRaisesRegex(OSMModelError, "schema validation failed"):
            ds.validate_against_schema(self.schema_path)

    def test_missing_jsonschema_dependency_is_reported_as_unvalidated(self) -> None:
        ds = OSMDataset(provenance=self.provenance, clip_lineage=self.clip)
        with patch.dict(sys.modules, {"jsonschema": None}):
            self.assertIs(ds.validate_against_schema(self.schema_path), False)

    def test_version_policy_rejection(self) -> None:
        valid_dict = OSMDataset(provenance=self.provenance).to_dict()

        for invalid_version in (0, 2, "1", None):
            bad_data = dict(valid_dict)
            bad_data["schema_version"] = invalid_version
            with self.assertRaises(OSMModelError):
                OSMDataset.from_dict(bad_data)

    def test_coordinate_and_id_bounds_validation(self) -> None:
        with self.assertRaises(OSMModelError):
            OSMNode(id=1, lat=91.0, lon=0.0)
        with self.assertRaises(OSMModelError):
            OSMNode(id=1, lat=-91.0, lon=0.0)
        with self.assertRaises(OSMModelError):
            OSMNode(id=1, lat=0.0, lon=181.0)
        with self.assertRaises(OSMModelError):
            OSMNode(id=1, lat=0.0, lon=-181.0)
        with self.assertRaises(OSMModelError):
            OSMNode(id="not-an-int", lat=0.0, lon=0.0)  # type: ignore

        with self.assertRaises(OSMModelError):
            OSMWay(id=1, nodes=["bad-ref"])  # type: ignore

        with self.assertRaises(OSMModelError):
            RelationMember(type="invalid_type", ref=1)

    def test_reference_audit_and_explicit_missing_refs(self) -> None:
        ds = OSMDataset(provenance=self.provenance)
        # Node 1 exists, Node 2 and Node 3 do not exist in dataset
        ds.add_node(OSMNode(id=1, lat=-25.4, lon=-49.2))
        ds.add_way(OSMWay(id=101, nodes=[1, 2, 3]))

        # Relation references Way 101 (exists), Way 999 (missing), Node 888 (missing)
        ds.add_relation(OSMRelation(
            id=501,
            members=[
                RelationMember(type="way", ref=101, role="from"),
                RelationMember(type="way", ref=999, role="to"),
                RelationMember(type="node", ref=888, role="via"),
            ],
            tags={"type": "restriction"}
        ))

        report = ds.audit_references(update_entities=True)

        self.assertTrue(report.has_missing_references)
        self.assertEqual(report.missing_node_refs, {101: [2, 3]})
        self.assertEqual(ds.ways[101].missing_nodes, [2, 3])

        self.assertIn(501, report.missing_member_refs)
        missing_members = report.missing_member_refs[501]
        self.assertEqual(len(missing_members), 2)
        self.assertEqual({m.ref for m in missing_members}, {999, 888})
        self.assertTrue(ds.relations[501].is_partial)
        self.assertEqual(len(ds.relations[501].missing_members), 2)

        self.assertEqual(report.total_nodes, 1)
        self.assertEqual(report.total_ways, 1)
        self.assertEqual(report.partial_ways_count, 1)
        self.assertEqual(report.complete_ways_count, 0)
        self.assertEqual(report.total_relations, 1)
        self.assertEqual(report.partial_relations_count, 1)
        self.assertEqual(report.complete_relations_count, 0)


if __name__ == "__main__":
    unittest.main()
