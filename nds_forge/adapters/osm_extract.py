"""Extract clipped OSM road ways from a local PBF into GeoJSON.

This adapter deliberately does not download data and does not compile Neusoft
layers. Its output is road-way geometry with source IDs and raw way tags; it
does not claim to extract POIs, addresses, nodes, restrictions, or a routing
graph.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..contracts import StageContext


DEFAULT_HIGHWAY_VALUES = frozenset({
    "motorway", "motorway_link", "trunk", "trunk_link", "primary",
    "primary_link", "secondary", "secondary_link", "tertiary",
    "tertiary_link", "unclassified", "residential", "living_street",
    "service", "pedestrian", "track", "road", "busway", "bus_guideway",
})


class OSMExtractError(RuntimeError):
    """The local PBF or selected region could not be extracted safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _region_geometry(region: Any, shape: Any, box: Any) -> tuple[Any, list[float]]:
    if not region.resolved:
        raise OSMExtractError(
            "A região ainda não está resolvida; forneça bbox ou polígono GeoJSON local"
        )
    if region.geometry_geojson is not None:
        try:
            geometry = shape(region.geometry_geojson)
        except Exception as exc:
            raise OSMExtractError(f"GeoJSON de região inválido: {exc}") from exc
    elif region.bbox is not None:
        if len(region.bbox) != 4:
            raise OSMExtractError("bbox deve ser (west, south, east, north)")
        west, south, east, north = region.bbox
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise OSMExtractError("bbox fora dos limites geográficos ou invertido")
        geometry = box(west, south, east, north)
    else:
        raise OSMExtractError("A região resolvida não contém bbox nem polígono")

    if geometry.is_empty or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise OSMExtractError("O limite precisa ser Polygon/MultiPolygon não vazio")
    if not geometry.is_valid:
        raise OSMExtractError("O limite contém geometria inválida; corrija a entrada GeoJSON")
    min_x, min_y, max_x, max_y = geometry.bounds
    return geometry, [min_x, min_y, max_x, max_y]


def _line_parts(geometry: Any) -> Iterable[Any]:
    if geometry.is_empty:
        return
    if geometry.geom_type == "LineString":
        yield geometry
    elif geometry.geom_type in {"MultiLineString", "GeometryCollection"}:
        for child in geometry.geoms:
            yield from _line_parts(child)


