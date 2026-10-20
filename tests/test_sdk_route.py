"""Unit tests for SDK Route calculation suite (Issue #39 / [6.4])."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest

from nds_forge.validation.sdk_route import (
    GPSFix,
    RouteCalculationRequest,
    RouteDestination,
    SDKRouteHarness,
    compute_level13_tile,
)


class SDKRouteSuiteTests(unittest.TestCase):
    """Test suite for SDK route calculation harness, tile transitions, and municipal scope."""

    def setUp(self) -> None:
        self.harness = SDKRouteHarness()
        self.candidate_hash = "3a7b9c1d0f8e4a2b6c5d7e9f1a3b5c7d9e1f3a5b7c9d1e3f5a7b9c1d0f8e4a2b"

    def test_same_tile_route_calculation_success(self) -> None:
        # Central Curitiba: Praça Tiradentes to Praça Santos Andrade (close, same tile)
        gps = GPSFix(
            lat=-25.4284,
            lon=-49.2733,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            accuracy_meters=4.5,
        )
        dest = RouteDestination(lat=-25.4295, lon=-49.2680, name="Praça Santos Andrade")

        req = RouteCalculationRequest(
            start_gps=gps,
            destination=dest,
            candidate_hash=self.candidate_hash,
            route_preference="fastest",
            guidance_enabled=False,
        )

        res = self.harness.calculate_route(req)
        self.assertEqual(res.status, "SUCCESSFUL_LOCAL")
        self.assertTrue(res.is_same_tile)
        self.assertFalse(res.is_cross_tile)
        self.assertFalse(res.guidance_enabled)
        self.assertGreater(res.distance_meters, 400.0)
        self.assertGreater(res.estimated_seconds, 10.0)
        self.assertEqual(res.candidate_hash, self.candidate_hash)

        # Verify parameter echo
        echo = res.parameter_echo
        self.assertEqual(echo["preference"], "fastest")
        self.assertEqual(echo["destination"]["name"], "Praça Santos Andrade")

    def test_cross_tile_route_calculation_success(self) -> None:
        # Central Curitiba to Santa Felicidade / CIC (crosses level 13 tile boundaries)
        gps = GPSFix(
            lat=-25.4284,
            lon=-49.2733,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            accuracy_meters=3.0,
        )
        dest = RouteDestination(lat=-25.5000, lon=-49.3200, name="Parque Barigui / CIC")

        req = RouteCalculationRequest(
            start_gps=gps,
            destination=dest,
            candidate_hash=self.candidate_hash,
            route_preference="shortest",
            guidance_enabled=False,
        )

        res = self.harness.calculate_route(req)
        self.assertEqual(res.status, "SUCCESSFUL_LOCAL")
        self.assertTrue(res.is_cross_tile)
        self.assertFalse(res.is_same_tile)
        self.assertGreater(res.distance_meters, 5000.0)

    def test_out_of_coverage_destination_fails(self) -> None:
        gps = GPSFix(
            lat=-25.4284,
            lon=-49.2733,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            accuracy_meters=5.0,
        )
        # São Paulo coordinates (out of Curitiba coverage)
        dest = RouteDestination(lat=-23.5505, lon=-46.6333, name="São Paulo")

        req = RouteCalculationRequest(
            start_gps=gps,
            destination=dest,
            candidate_hash=self.candidate_hash,
            guidance_enabled=False,
        )

        res = self.harness.calculate_route(req)
        self.assertEqual(res.status, "FAILED_DESTINATION_OUT_OF_COVERAGE")
        self.assertEqual(res.distance_meters, 0.0)

    def test_guidance_enabled_strictly_prohibited_under_gate_6_4(self) -> None:
        gps = GPSFix(
            lat=-25.4284,
            lon=-49.2733,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            accuracy_meters=5.0,
        )
        dest = RouteDestination(lat=-25.4295, lon=-49.2680, name="Dest")

        with self.assertRaises(ValueError) as ctx:
            RouteCalculationRequest(
                start_gps=gps,
                destination=dest,
                candidate_hash=self.candidate_hash,
                guidance_enabled=True,  # Prohibited under gate 6.4
            )
        self.assertIn("guidance_enabled must strictly be False", str(ctx.exception))

    def test_invalid_gps_fix_rejected(self) -> None:
        # Negative accuracy
        with self.assertRaises(ValueError):
            GPSFix(lat=-25.4, lon=-49.2, timestamp_utc="2026-10-20T00:00:00Z", accuracy_meters=-1.0)
        # Latitude out of bounds
        with self.assertRaises(ValueError):
            GPSFix(lat=100.0, lon=-49.2, timestamp_utc="2026-10-20T00:00:00Z", accuracy_meters=5.0)


if __name__ == "__main__":
    unittest.main()
