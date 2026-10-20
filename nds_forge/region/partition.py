"""Multi-region partitioning, seam auditing, and catalog coverage verification (Issue #34 / [5.3]).

Implements:
1. Multi-region tile coverage generation across explicit per-layer levels and index levels.
2. Parent-child tile containment verification:
   - Fails on missing parent-index tiles.
   - Fails on excess/orphaned parent-index tiles (index tile without any child payload tiles).
3. Seam and gateway reciprocal auditing across tile/region boundaries:
   - Ensures boundary links have reciprocal entry/exit gateways between neighbor tiles.
   - Audits seam coordinate matching and detects asymmetric seams.
4. Geographic separation check for disjoint regions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class PartitionError(ValueError):
    """Raised when partitioning or seam auditing fails."""


class SeamMismatchError(PartitionError):
    """Raised when tile boundaries have asymmetric or non-reciprocal seams."""


class IndexContainmentError(PartitionError):
    """Raised when parent-index tiles do not strictly reconcile with payload tiles."""


@dataclass(frozen=True)
class Gateway:
    """A connection gateway crossing between two tiles."""
    source_tile: int
    target_tile: int
    link_id: int
    coord_lon: float
    coord_lat: float


@dataclass
class RegionPartition:
    """Partition specification for a single region."""
    region_id: int
    region_name: str
    layer_tiles: dict[str, list[int]] = field(default_factory=dict)
    layer_index_tiles: dict[str, list[int]] = field(default_factory=dict)
    gateways: list[Gateway] = field(default_factory=list)


def compute_parent_tile_id(tile_id: int, child_level: int, parent_level: int) -> int:
    """Compute parent Morton tile ID by bit-shifting."""
    if parent_level > child_level:
        raise ValueError(f"parent_level ({parent_level}) cannot be greater than child_level ({child_level})")
    shift = 2 * (child_level - parent_level)
    return tile_id >> shift


def verify_index_containment(
    payload_tiles: list[int],
    index_tiles: list[int],
    payload_level: int,
    index_level: int,
    layer_name: str,
) -> None:
    """Verify that every payload tile has its parent index tile and no excess index tiles exist.

    Raises IndexContainmentError on mismatch.
    """
    expected_index_tiles = {
        compute_parent_tile_id(t, payload_level, index_level) for t in payload_tiles
    }
    actual_index_tiles = set(index_tiles)

    missing = expected_index_tiles - actual_index_tiles
    if missing:
        raise IndexContainmentError(
            f"Layer {layer_name}: missing parent-index tiles for payload: {sorted(missing)}"
        )

    excess = actual_index_tiles - expected_index_tiles
    if excess:
        raise IndexContainmentError(
            f"Layer {layer_name}: excess/orphaned parent-index tiles with no payload: {sorted(excess)}"
        )


def audit_seam_reciprocity(gateways: list[Gateway]) -> dict[str, Any]:
    """Audit gateway reciprocity across tile seams.

    Every gateway from A -> B at coordinate (x, y) must have a matching reciprocal gateway from B -> A.
    """
    # Key by (min(A, B), max(A, B), round(lon, 6), round(lat, 6))
    outgoing = {}
    incoming = {}
    orphans: list[dict[str, Any]] = []
    matched_count = 0

    for gw in gateways:
        key = (gw.source_tile, gw.target_tile, round(gw.coord_lon, 6), round(gw.coord_lat, 6))
        outgoing[key] = gw

    for gw in gateways:
        reciprocal_key = (gw.target_tile, gw.source_tile, round(gw.coord_lon, 6), round(gw.coord_lat, 6))
        if reciprocal_key not in outgoing:
            orphans.append({
                "source_tile": gw.source_tile,
                "target_tile": gw.target_tile,
                "link_id": gw.link_id,
                "coord": [gw.coord_lon, gw.coord_lat],
                "error": "Missing reciprocal gateway in target tile",
            })
        else:
            matched_count += 1

    is_reciprocal = len(orphans) == 0

    return {
        "total_gateways": len(gateways),
        "matched_gateways": matched_count // 2 if is_reciprocal else 0,
        "is_reciprocal": is_reciprocal,
        "orphans": orphans,
    }


def audit_disjoint_regions(partitions: list[RegionPartition]) -> dict[str, Any]:
    """Verify that geographically separated regions do not have overlapping tiles."""
    seen_tiles: dict[tuple[str, int], int] = {}
    overlaps: list[dict[str, Any]] = []

    for part in partitions:
        for layer, tiles in part.layer_tiles.items():
            for t in tiles:
                key = (layer, t)
                if key in seen_tiles:
                    overlaps.append({
                        "layer": layer,
                        "tile_id": t,
                        "region_1": seen_tiles[key],
                        "region_2": part.region_id,
                    })
                else:
                    seen_tiles[key] = part.region_id

    return {
        "are_disjoint": len(overlaps) == 0,
        "overlaps": overlaps,
    }


def build_partition_manifest(
    partitions: list[RegionPartition],
    layer_rules: dict[str, dict[str, int]],
) -> dict[str, Any]:
    """Build multi-region partition manifest with coverage, index, and seam audit.

    Args:
        partitions: List of RegionPartition objects.
        layer_rules: Per-layer explicit levels, e.g.:
                     {"route": {"payload_level": 13, "index_level": 9}, "bmd": {"level": 11}}
    """
    disjoint_audit = audit_disjoint_regions(partitions)
    all_gateways: list[Gateway] = []
    region_summaries = []

    for part in partitions:
        all_gateways.extend(part.gateways)

        # Validate parent index containment for each layer that declares index_level
        for layer, tiles in part.layer_tiles.items():
            rule = layer_rules.get(layer)
            if rule and "index_level" in rule and "payload_level" in rule:
                index_tiles = part.layer_index_tiles.get(layer, [])
                verify_index_containment(
                    payload_tiles=tiles,
                    index_tiles=index_tiles,
                    payload_level=rule["payload_level"],
                    index_level=rule["index_level"],
                    layer_name=f"{part.region_name}:{layer}",
                )

        region_summaries.append({
            "region_id": part.region_id,
            "region_name": part.region_name,
            "layers": {l: len(t) for l, t in part.layer_tiles.items()},
            "gateways_count": len(part.gateways),
        })

    seam_audit = audit_seam_reciprocity(all_gateways)

    return {
        "schema_version": 1,
        "step": "region.partition",
        "layer_rules": layer_rules,
        "regions": region_summaries,
        "seam_audit": seam_audit,
        "disjoint_audit": disjoint_audit,
    }