def clip_pbf(context: StageContext,
             policy: Iterable[str] | None = None) -> dict[str, Any]:
    """Clip highway ways from ``context.source_path`` to its resolved region.

    ``policy`` is an optional iterable of accepted OSM ``highway`` values;
    the default is a broad road-centerline set used by the legacy Curitiba
    extractor. Provide an explicit policy for a product-specific selection.
    The function writes ``roads.geojson`` and ``clip-report.json`` beneath the
    supplied output directory and returns the same report object.

    Requires optional dependencies ``pyosmium`` and ``shapely``. No network
    access is performed.
    """
    try:
        import osmium
        from shapely.geometry import LineString, box, mapping, shape
    except ImportError as exc:
        missing = exc.name or "pyosmium/shapely"
        raise OSMExtractError(
            f"OSM clipping requires optional dependency {missing}; install pyosmium and shapely"
        ) from exc

    pbf = Path(context.source_path)
    if not pbf.is_file():
        raise OSMExtractError(f"PBF local não existe: {pbf}")
    boundary, bbox = _region_geometry(context.region, shape, box)
    if isinstance(policy, (str, bytes)):
        raise OSMExtractError("policy deve ser uma coleção de valores highway, não uma string")
    try:
        accepted = frozenset(DEFAULT_HIGHWAY_VALUES if policy is None else policy)
    except TypeError as exc:
        raise OSMExtractError("policy deve ser uma coleção iterável de valores highway") from exc
    if not accepted or any(not isinstance(value, str) or not value for value in accepted):
        raise OSMExtractError("policy deve conter ao menos um valor highway não vazio")

    output_dir = Path(context.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    roads_path = output_dir / "roads.geojson"
    report_path = output_dir / "clip-report.json"
    if roads_path.exists() or report_path.exists():
        raise OSMExtractError(f"A saída já existe; use um staging directory vazio: {output_dir}")

    counts = {
        "highway_ways_seen": 0,
        "ways_missing_node_locations": 0,
        "ways_with_valid_locations": 0,
        "geometry_errors": 0,
        "ways_intersecting_region": 0,
        "clipped_parts": 0,
        "unnamed_ways": 0,
    }
    first_feature = True

    class RoadHandler(osmium.SimpleHandler):
        def way(self, way: Any) -> None:
            nonlocal first_feature
            highway = way.tags.get("highway")
            if highway not in accepted:
                return
            counts["highway_ways_seen"] += 1
            coords = []
            try:
                for node in way.nodes:
                    if not node.location.valid():
                        raise ValueError("invalid node location")
                    coords.append((float(node.location.lon), float(node.location.lat)))
            except (ValueError, RuntimeError):
                counts["ways_missing_node_locations"] += 1
                return
            if len(coords) < 2:
                counts["ways_missing_node_locations"] += 1
                return

            counts["ways_with_valid_locations"] += 1
            try:
                clipped = boundary.intersection(LineString(coords))
            except Exception:
                counts["geometry_errors"] += 1
                return
            parts = [part for part in _line_parts(clipped) if len(part.coords) >= 2]
            if not parts:
                return
            counts["ways_intersecting_region"] += 1
            tags = {tag.k: tag.v for tag in way.tags}
            if not tags.get("name"):
                counts["unnamed_ways"] += 1
            for part_no, part in enumerate(parts):
                feature = {
                    "type": "Feature",
                    "id": f"way/{int(way.id)}/{part_no}",
                    "properties": {**tags, "osm_id": int(way.id), "raw_tags": tags},
                    "geometry": mapping(part),
                }
                if not first_feature:
                    output.write(",")
                json.dump(feature, output, ensure_ascii=False, separators=(",", ":"))
                first_feature = False
                counts["clipped_parts"] += 1

    try:
        with roads_path.open("x", encoding="utf-8") as output:
            output.write('{"type":"FeatureCollection","features":[')
            handler = RoadHandler()
            handler.apply_file(str(pbf), locations=True)
            output.write("]}")
            output.write("\n")
    except Exception:
        roads_path.unlink(missing_ok=True)
        raise

    pbf_hash = _sha256(pbf)
    polygon = context.region.geometry_geojson
    region_hash = (hashlib.sha256(json.dumps(polygon, sort_keys=True, separators=(",", ":"),
                                            ensure_ascii=False).encode("utf-8")).hexdigest()
                   if polygon is not None else None)
    report = {
        "schema_version": 1,
        "step": "osm.clip",
        "maturity": "experimental",
        "region": {"id": context.region.id, "name": context.region.name,
                   "bbox_lon_lat": bbox, "geometry_sha256": region_hash,
                   "resolved": context.region.resolved},
        "source": {"kind": "pbf", "path": str(pbf), "bytes": pbf.stat().st_size,
                   "sha256": pbf_hash, "license": context.license,
                   "attribution": context.attribution},
        "policy": {"highway_values": sorted(accepted)},
        "outputs": {"roads.geojson": {
            "bytes": roads_path.stat().st_size, "sha256": _sha256(roads_path),
            "media_type": "application/geo+json", "feature_count": counts["clipped_parts"]}},
        "counts": counts,
        "capabilities_produced": ["road_ways", "osm_way_ids", "raw_way_tags", "clipped_road_geometry"],
        "capabilities_missing": ["osm_nodes_as_entities", "relations", "pois", "addresses",
                                 "turn_restrictions", "routing_graph", "traffic_location_ids"],
        "limitations": [
            "This adapter exports clipped highway ways only; retained tags are not compiled semantics.",
            "POIs, address objects, turn restrictions, relations, routing attributes and traffic matching are absent.",
            "Coordinates are consumed as longitude/latitude; no reprojection is performed.",
        ],
    }
    try:
        with report_path.open("x", encoding="utf-8") as output:
            json.dump(report, output, sort_keys=True, indent=2, ensure_ascii=False)
            output.write("\n")
    except Exception:
        roads_path.unlink(missing_ok=True)
        raise
    return report
