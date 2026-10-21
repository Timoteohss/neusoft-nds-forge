"""SDK integration must fail closed until a real adapter exists."""
from datetime import datetime, timezone
import unittest
from nds_forge.validation.sdk_route import (
    GPSFix, RouteCalculationRequest, RouteDestination, SDKRouteHarness,
)

class SDKRouteSuiteTests(unittest.TestCase):
    def test_no_sdk_adapter_never_manufactures_success_or_coverage(self):
        gps = GPSFix(-25.4284, -49.2733, datetime.now(timezone.utc).isoformat(), 4.5)
        for lat, lon in ((-25.4295, -49.2680), (-25.5, -49.32), (-23.55, -46.63)):
            with self.subTest(destination=(lat, lon)):
                request = RouteCalculationRequest(
                    gps, RouteDestination(lat, lon, "Synthetic destination"),
                    candidate_hash="nonexistent-candidate")
                result = SDKRouteHarness().calculate_route(request)
                self.assertEqual(result.status, "UNSUPPORTED_SDK_ADAPTER")
                self.assertEqual(result.distance_meters, 0)
                self.assertEqual(result.estimated_seconds, 0)
                self.assertFalse(result.is_same_tile)
                self.assertFalse(result.is_cross_tile)
                self.assertFalse(result.guidance_enabled)
                self.assertEqual(result.parameter_echo["candidate_hash"], "nonexistent-candidate")

    def test_guidance_enabled_strictly_prohibited(self):
        gps = GPSFix(-25.4, -49.2, "2026-10-20T00:00:00Z", 5)
        with self.assertRaises(ValueError):
            RouteCalculationRequest(gps, RouteDestination(-25.4, -49.2, "Dest"),
                                    "candidate", guidance_enabled=True)

    def test_invalid_gps_fix_rejected(self):
        for lat, accuracy in ((-25.4, -1), (100, 5)):
            with self.assertRaises(ValueError):
                GPSFix(lat, -49.2, "2026-10-20T00:00:00Z", accuracy)

if __name__ == "__main__":
    unittest.main()
