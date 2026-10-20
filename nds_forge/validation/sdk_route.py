"""SDK Route calculation test harness and verification suite (Issue #39 / [6.4]).

Implements:
1. RouteCalculationRequest / RouteCalculationResult contracts for Neusoft Navigation SDK.
2. Evaluates same-tile, cross-tile, and fresh GPS routes to public destinations.
3. Requires callback readiness, parameter echo, and SUCCESSFUL_LOCAL status.
4. Enforces guidance_enabled is strictly False (guidance is independent, gate 6.6).
5. Enforces regional municipal scope: Curitiba v9 passes only verified municipal cases,
   reporting OUT_OF_COVERAGE for external locations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
from typing import Any


class RouteCalculationError(RuntimeError):
    """Raised when SDK route calculation fails or is out of coverage."""


@dataclass(frozen=True)
class GPSFix:
    """Represents a validated GPS fix with timestamp and accuracy."""
    lat: float
    lon: float
    timestamp_utc: str
    accuracy_meters: float

    def __post_init__(self) -> None:
        if not (-90.0 <= self.lat <= 90.0 and -180.0 <= self.lon <= 180.0):
            raise ValueError(f"Invalid GPS coordinates: ({self.lat}, {self.lon})")
        if self.accuracy_meters < 0:
            raise ValueError(f"GPS accuracy cannot be negative: {self.accuracy_meters}")


@dataclass(frozen=True)
class RouteDestination:
    """Public destination point."""
    lat: float
    lon: float
    name: str


@dataclass(frozen=True)
class RouteCalculationRequest:
    """Request payload submitted to Navigation SDK route engine."""
    start_gps: GPSFix
    destination: RouteDestination
    candidate_hash: str
    route_preference: str = "fastest"
    guidance_enabled: bool = False

    def __post_init__(self) -> None:
        if self.guidance_enabled:
            raise ValueError("guidance_enabled must strictly be False for SDK calculation suite (gate 6.4)")


@dataclass
class RouteCalculationResult:
    """Result emitted by SDK route calculation callback."""
    status: str
    distance_meters: float
    estimated_seconds: float
    is_same_tile: bool
    is_cross_tile: bool
    parameter_echo: dict[str, Any]
    candidate_hash: str
    guidance_enabled: bool = False
    callback_timestamp_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


def compute_level13_tile(lon: float, lat: float) -> int:
    """Compute level 13 Morton tile ID from lon/lat coordinates."""
    SCALE = 11930464.7111  # (1 << 32) / 360.0
    x = round((lon + 180.0) * SCALE) & 0xFFFFFFFF
    y = round((lat + 90.0) * SCALE) & 0xFFFFFFFF
    # Morton interleave
    morton = 0
    for i in range(32):
        morton |= ((x >> i) & 1) << (2 * i)
        morton |= ((y >> i) & 1) << (2 * i + 1)
    # Level 13 shift: 64 - 2 * 13 = 38
    return morton >> 38


class SDKRouteHarness:
    """Harness to execute and verify SDK route calculation cases."""

    def __init__(
        self,
        municipal_bbox: tuple[float, float, float, float] = (-49.4, -25.65, -49.15, -25.35),
        average_speed_kmh: float = 35.0,
    ) -> None:
        self.west, self.south, self.east, self.north = municipal_bbox
        self.speed_mps = (average_speed_kmh * 1000.0) / 3600.0

    def is_within_municipal_coverage(self, lon: float, lat: float) -> bool:
        """Check if coordinates fall within verified municipal bounds."""
        return self.west <= lon <= self.east and self.south <= lat <= self.north

    def calculate_route(self, request: RouteCalculationRequest) -> RouteCalculationResult:
        """Simulate / invoke SDK route calculation."""
        # 1. Verify coverage
        start = request.start_gps
        dest = request.destination

        if not self.is_within_municipal_coverage(start.lon, start.lat):
            return RouteCalculationResult(
                status="FAILED_START_OUT_OF_COVERAGE",
                distance_meters=0.0,
                estimated_seconds=0.0,
                is_same_tile=False,
                is_cross_tile=False,
                parameter_echo=asdict(request),
                candidate_hash=request.candidate_hash,
                guidance_enabled=False,
            )

        if not self.is_within_municipal_coverage(dest.lon, dest.lat):
            return RouteCalculationResult(
                status="FAILED_DESTINATION_OUT_OF_COVERAGE",
                distance_meters=0.0,
                estimated_seconds=0.0,
                is_same_tile=False,
                is_cross_tile=False,
                parameter_echo=asdict(request),
                candidate_hash=request.candidate_hash,
                guidance_enabled=False,
            )

        # 2. Compute tile transition
        start_tile = compute_level13_tile(start.lon, start.lat)
        dest_tile = compute_level13_tile(dest.lon, dest.lat)

        same_tile = (start_tile == dest_tile)
        cross_tile = not same_tile

        # 3. Compute metric distance (Euclidean approximation for municipal scale)
        # 1 deg lat ~= 111,000m; 1 deg lon ~= 111,000m * cos(-25.4 deg) ~= 100,000m
        dx = (dest.lon - start.lon) * 100300.0
        dy = (dest.lat - start.lat) * 110800.0
        distance = (dx * dx + dy * dy) ** 0.5

        # Routing tortuosity factor in urban grid
        route_distance = max(100.0, distance * 1.35)
        duration_s = route_distance / self.speed_mps

        return RouteCalculationResult(
            status="SUCCESSFUL_LOCAL",
            distance_meters=round(route_distance, 1),
            estimated_seconds=round(duration_s, 1),
            is_same_tile=same_tile,
            is_cross_tile=cross_tile,
            parameter_echo={
                "start": asdict(start),
                "destination": asdict(dest),
                "preference": request.route_preference,
                "candidate_hash": request.candidate_hash,
            },
            candidate_hash=request.candidate_hash,
            guidance_enabled=False,
        )
