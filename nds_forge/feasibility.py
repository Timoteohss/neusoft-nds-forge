"""Tile and profile feasibility measurement for NDS Forge (Issue #33 / [5.2]).

Measures routing and geometry tile complexity before compilation:
1. Counts intersections, links, max node degree, external tile ordinals, and AUX row offsets.
2. Detects and fails on buffer/format overflows before invoking heavy compilers.
3. Documents candidate profile evidence:
   - Level 13 has verified Curitiba SDK route evidence.
   - Level 14 lacks SDK evidence.
   - Prohibits unfounded national density/resource extrapolation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class TileOverflowError(ValueError):
    """Raised when tile metrics exceed NDS Classic 2.4.4 architectural limits."""


@dataclass(frozen=True)
class TileLimits:
    """NDS.Classic 2.4.4 architectural capacity limits per tile."""
    max_links: int = 32767
    max_intersections: int = 32767
    max_degree: int = 16
    max_external_tiles: int = 255
    max_aux_bytes: int = 16777216  # 16 MiB


@dataclass
class TileMetrics:
    """Observed complexity metrics for a single tile."""
    tile_id: int
    level: int
    intersection_count: int
    link_count: int
    max_degree: int
    external_tile_count: int
    aux_bytes: int = 0


@dataclass
class TileFeasibilityReport:
    """Evaluation result for one or more tiles."""
    tile_id: int
    level: int
    is_feasible: bool
    metrics: dict[str, Any]
    overflows: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def evaluate_tile_feasibility(
    metrics: TileMetrics,
    limits: TileLimits | None = None,
    raise_on_overflow: bool = False,
) -> TileFeasibilityReport:
    """Evaluate if a tile's complexity fits within architectural limits.

    Raises TileOverflowError if raise_on_overflow is True and an overflow is detected.
    """
    lim = limits or TileLimits()
    overflows: list[str] = []
    warnings: list[str] = []

    if metrics.link_count > lim.max_links:
        overflows.append(
            f"Link count {metrics.link_count} exceeds limit {lim.max_links}"
        )
    elif metrics.link_count > lim.max_links * 0.8:
        warnings.append(f"Link count {metrics.link_count} reaches 80% of limit {lim.max_links}")

    if metrics.intersection_count > lim.max_intersections:
        overflows.append(
            f"Intersection count {metrics.intersection_count} exceeds limit {lim.max_intersections}"
        )
    elif metrics.intersection_count > lim.max_intersections * 0.8:
        warnings.append(f"Intersection count {metrics.intersection_count} reaches 80% of limit {lim.max_intersections}")

    if metrics.max_degree > lim.max_degree:
        overflows.append(
            f"Node degree {metrics.max_degree} exceeds limit {lim.max_degree}"
        )

    if metrics.external_tile_count > lim.max_external_tiles:
        overflows.append(
            f"External tile count {metrics.external_tile_count} exceeds 8-bit ordinal limit {lim.max_external_tiles}"
        )

    if metrics.aux_bytes > lim.max_aux_bytes:
        overflows.append(
            f"AUX geometry bytes {metrics.aux_bytes} exceeds limit {lim.max_aux_bytes}"
        )

    is_feasible = len(overflows) == 0

    if not is_feasible and raise_on_overflow:
        raise TileOverflowError("; ".join(overflows))

    return TileFeasibilityReport(
        tile_id=metrics.tile_id,
        level=metrics.level,
        is_feasible=is_feasible,
        metrics=asdict(metrics),
        overflows=overflows,
        warnings=warnings,
    )


def build_candidate_profile_report(
    regional_tiles: list[TileMetrics],
    profile_level: int,
    profile_name: str,
) -> dict[str, Any]:
    """Generate regional feasibility report comparing candidate profiles.

    Enforces data boundary rule:
    - Level 13 has Curitiba SDK evidence.
    - Level 14 does NOT have SDK evidence.
    - National density cannot be inferred from a single municipality.
    """
    reports = [evaluate_tile_feasibility(m) for m in regional_tiles]
    all_feasible = all(r.is_feasible for r in reports)

    sdk_evidence = (profile_level == 13 and "curitiba" in profile_name.lower())

    evidence_note = (
        "Level 13 has verified Curitiba SDK route calculation evidence."
        if profile_level == 13
        else "Level 14 lacks validated SDK route calculation evidence; unproven for production."
    )

    return {
        "profile_name": profile_name,
        "profile_level": profile_level,
        "sdk_evidence_verified": sdk_evidence,
        "evidence_note": evidence_note,
        "national_inference_allowed": False,
        "national_inference_note": (
            "National density/resource requirements cannot be inferred from regional samples. "
            "Each Brazilian metropolitan region must be partitioned and measured independently."
        ),
        "total_tiles": len(regional_tiles),
        "all_feasible": all_feasible,
        "tile_reports": [asdict(r) for r in reports],
    }
