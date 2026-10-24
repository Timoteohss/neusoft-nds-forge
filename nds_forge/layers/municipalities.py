"""Build municipality polygons from OSM administrative areas.

The Parana state profile needs a municipality for each street so the Neusoft
NAME/FTS hierarchy can expose cities and city-scoped street search. This module
reads the source PBF directly (outside ``osm.clip``, which intentionally keeps
only road/POI entities) and emits the polygons and OSM identity fields used by
``names.compile``.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..contracts import RegionSpec


class MunicipalityError(RuntimeError):
    """The source PBF or a municipality area could not be assembled safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ring_coords(ring: Any) -> list[tuple[float, float]]:
    coords = [(float(node.lon), float(node.lat)) for node in ring if node.location.valid()]
    if len(coords) >= 4 and coords[0] != coords[-1]:
        coords.append(coords[0])
    return coords


def _build_geometry(area: Any, Polygon: Any, MultiPolygon: Any) -> Any | None:
    polygons = []
    for outer in area.outer_rings():
        outer_coords = _ring_coords(outer)
        if len(outer_coords) < 4:
            continue
        holes = []
        for inner in area.inner_rings(outer):
            inner_coords = _ring_coords(inner)
            if len(inner_coords) >= 4:
                holes.append(inner_coords)
        polygons.append(Polygon(outer_coords, holes))
    if not polygons:
        return None
    return polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)


def compile_municipalities(source_path: Path, region: RegionSpec, output_dir: Path,
                           *, license: str = "unknown", attribution: str = "",
                           admin_level: str = "8", ibge_prefix: str | None = None) -> dict[str, Any]:
    """Write ``municipalities.json`` for administrative areas in the region.

    Only OSM relations with ``boundary=administrative``, the selected
    ``admin_level`` and an IBGE geocode are retained. IBGE is used as the stable
    identity, not the display name, because names repeat across Brazil.
    """
    try:
        import osmium
        from shapely.geometry import MultiPolygon, Polygon, box, mapping
    except ImportError as exc:
        raise MunicipalityError(
            "Municipality generation requires optional dependencies pyosmium and shapely"
        ) from exc

    pbf = Path(source_path)
    if not pbf.is_file():
        raise MunicipalityError(f"PBF local não existe: {pbf}")
    if not region.resolved:
        raise MunicipalityError("A região precisa estar resolvida para gerar municípios")

    region_box = None
    if region.bbox is not None:
        if len(region.bbox) != 4:
            raise MunicipalityError("bbox da região inválida")
        west, south, east, north = (float(value) for value in region.bbox)
        region_box = box(west, south, east, north)

    counts = {
        "areas_seen": 0,
        "admin_areas_seen": 0,
        "assembled": 0,
        "missing_geocode": 0,
        "invalid_geometry": 0,
        "outside_region": 0,
    }
    records: dict[str, dict[str, Any]] = {}

    class MunicipalityHandler(osmium.SimpleHandler):
        def area(self, area: Any) -> None:
            counts["areas_seen"] += 1
            tags = {tag.k: tag.v for tag in area.tags}
            if tags.get("boundary") != "administrative" or tags.get("admin_level") != admin_level:
                return
            counts["admin_areas_seen"] += 1
            geocode = tags.get("IBGE:GEOCODIGO", "").strip()
            name = tags.get("name", "").strip()
            if not geocode or not name:
                counts["missing_geocode"] += 1
                return
            if ibge_prefix is not None and not geocode.startswith(ibge_prefix):
                counts["outside_region"] += 1
                return
            if geocode in records:
                return
            try:
                geometry = _build_geometry(area, Polygon, MultiPolygon)
            except Exception:  # noqa: BLE001 -- Shapely may raise several geometry errors; all are reported as invalid areas
                counts["invalid_geometry"] += 1
                return
            if geometry is None or geometry.is_empty or not geometry.is_valid:
                counts["invalid_geometry"] += 1
                return
            if region_box is not None and not geometry.intersects(region_box):
                counts["outside_region"] += 1
                return
            point = geometry.representative_point()
            record = {
                "id": int(area.orig_id()),
                "ibge_code": geocode,
                "name": name,
                "admin_level": admin_level,
                "postal_code": tags.get("postal_code", "").strip(),
                "longitude": float(point.x),
                "latitude": float(point.y),
                "bbox": [float(value) for value in geometry.bounds],
                "geometry": mapping(geometry),
            }
            records[geocode] = record
            counts["assembled"] += 1

    try:
        MunicipalityHandler().apply_file(str(pbf), locations=True)
    except Exception as exc:
        raise MunicipalityError(f"Falha ao montar municípios do PBF: {exc}") from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "step": "names.municipalities",
        "maturity": "experimental",
        "source": {
            "path": str(pbf),
            "sha256": _sha256(pbf),
            "license": license,
            "attribution": attribution,
        },
        "region": {"id": region.id, "bbox": list(region.bbox) if region.bbox else None},
        "admin_level": admin_level,
        "counts": counts,
        "records": [records[key] for key in sorted(records)],
        "capabilities_produced": ["municipality_boundaries", "ibge_municipality_codes"],
        "capabilities_missing": ["municipality_ui_acceptance", "postcode_street_interpolation"],
        "limitations": [
            "Municipality polygons are derived from OSM administrative relations.",
            "Street assignment uses the street centroid, not the full road geometry.",
            "A municipality boundary does not prove native/UI search acceptance.",
        ],
    }
    output_path = output_dir / "municipalities.json"
    output_path.write_text(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "counts": counts,
        "outputs": {
            "municipalities.json": {
                "bytes": output_path.stat().st_size,
                "sha256": _sha256(output_path),
            }
        },
        "maturity": "experimental",
    }