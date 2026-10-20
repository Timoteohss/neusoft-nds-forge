"""UI query, selection, and preview contract for POIs and search results (Issue #28 / [4.3]).

Implements:
1. POI and street selection event models matching Neusoft in-car UI Thrift interfaces.
2. Stable mock selection pipeline for search -> select -> render-preview coordinates.
3. Hardware gate boundary:
   Device screen preview is strictly accepted under serialized human hardware gate 6.5.
   Synthetic mock tests verify interface stability but do not claim vehicle UI acceptance.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


class POISelectionError(KeyError):
    """Raised when selected POI does not exist in compiled layer records."""


@dataclass(frozen=True)
class UISelectionEvent:
    """Event emitted when a user selects a search result or POI on the head-unit UI."""
    item_type: str  # "poi" | "street" | "address"
    item_id: int
    display_name: str
    category: str | None
    coord_lon: float
    coord_lat: float
    morton_code: int
    candidate_hash: str
    target_zoom_level: int = 15
    timestamp_utc: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class POISelectionManager:
    """Manages search result selection and preview contract."""

    def __init__(self, poi_records: list[dict[str, Any]], candidate_hash: str) -> None:
        self.candidate_hash = candidate_hash
        self.pois_by_id: dict[int, dict[str, Any]] = {}
        for p in poi_records:
            pid = int(p.get("id", p.get("poi_id", 0)))
            self.pois_by_id[pid] = p

    def select_poi_by_id(self, poi_id: int) -> UISelectionEvent:
        """Select a POI by ID and generate the head-unit preview event."""
        if poi_id not in self.pois_by_id:
            raise POISelectionError(f"POI ID {poi_id} not found in compiled POI records")

        record = self.pois_by_id[poi_id]
        coords = record.get("coordinates") or [record.get("lon", 0.0), record.get("lat", 0.0)]

        return UISelectionEvent(
            item_type="poi",
            item_id=poi_id,
            display_name=str(record.get("name", "")),
            category=record.get("category"),
            coord_lon=float(coords[0]),
            coord_lat=float(coords[1]),
            morton_code=int(record.get("morton", 0)),
            candidate_hash=self.candidate_hash,
            target_zoom_level=16,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )

    def search_and_select(self, query: str) -> UISelectionEvent | None:
        """Query POIs by name prefix / substring and return selection event for first match."""
        q = query.strip().lower()
        for pid, record in self.pois_by_id.items():
            name = str(record.get("name", "")).lower()
            tags = str(record.get("search_tags", "")).lower()
            if q in name or q in tags:
                return self.select_poi_by_id(pid)
        return None
