"""Extract clipped OSM road ways from a local PBF into GeoJSON.

This adapter deliberately does not download data and does not compile Neusoft
layers. Its outputs include clipped road geometry, connected nodes, selected
POI nodes/ways, and retained raw OSM relations. Retaining restriction relations
does not compile turn rules, addresses, POI relation geometry, or a routing
graph.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..contracts import StageContext
from ..osm_model import (
    ClipLineage,
    OSMDataset,
    OSMNode,
    OSMProvenance,
    OSMRelation,
    OSMWay,
    RelationMember,
)

DEFAULT_HIGHWAY_VALUES = frozenset({
    "motorway", "motorway_link", "trunk", "trunk_link", "primary",
    "primary_link", "secondary", "secondary_link", "tertiary",
    "tertiary_link", "unclassified", "residential", "living_street",
    "service", "pedestrian", "track", "road", "busway", "bus_guideway",
})
POI_TAG_KEYS = frozenset({"amenity", "shop", "tourism", "leisure", "historic", "office", "craft"})
OSM_CLIP_CAPABILITIES_PRODUCED = (
    "road_ways", "osm_way_ids", "raw_way_tags", "clipped_road_geometry",
    "connected_nodes", "relations", "restriction_relations_raw",
    "canonical_osm_dataset", "poi_nodes", "poi_ways",
)
OSM_CLIP_CAPABILITIES_MISSING = (
    "complete_poi_relation_geometry", "addresses", "routing_graph",
    "traffic_location_ids", "turn_restrictions",
)


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
        from shapely.geometry import LineString, Point, box, mapping, shape
        from shapely.prepared import prep
    except ImportError as exc:
        missing = exc.name or "pyosmium/shapely"
        raise OSMExtractError(
            f"OSM clipping requires optional dependency {missing}; install pyosmium and shapely"
        ) from exc

    pbf = Path(context.source_path)
    if not pbf.is_file():
        raise OSMExtractError(f"PBF local não existe: {pbf}")
    boundary, bbox = _region_geometry(context.region, shape, box)
    prepared_boundary = prep(boundary)
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
    dataset_path = output_dir / "osm-dataset.json"
    report_path = output_dir / "clip-report.json"
    if roads_path.exists() or report_path.exists() or dataset_path.exists():
        raise OSMExtractError(f"A saída já existe; use um staging directory vazio: {output_dir}")

    pbf_hash = _sha256(pbf)
    polygon = context.region.geometry_geojson
    region_hash = (hashlib.sha256(json.dumps(polygon, sort_keys=True, separators=(",", ":"),
                                            ensure_ascii=False).encode("utf-8")).hexdigest()
                   if polygon is not None else None)

    provenance = OSMProvenance(
        source="pbf",
        path=str(pbf),
        sha256=pbf_hash,
        license=context.license,
        attribution=context.attribution,
    )
    clip_lineage = ClipLineage(
        region_id=context.region.id,
        boundary_sha256=region_hash,
        policy={"highway_values": sorted(accepted)},
    )
    dataset = OSMDataset(provenance=provenance, clip_lineage=clip_lineage)

    counts = {
        "highway_ways_seen": 0,
        "ways_missing_node_locations": 0,
        "ways_with_missing_nodes": 0,
        "ways_with_valid_locations": 0,
        "geometry_errors": 0,
        "ways_intersecting_region": 0,
        "clipped_parts": 0,
        "unnamed_ways": 0,
        "boundary_cut_ways": 0,
        "relations_seen": 0,
        "relations_collected": 0,
        "complete_relations": 0,
        "partial_relations": 0,
        "restrictions_collected": 0,
        "boundaries_collected": 0,
        "addresses_collected": 0,
    }
    first_feature = True

    class RoadHandler(osmium.SimpleHandler):
        def node(self, node: Any) -> None:
            tags = {tag.k: tag.v for tag in node.tags}
            if not (POI_TAG_KEYS.intersection(tags) or any(key.startswith("addr:") for key in tags)):
                return
            if node.location.valid() and prepared_boundary.covers(
                    Point(node.location.lon, node.location.lat)):
                dataset.add_node(OSMNode(id=int(node.id), lat=float(node.location.lat),
                                         lon=float(node.location.lon), tags=tags))

        def way(self, way: Any) -> None:
            nonlocal first_feature
            highway = way.tags.get("highway")
            tags = {tag.k: tag.v for tag in way.tags}
            is_road = highway in accepted
            if not is_road and not POI_TAG_KEYS.intersection(tags):
                return
            if is_road:
                counts["highway_ways_seen"] += 1

            node_refs: list[int] = []
            missing_node_refs: list[int] = []
            coords: list[tuple[float, float]] = []
            way_nodes_info: list[tuple[int, float, float]] = []

            for node in way.nodes:
                ref = int(node.ref)
                node_refs.append(ref)
                if not node.location.valid():
                    missing_node_refs.append(ref)
                else:
                    lon, lat = float(node.location.lon), float(node.location.lat)
                    coords.append((lon, lat))
                    way_nodes_info.append((ref, lat, lon))

            if missing_node_refs:
                counts["ways_with_missing_nodes"] += 1

            if len(coords) < 2:
                counts["ways_missing_node_locations"] += 1
                return

            counts["ways_with_valid_locations"] += 1
            if not is_road:
                # POI ways use the same vertex-mean representative location as
                # the model compiler. Select by that point rather than by the
                # footprint perimeter (which may enclose the entire region).
                lon = sum(point[0] for point in coords) / len(coords)
                lat = sum(point[1] for point in coords) / len(coords)
                if not missing_node_refs and prepared_boundary.covers(Point(lon, lat)):
                    dataset.add_way(OSMWay(id=int(way.id), nodes=node_refs, tags=tags,
                                           coordinates=coords))
                    for ref, lat, lon in way_nodes_info:
                        if ref not in dataset.nodes:
                            dataset.add_node(OSMNode(id=ref, lat=lat, lon=lon))
                return
            try:
                full_line = LineString(coords)
                clipped = boundary.intersection(full_line)
            except Exception:  # noqa: BLE001 -- Broad catch is intentional at this recovery/reporting boundary; preserve rollback/error aggregation behavior
                counts["geometry_errors"] += 1
                return
            parts = [part for part in _line_parts(clipped) if len(part.coords) >= 2]
            if not parts:
                return
            counts["ways_intersecting_region"] += 1
            if not tags.get("name"):
                counts["unnamed_ways"] += 1

            is_cut = (clipped.length < full_line.length - 1e-9) or bool(missing_node_refs)
            if is_cut:
                counts["boundary_cut_ways"] += 1

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

            dataset.add_way(OSMWay(
                id=int(way.id),
                nodes=node_refs,
                tags=tags,
                coordinates=coords,
                missing_nodes=missing_node_refs,
                is_boundary_cut=is_cut,
            ))

            for ref, lat, lon in way_nodes_info:
                if ref not in dataset.nodes:
                    is_outside = not prepared_boundary.intersects(Point(lon, lat))
                    dataset.add_node(OSMNode(
                        id=ref,
                        lat=lat,
                        lon=lon,
                        is_boundary_cut=is_outside,
                    ))

        def relation(self, rel: Any) -> None:
            counts["relations_seen"] += 1
            type_map = {"n": "node", "w": "way", "r": "relation"}
            members = [
                RelationMember(
                    type=type_map.get(m.type, str(m.type)),
                    ref=int(m.ref),
                    role=str(m.role),
                )
                for m in rel.members
            ]
            tags = {tag.k: tag.v for tag in rel.tags}
            rel_type = tags.get("type", "")

            has_relevant_type = rel_type in {
                "restriction", "boundary", "multipolygon",
                "associatedStreet", "address", "route",
            }
            touches_dataset = any(
                (m.type == "way" and m.ref in dataset.ways) or
                (m.type == "node" and m.ref in dataset.nodes)
                for m in members
            )
            if has_relevant_type or touches_dataset:
                dataset.add_relation(OSMRelation(
                    id=int(rel.id),
                    members=members,
                    tags=tags,
                ))
                counts["relations_collected"] += 1
                if rel_type == "restriction":
                    counts["restrictions_collected"] += 1
                elif rel_type == "boundary":
                    counts["boundaries_collected"] += 1
                elif rel_type in {"associatedStreet", "address"}:
                    counts["addresses_collected"] += 1

    try:
        with roads_path.open("x", encoding="utf-8") as output:
            output.write('{"type":"FeatureCollection","features":[')
            handler = RoadHandler()
            handler.apply_file(str(pbf), locations=True)
            output.write("]}")
            output.write("\n")
    except Exception:
        roads_path.unlink(missing_ok=True)
        dataset_path.unlink(missing_ok=True)
        raise

    try:
        audit = dataset.audit_references()
        dataset_path.write_text(dataset.to_json(), encoding="utf-8")
    except Exception:
        roads_path.unlink(missing_ok=True)
        dataset_path.unlink(missing_ok=True)
        raise

    counts["nodes_collected"] = len(dataset.nodes)
    counts["ways_collected"] = len(dataset.ways)
    counts["complete_relations"] = audit.complete_relations_count
    counts["partial_relations"] = audit.partial_relations_count

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
        "outputs": {
            "roads.geojson": {
                "bytes": roads_path.stat().st_size, "sha256": _sha256(roads_path),
                "media_type": "application/geo+json", "feature_count": counts["clipped_parts"]},
            "osm-dataset.json": {
                "bytes": dataset_path.stat().st_size, "sha256": _sha256(dataset_path),
                "media_type": "application/json", "node_count": len(dataset.nodes),
                "way_count": len(dataset.ways), "relation_count": len(dataset.relations)},
        },
        "counts": counts,
        "capabilities_produced": list(OSM_CLIP_CAPABILITIES_PRODUCED),
        "capabilities_missing": list(OSM_CLIP_CAPABILITIES_MISSING),
        "limitations": [
            "This adapter exports clipped highway ways, connected nodes, and relations; retained tags are not compiled semantics.",
            "Raw OSM restriction relations are retained for downstream use; turn restrictions are not compiled by this adapter.",
            "POI nodes/ways are retained for a separately selected POI compiler; complete multipolygon POI geometry is not guaranteed.",
            "Address objects, routing graph attributes and traffic matching remain separate compiler stages.",
            "Coordinates are consumed as longitude/latitude; no reprojection is performed.",
        ],
    }
    try:
        with report_path.open("x", encoding="utf-8") as output:
            json.dump(report, output, sort_keys=True, indent=2, ensure_ascii=False)
            output.write("\n")
    except Exception:
        roads_path.unlink(missing_ok=True)
        dataset_path.unlink(missing_ok=True)
        raise
    return report
