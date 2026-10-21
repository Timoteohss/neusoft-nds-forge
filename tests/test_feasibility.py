"""Unit tests for tile and profile feasibility measurement (Issue #33 / [5.2])."""

from __future__ import annotations

import unittest

from nds_forge.feasibility import (
    TileMetrics,
    TileOverflowError,
    build_candidate_profile_report,
    evaluate_tile_feasibility,
)


class FeasibilityTests(unittest.TestCase):
    """Test suite for tile complexity thresholds, overflow checks, and profile reports."""

    def test_nominal_tile_is_feasible(self) -> None:
        metrics = TileMetrics(
            tile_id=540673410,
            level=13,
            intersection_count=1200,
            link_count=2500,
            max_degree=4,
            external_tile_count=8,
            aux_bytes=50000,
        )
        report = evaluate_tile_feasibility(metrics)
        self.assertTrue(report.is_feasible)
        self.assertEqual(len(report.overflows), 0)
        self.assertEqual(len(report.warnings), 0)

    def test_link_count_overflow_detected(self) -> None:
        metrics = TileMetrics(
            tile_id=540673410,
            level=13,
            intersection_count=1000,
            link_count=35000,  # Exceeds 32767
            max_degree=4,
            external_tile_count=8,
        )
        report = evaluate_tile_feasibility(metrics)
        self.assertFalse(report.is_feasible)
        self.assertIn("Link count 35000 exceeds limit 32767", report.overflows[0])

        with self.assertRaises(TileOverflowError):
            evaluate_tile_feasibility(metrics, raise_on_overflow=True)

    def test_intersection_count_overflow_detected(self) -> None:
        metrics = TileMetrics(
            tile_id=540673410,
            level=13,
            intersection_count=33000,  # Exceeds 32767
            link_count=1000,
            max_degree=4,
            external_tile_count=8,
        )
        report = evaluate_tile_feasibility(metrics)
        self.assertFalse(report.is_feasible)
        self.assertIn("Intersection count 33000 exceeds limit", report.overflows[0])

    def test_node_degree_overflow_detected(self) -> None:
        metrics = TileMetrics(
            tile_id=540673410,
            level=13,
            intersection_count=500,
            link_count=1000,
            max_degree=24,  # Exceeds 16
            external_tile_count=8,
        )
        report = evaluate_tile_feasibility(metrics)
        self.assertFalse(report.is_feasible)
        self.assertIn("Node degree 24 exceeds limit 16", report.overflows[0])

    def test_external_tile_ordinal_overflow_detected(self) -> None:
        metrics = TileMetrics(
            tile_id=540673410,
            level=13,
            intersection_count=500,
            link_count=1000,
            max_degree=4,
            external_tile_count=300,  # Exceeds 255
        )
        report = evaluate_tile_feasibility(metrics)
        self.assertFalse(report.is_feasible)
        self.assertIn("External tile count 300 exceeds 8-bit ordinal limit 255", report.overflows[0])

    def test_aux_bytes_overflow_detected(self) -> None:
        metrics = TileMetrics(
            tile_id=540673410,
            level=13,
            intersection_count=500,
            link_count=1000,
            max_degree=4,
            external_tile_count=10,
            aux_bytes=20 * 1024 * 1024,  # 20 MiB exceeds 16 MiB
        )
        report = evaluate_tile_feasibility(metrics)
        self.assertFalse(report.is_feasible)
        self.assertIn("AUX geometry bytes 20971520 exceeds limit 16777216", report.overflows[0])

    def test_regional_report_level13_vs_level14_evidence(self) -> None:
        sample_tiles = [
            TileMetrics(
                tile_id=540673410,
                level=13,
                intersection_count=500,
                link_count=1200,
                max_degree=4,
                external_tile_count=6,
            ),
        ]

        # Level 13 report has verified SDK evidence
        rep13 = build_candidate_profile_report(sample_tiles, profile_level=13, profile_name="neusoft-curitiba-v9")
        self.assertTrue(rep13["sdk_evidence_verified"])
        self.assertIn("Level 13 has verified Curitiba SDK route calculation evidence", rep13["evidence_note"])
        self.assertFalse(rep13["national_inference_allowed"])

        # Level 14 candidate profile lacks SDK evidence
        rep14 = build_candidate_profile_report(sample_tiles, profile_level=14, profile_name="candidate-national-v14")
        self.assertFalse(rep14["sdk_evidence_verified"])
        self.assertIn("Level 14 lacks validated SDK route calculation evidence", rep14["evidence_note"])
        self.assertFalse(rep14["national_inference_allowed"])


if __name__ == "__main__":
    unittest.main()
