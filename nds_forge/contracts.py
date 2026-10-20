"""Small data contracts shared by pipeline steps and optional adapters."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RegionSpec:
    id: str
    name: str
    bbox: tuple[float, float, float, float] | None
    geometry_geojson: dict[str, Any] | None
    resolved: bool


@dataclass(frozen=True)
class StageContext:
    source_path: Path
    region: RegionSpec
    output_dir: Path
    license: str
    attribution: str
