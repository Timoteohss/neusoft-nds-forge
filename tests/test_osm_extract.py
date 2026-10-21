from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import osmium
    from shapely.geometry import box, mapping
except ImportError:  # Optional adapter dependencies; default core stays stdlib-only.
    osmium = None
    box = None
    mapping = None

from nds_forge.adapters.osm_extract import (
    OSM_CLIP_CAPABILITIES_MISSING,
    OSM_CLIP_CAPABILITIES_PRODUCED,
    OSMExtractError,
    clip_pbf,
)
from nds_forge.contracts import RegionSpec, StageContext as OSMStageContext
from nds_forge.osm_model import OSMDataset


@unittest.skipUnless(osmium is not None and box is not None,
                     "optional clipping smoke requires pyosmium and shapely")
class OSMExtractSmokeTests(unittest.TestCase):
    """Synthetic valid PBF coverage for the experimental clipping adapter only."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pbf = self.root / "tiny-synthetic.osm.pbf"
        self._write_fixture(self.pbf)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _write_fixture(path: Path) -> None:
        writer = osmium.SimpleWriter(str(path))
        points = {
            1: (-2.0, 0.0), 2: (2.0, 0.0),
            3: (-0.5, -0.5), 4: (0.5, 0.5),
            5: (2.0, 2.0), 6: (3.0, 3.0),
            7: (-1.5, 0.5), 8: (1.5, 0.5),
        }
        for node_id, location in points.items():
            writer.add_node(osmium.osm.mutable.Node(
                id=node_id, location=location, version=1,
                tags={"fixture": "synthetic"},
            ))
        ways = (
            (100, [1, 2], {"highway": "primary", "name": "Crossing fixture",
                           "oneway": "yes"}),
            (101, [3, 4], {"highway": "residential", "name": "Inside fixture"}),
            (102, [5, 6], {"highway": "secondary", "name": "Outside fixture"}),
            (103, [7, 8], {"name": "Not a road"}),
        )
        for way_id, nodes, tags in ways:
            writer.add_way(osmium.osm.mutable.Way(
                id=way_id, nodes=nodes, version=1, tags=tags,
            ))
        writer.close()

    def _context(self, output_dir: Path, region: RegionSpec) -> OSMStageContext:
        return OSMStageContext(
            source_path=self.pbf,
            region=region,
            output_dir=output_dir,
            license="ODbL-1.0",
            attribution="© OpenStreetMap contributors (synthetic test fixture)",
        )

    def test_bbox_clips_crossing_way_and_preserves_source_tags(self) -> None:
        output = self.root / "bbox-output"
        region = RegionSpec("bbox-fixture", "bbox fixture", (-1, -1, 1, 1), None, True)
        report = clip_pbf(self._context(output, region))
        self.assertEqual(tuple(report["capabilities_produced"]), OSM_CLIP_CAPABILITIES_PRODUCED)
        self.assertEqual(tuple(report["capabilities_missing"]), OSM_CLIP_CAPABILITIES_MISSING)
        collection = json.loads((output / "roads.geojson").read_text(encoding="utf-8"))
        self.assertEqual([feature["properties"]["osm_id"] for feature in collection["features"]],
                         [100, 101])
        crossing = collection["features"][0]
        self.assertEqual(crossing["properties"]["name"], "Crossing fixture")
        self.assertEqual(crossing["properties"]["oneway"], "yes")
        self.assertEqual(crossing["properties"]["raw_tags"]["highway"], "primary")
        self.assertEqual(crossing["geometry"]["coordinates"], [[-1.0, 0.0], [1.0, 0.0]])
        self.assertEqual(report["counts"]["clipped_parts"], 2)
        self.assertNotIn("turn_restrictions", report["capabilities_produced"])
        self.assertIn("turn_restrictions", report["capabilities_missing"])
        self.assertIn("routing_graph", report["capabilities_missing"])
        self.assertEqual(report["maturity"], "experimental")

    def test_polygon_clips_to_polygon_boundary_not_only_its_extent(self) -> None:
        output = self.root / "polygon-output"
        polygon = mapping(box(-0.5, -0.5, 0.5, 0.5))
        region = RegionSpec("polygon-fixture", "polygon fixture", None, polygon, True)
        clip_pbf(self._context(output, region))
        collection = json.loads((output / "roads.geojson").read_text(encoding="utf-8"))
        crossing = next(feature for feature in collection["features"]
                        if feature["properties"]["osm_id"] == 100)
        self.assertEqual(crossing["geometry"]["coordinates"], [[-0.5, 0.0], [0.5, 0.0]])
        self.assertEqual({f["properties"]["osm_id"] for f in collection["features"]}, {100, 101})

    def test_point_predicates_use_prepared_boundary_and_preserve_boundary_covers(self) -> None:
        from shapely.prepared import prep as real_prep

        pbf = self.root / "prepared-boundary.osm.pbf"
        writer = osmium.SimpleWriter(str(pbf))
        for node_id, location, tags in (
            (1, (1.0, 0.5), {"amenity": "cafe"}),  # exactly on east boundary
            (2, (1.01, 0.5), {"amenity": "cafe"}),  # just outside
            (3, (0.0, 0.0), {}),
            (4, (1.5, 0.0), {}),
            (5, (0.8, 0.5), {}),
            (6, (1.2, 0.5), {}),
        ):
            writer.add_node(osmium.osm.mutable.Node(
                id=node_id, location=location, version=1, tags=tags,
            ))
        writer.add_way(osmium.osm.mutable.Way(
            id=10, nodes=[3, 4], version=1,
            tags={"highway": "residential", "name": "Boundary crossing"},
        ))
        writer.add_way(osmium.osm.mutable.Way(
            id=11, nodes=[5, 6], version=1, tags={"shop": "books"},
        ))
        writer.close()

        calls = {"prepare": 0, "covers": 0, "intersects": 0}

        class PreparedBoundaryProbe:
            def __init__(self, geometry):
                self.geometry = real_prep(geometry)

            def covers(self, other):
                calls["covers"] += 1
                return self.geometry.covers(other)

            def intersects(self, other):
                calls["intersects"] += 1
                return self.geometry.intersects(other)

        def prepare(geometry):
            calls["prepare"] += 1
            return PreparedBoundaryProbe(geometry)

        output = self.root / "prepared-boundary-output"
        region = RegionSpec("bbox-boundary", "bbox boundary", (0, -1, 1, 1), None, True)
        with patch("shapely.prepared.prep", side_effect=prepare):
            clip_pbf(self._context_for(pbf, output, region))

        dataset = OSMDataset.from_json((output / "osm-dataset.json").read_text(encoding="utf-8"))
        self.assertIn(1, dataset.nodes, "covers semantics must retain a POI on the boundary")
        self.assertNotIn(2, dataset.nodes, "a point outside the boundary must remain excluded")
        self.assertIn(11, dataset.ways, "a POI way whose representative lies on boundary is retained")
        self.assertEqual(calls["prepare"], 1, "prepare the regional geometry once per clip")
        self.assertGreater(calls["covers"], 0, "point inclusion checks use prepared covers")
        self.assertGreater(calls["intersects"], 0, "connected-node checks use prepared intersects")

    def _context_for(self, pbf: Path, output: Path, region: RegionSpec) -> OSMStageContext:
        return OSMStageContext(
            source_path=pbf, region=region, output_dir=output,
            license="ODbL-1.0", attribution="© OpenStreetMap contributors (synthetic test fixture)",
        )

    def test_unresolved_region_fails_without_output(self) -> None:
        output = self.root / "unresolved-output"
        region = RegionSpec("relation-fixture", "unresolved relation", None, None, False)
        with self.assertRaisesRegex(OSMExtractError, "ainda não está resolvida"):
            clip_pbf(self._context(output, region))
        self.assertFalse(output.exists())

    def test_cli_pipeline_runs_experimental_clip_with_explicit_opt_in(self) -> None:
        workspace = self.root / "pipeline-work"
        config_path = self.root / "pipeline.toml"
        config_path.write_text(
            "\n".join([
                "schema_version = 1",
                'project_id = "tiny-osm-clip-smoke"',
                f"workspace = {json.dumps(str(workspace))}",
                "",
                "[region]",
                'id = "synthetic-bbox"',
                'name = "synthetic bbox"',
                'source = "bbox"',
                "bbox = [-1.0, -1.0, 1.0, 1.0]",
                "",
                "[source]",
                'kind = "pbf"',
                f"path = {json.dumps(str(self.pbf))}",
                'license = "ODbL-1.0"',
                'attribution = "© OpenStreetMap contributors (synthetic test fixture)"',
                "",
                "[pipeline]",
                'target = "osm.clip"',
                "enable_experimental = false",
                "",
            ]) + "\n",
            encoding="utf-8",
        )
        repo_root = Path(__file__).resolve().parents[1]
        blocked = subprocess.run(
            [sys.executable, "-m", "nds_forge", "run", "--config", str(config_path),
             "--target", "osm.clip"],
            cwd=repo_root, text=True, capture_output=True, timeout=30, check=False,
        )
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("allow-experimental", blocked.stderr)
        self.assertFalse(workspace.exists())

        executed = subprocess.run(
            [sys.executable, "-m", "nds_forge", "run", "--config", str(config_path),
             "--target", "osm.clip", "--allow-experimental"],
            cwd=repo_root, text=True, capture_output=True, timeout=30, check=False,
        )
        self.assertEqual(executed.returncode, 0, executed.stderr)
        run_result = json.loads(executed.stdout)
        self.assertEqual(run_result["overall_validation"], "unvalidated")
        self.assertEqual(run_result["target"], "osm.clip")
        report = json.loads((workspace / "artifacts" / "clipped-osm" / "clip-report.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(report["maturity"], "experimental")
        self.assertEqual(report["counts"]["clipped_parts"], 2)
        features = json.loads((workspace / "artifacts" / "clipped-osm" / "roads.geojson")
                              .read_text(encoding="utf-8"))["features"]
        self.assertEqual({feature["properties"]["osm_id"] for feature in features}, {100, 101})

        validated = subprocess.run(
            [sys.executable, "-m", "nds_forge", "validate", "--config", str(config_path),
             "--step", "osm.clip"],
            cwd=repo_root, text=True, capture_output=True, timeout=30, check=False,
        )
        self.assertEqual(validated.returncode, 0, validated.stderr)
        self.assertEqual(json.loads(validated.stdout)["validation_status"], "partial")


@unittest.skipUnless(osmium is not None and box is not None,
                     "optional clipping smoke requires pyosmium and shapely")
class OSMModelExtractionTests(unittest.TestCase):
    """Synthetic PBF tests verifying canonical loss-aware OSM extraction (Issue #10 / [2.2])."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pbf = self.root / "loss-aware-fixture.osm.pbf"
        self._write_fixture(self.pbf)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _write_fixture(path: Path) -> None:
        writer = osmium.SimpleWriter(str(path))
        # Nodes: 1 & 2 outside region [-1, 1], 3 & 4 inside
        points = {
            1: (-2.0, 0.0), 2: (2.0, 0.0),
            3: (-0.5, -0.5), 4: (0.5, 0.5),
        }
        for node_id, location in points.items():
            writer.add_node(osmium.osm.mutable.Node(
                id=node_id, location=location, version=1,
                tags={"fixture": "synthetic"},
            ))
        ways = (
            # Border clipping: way crosses boundary
            (201, [1, 2], {"highway": "primary", "name": "Boundary Crossing"}),
            # Reversed way: oneway=-1
            (202, [4, 3], {"highway": "residential", "name": "Reversed Way", "oneway": "-1"}),
            # Bridge and layer tags
            (203, [3, 4], {"highway": "secondary", "name": "Bridge Overpass", "bridge": "yes", "layer": "1"}),
            # Way referencing missing node 999
            (204, [3, 4, 999], {"highway": "tertiary", "name": "Partial Way With Missing Node"}),
        )
        for way_id, nodes, tags in ways:
            writer.add_way(osmium.osm.mutable.Way(
                id=way_id, nodes=nodes, version=1, tags=tags,
            ))
        relations = (
            # 1. Restriction with via-node
            (301, [('w', 201, 'from'), ('n', 3, 'via'), ('w', 202, 'to')],
             {"type": "restriction", "restriction": "no_left_turn"}),
            # 2. Restriction with via-way
            (302, [('w', 201, 'from'), ('w', 203, 'via'), ('w', 202, 'to')],
             {"type": "restriction", "restriction": "no_u_turn"}),
            # 3. Boundary relation
            (303, [('w', 201, 'outer'), ('w', 202, 'inner')],
             {"type": "boundary", "boundary": "administrative", "admin_level": "8"}),
            # 4. Address relation (associatedStreet)
            (304, [('w', 201, 'street'), ('n', 3, 'house')],
             {"type": "associatedStreet", "name": "Rua das Flores"}),
            # 5. Relation with missing member (way 99999 is missing)
            (305, [('w', 201, 'from'), ('n', 3, 'via'), ('w', 99999, 'to')],
             {"type": "restriction", "restriction": "only_straight_on"}),
        )
        for rel_id, members, tags in relations:
            writer.add_relation(osmium.osm.mutable.Relation(
                id=rel_id, members=members, version=1, tags=tags,
            ))
        writer.close()

    def test_pbf_extracts_canonical_model_with_cut_and_missing_markers(self) -> None:
        output = self.root / "model-output"
        region = RegionSpec("bbox-fixture", "bbox fixture", (-1.0, -1.0, 1.0, 1.0), None, True)
        context = OSMStageContext(
            source_path=self.pbf,
            region=region,
            output_dir=output,
            license="ODbL-1.0",
            attribution="© OpenStreetMap contributors",
        )
        report = clip_pbf(context)

        # 1. Output files exist
        self.assertTrue((output / "roads.geojson").is_file())
        self.assertTrue((output / "clip-report.json").is_file())
        self.assertTrue((output / "osm-dataset.json").is_file())

        # 2. Check report metadata
        self.assertIn("osm-dataset.json", report["outputs"])
        self.assertIn("connected_nodes", report["capabilities_produced"])
        self.assertIn("canonical_osm_dataset", report["capabilities_produced"])
        self.assertGreaterEqual(report["counts"]["boundary_cut_ways"], 1)
        self.assertGreaterEqual(report["counts"]["ways_with_missing_nodes"], 1)

        # 3. Load canonical OSM dataset
        dataset = OSMDataset.from_json((output / "osm-dataset.json").read_text(encoding="utf-8"))
        dataset.validate_against_schema()

        # 4. Verify reversed way
        self.assertIn(202, dataset.ways)
        self.assertEqual(dataset.ways[202].tags.get("oneway"), "-1")
        self.assertEqual(dataset.ways[202].nodes, [4, 3])

        # 5. Verify bridge and layer attributes
        self.assertIn(203, dataset.ways)
        self.assertEqual(dataset.ways[203].tags.get("bridge"), "yes")
        self.assertEqual(dataset.ways[203].tags.get("layer"), "1")

        # 6. Verify boundary clipping marker
        self.assertIn(201, dataset.ways)
        self.assertTrue(dataset.ways[201].is_boundary_cut)
        # Node 1 and Node 2 are outside the bbox [-1, 1], so they are marked is_boundary_cut
        self.assertTrue(dataset.nodes[1].is_boundary_cut)
        self.assertTrue(dataset.nodes[2].is_boundary_cut)
        # Node 3 and Node 4 are inside
        self.assertFalse(dataset.nodes[3].is_boundary_cut)
        self.assertFalse(dataset.nodes[4].is_boundary_cut)

        # 7. Verify missing nodes tracking
        self.assertIn(204, dataset.ways)
        self.assertEqual(dataset.ways[204].missing_nodes, [999])
        self.assertTrue(dataset.ways[204].is_boundary_cut)

        # 8. Verify deterministic IDs and connected nodes presence
        self.assertEqual(set(dataset.nodes.keys()), {1, 2, 3, 4})
        self.assertEqual(set(dataset.ways.keys()), {201, 202, 203, 204})

    def test_relations_extraction_roles_and_missing_members(self) -> None:
        output = self.root / "relations-output"
        region = RegionSpec("bbox-fixture", "bbox fixture", (-1.0, -1.0, 1.0, 1.0), None, True)
        context = OSMStageContext(
            source_path=self.pbf,
            region=region,
            output_dir=output,
            license="ODbL-1.0",
            attribution="© OpenStreetMap contributors",
        )
        report = clip_pbf(context)

        # Raw relation retention is not semantic turn-restriction compilation.
        self.assertIn("relations", report["capabilities_produced"])
        self.assertIn("restriction_relations_raw", report["capabilities_produced"])
        self.assertNotIn("turn_restrictions", report["capabilities_produced"])
        self.assertNotIn("relations", report["capabilities_missing"])
        self.assertIn("turn_restrictions", report["capabilities_missing"])
        self.assertTrue(any("not compiled" in limitation.lower()
                            for limitation in report["limitations"]))
        self.assertEqual(tuple(report["capabilities_produced"]), OSM_CLIP_CAPABILITIES_PRODUCED)
        self.assertEqual(tuple(report["capabilities_missing"]), OSM_CLIP_CAPABILITIES_MISSING)

        # Counts
        self.assertEqual(report["counts"]["relations_seen"], 5)
        self.assertEqual(report["counts"]["relations_collected"], 5)
        self.assertEqual(report["counts"]["restrictions_collected"], 3)
        self.assertEqual(report["counts"]["boundaries_collected"], 1)
        self.assertEqual(report["counts"]["addresses_collected"], 1)
        self.assertEqual(report["counts"]["complete_relations"], 4)
        self.assertEqual(report["counts"]["partial_relations"], 1)

        # Load dataset
        dataset = OSMDataset.from_json((output / "osm-dataset.json").read_text(encoding="utf-8"))
        dataset.validate_against_schema()

        # 1. Via-node restriction (301)
        rel_301 = dataset.relations[301]
        self.assertEqual(rel_301.tags["type"], "restriction")
        self.assertEqual(rel_301.tags["restriction"], "no_left_turn")
        self.assertEqual(
            [(m.type, m.ref, m.role) for m in rel_301.members],
            [("way", 201, "from"), ("node", 3, "via"), ("way", 202, "to")],
        )
        self.assertFalse(rel_301.is_partial)
        self.assertEqual(rel_301.missing_members, [])

        # 2. Via-way restriction (302)
        rel_302 = dataset.relations[302]
        self.assertEqual(rel_302.tags["type"], "restriction")
        self.assertEqual(rel_302.tags["restriction"], "no_u_turn")
        self.assertEqual(
            [(m.type, m.ref, m.role) for m in rel_302.members],
            [("way", 201, "from"), ("way", 203, "via"), ("way", 202, "to")],
        )
        self.assertFalse(rel_302.is_partial)

        # 3. Boundary relation (303)
        rel_303 = dataset.relations[303]
        self.assertEqual(rel_303.tags["type"], "boundary")
        self.assertEqual(
            [(m.type, m.ref, m.role) for m in rel_303.members],
            [("way", 201, "outer"), ("way", 202, "inner")],
        )
        self.assertFalse(rel_303.is_partial)

        # 4. Address relation (304)
        rel_304 = dataset.relations[304]
        self.assertEqual(rel_304.tags["type"], "associatedStreet")
        self.assertEqual(
            [(m.type, m.ref, m.role) for m in rel_304.members],
            [("way", 201, "street"), ("node", 3, "house")],
        )
        self.assertFalse(rel_304.is_partial)

        # 5. Missing member relation (305) - way 99999 was not extracted
        rel_305 = dataset.relations[305]
        self.assertTrue(rel_305.is_partial)
        self.assertEqual(len(rel_305.missing_members), 1)
        self.assertEqual(rel_305.missing_members[0].type, "way")
        self.assertEqual(rel_305.missing_members[0].ref, 99999)
        self.assertEqual(rel_305.missing_members[0].role, "to")
        # Member order preserved
        self.assertEqual(
            [(m.type, m.ref, m.role) for m in rel_305.members],
            [("way", 201, "from"), ("node", 3, "via"), ("way", 99999, "to")],
        )


if __name__ == "__main__":
    unittest.main()
