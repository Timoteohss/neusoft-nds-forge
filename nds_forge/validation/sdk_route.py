"""SDK request/result contracts; calculation remains unsupported without a real adapter.

This module does not load a map or call Neusoft. It must never manufacture a
successful route or be counted as evidence for SDK, UI, or vehicle acceptance.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..layers.bmd_codec import tile_for


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
        default_factory=lambda: datetime.now(UTC).isoformat()
    )


def compute_level13_tile(lon: float, lat: float) -> int:
    """Compatibility wrapper for the canonical level-13 BMD tile encoder."""
    return tile_for(lon, lat, 13)


class SDKRouteHarness:
    """Pending SDK integration; bbox and speed are compatibility inputs only."""

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
        """Fail closed until a real SDK adapter with candidate binding is implemented."""
        return RouteCalculationResult(
            status="UNSUPPORTED_SDK_ADAPTER",
            distance_meters=0.0,
            estimated_seconds=0.0,
            is_same_tile=False,
            is_cross_tile=False,
            parameter_echo=asdict(request),
            candidate_hash=request.candidate_hash,
            guidance_enabled=False,
        )
