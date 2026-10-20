"""Unit tests for multi-region partitioning, seam auditing, and catalog coverage (Issue #34 / [5.3])."""

from __future__ import annotations

import unittest

from nds_forge.region.partition import (
    Gateway,
    IndexContainmentError,
    RegionPartition,
    audit_disjoint_regions,
    audit_seam_reciprocity,
    build_partition_manifest,
    compute_parent_tile_id,
    verify_index_containment,
)


class RegionPartitionTests(unittest.TestCase):
    """Test suite for region partitioning, reciprocal seam checks, and index containment."""

    def test_parent_tile_calculation(self) -> None:
        # Shift 13 -> 9: 2 * (13 - 9) = 8 bits
        child_tile = 540673410
        expected_parent = child_tile >> 8
        self.assertEqual(compute_parent_tile_id(child_tile, 13, 9), expected_parent)

    def test_verify_index_containment_success(self) -> None:
        payload_tiles = [540673410, 540673411]  # Both have same parent
        parent = 540673410 >> 8
        index_tiles = [parent]

        # Should pass without error
        verify_index_containment(
            payload_tiles=payload_tiles,
            index_tiles=index_tiles,
            payload_level=13,
            index_level=9,
            layer_name="route",
        )

    def test_verify_index_containment_missing_parent_fails(self) -> None:
        payload_tiles = [540673410]
        index_tiles = []  # Missing parent

        with self.assertRaises(IndexContainmentError) as ctx:
            verify_index_containment(
                payload_tiles=payload_tiles,
                index_tiles=index_tiles,
                payload_level=13,
                index_level=9,
                layer_name="route",
            )
        self.assertIn("missing parent-index tiles", str(ctx.exception))

    def test_verify_index_containment_excess_parent_fails(self) -> None:
        payload_tiles = [540673410]
        parent = 540673410 >> 8
        orphan_parent = 999999
        index_tiles = [parent, orphan_parent]  # Orphan parent

        with self.assertRaises(IndexContainmentError) as ctx:
            verify_index_containment(
                payload_tiles=payload_tiles,
                index_tiles=index_tiles,
                payload_level=13,
                index_level=9,
                layer_name="route",
            )
        self.assertIn("excess/orphaned parent-index tiles", str(ctx.exception))

    def test_seam_reciprocity_audit_matched_and_orphan(self) -> None:
        # 1. Perfectly reciprocal seam between tile A and tile B
        gw_a = Gateway(source_tile=100, target_tile=200, link_id=1, coord_lon=-49.27, coord_lat=-25.43)
        gw_b = Gateway(source_tile=200, target_tile=100, link_id=2, coord_lon=-49.27, coord_lat=-25.43)

        audit_ok = audit_seam_reciprocity([gw_a, gw_b])
        self.assertTrue(audit_ok["is_reciprocal"])
        self.assertEqual(audit_ok["matched_gateways"], 1)
        self.assertEqual(len(audit_ok["orphans"]), 0)

        # 2. Asymmetric seam: gw_a without reciprocal
        audit_orphan = audit_seam_reciprocity([gw_a])
        self.assertFalse(audit_orphan["is_reciprocal"])
        self.assertEqual(len(audit_orphan["orphans"]), 1)
        self.assertEqual(audit_orphan["orphans"][0]["source_tile"], 100)

    def test_two_separated_real_regions_disjoint_check(self) -> None:
        # Region 1: Curitiba (UR439)
        curitiba = RegionPartition(
            region_id=439,
            region_name="Curitiba",
            layer_tiles={
                "bmd": [135168352],
                "route": [540673410],
            },
            layer_index_tiles={
                "route": [540673410 >> 8],
            },
        )

        # Region 2: Londrina (separated distinct licensed region in Paraná)
        londrina = RegionPartition(
            region_id=440,
            region_name="Londrina",
            layer_tiles={
                "bmd": [136000000],
                "route": [544000000],
            },
            layer_index_tiles={
                "route": [544000000 >> 8],
            },
        )

        audit = audit_disjoint_regions([curitiba, londrina])
        self.assertTrue(audit["are_disjoint"])
        self.assertEqual(len(audit["overlaps"]), 0)

    def test_overlapping_regions_detected(self) -> None:
        part_1 = RegionPartition(region_id=1, region_name="R1", layer_tiles={"route": [1000]})
        part_2 = RegionPartition(region_id=2, region_name="R2", layer_tiles={"route": [1000]})

        audit = audit_disjoint_regions([part_1, part_2])
        self.assertFalse(audit["are_disjoint"])
        self.assertEqual(len(audit["overlaps"]), 1)
        self.assertEqual(audit["overlaps"][0]["tile_id"], 1000)

    def test_build_partition_manifest_end_to_end(self) -> None:
        gw_1 = Gateway(source_tile=10, target_tile=20, link_id=101, coord_lon=-49.0, coord_lat=-25.0)
        gw_2 = Gateway(source_tile=20, target_tile=10, link_id=102, coord_lon=-49.0, coord_lat=-25.0)

        curitiba = RegionPartition(
            region_id=439,
            region_name="Curitiba",
            layer_tiles={"route": [540673410]},
            layer_index_tiles={"route": [540673410 >> 8]},
            gateways=[gw_1, gw_2],
        )

        layer_rules = {
            "route": {"payload_level": 13, "index_level": 9},
        }

        manifest = build_partition_manifest([curitiba], layer_rules)
        self.assertEqual(manifest["step"], "region.partition")
        self.assertTrue(manifest["seam_audit"]["is_reciprocal"])
        self.assertTrue(manifest["disjoint_audit"]["are_disjoint"])
        self.assertEqual(len(manifest["regions"]), 1)


if __name__ == "__main__":
    unittest.main()
