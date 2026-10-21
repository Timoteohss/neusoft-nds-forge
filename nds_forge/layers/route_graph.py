"""Build a version-pinned compact level-13 OSM route graph.

Ported from the v9 legacy builder and limited to the explicit Curitiba v9
profile policy. Unlike a link-per-OSM-segment graph, it preserves junctions and
route shape between them as polylines. It targets the highest route level in
the stock Neusoft catalog and keeps auxiliary geometry offsets small.

This experimental output preserves graph topology but does not compile
restrictions, conditional access, maneuver guidance, or legal speed semantics.
"""

import hashlib
import itertools
import json
import math
from collections import Counter, defaultdict

from .bmd_codec import nds_coordinate, tile_origin
from .route_attributes import bearing_degrees, build_link_attributes, distance_meters
from .route_codec import encode_route_tile, parse_intersections, parse_main_header
from .route_support import split_segment_by_tile


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def eligible(way, policy):
    tags = {t.k: t.v for t in way.tags}
    if tags.get("highway") not in policy["driveable_highways"]:
        return False, tags
    denied = set(policy["denied_access_values"])
    for key in ("motorcar", "motor_vehicle", "vehicle", "access"):
        if key in tags:
            return tags[key] not in denied, tags
    return True, tags


def direction_for(tags):
    one_way = tags.get("oneway", "").lower()
    if one_way in {"yes", "true", "1"} or (
        not one_way and tags.get("junction") == "roundabout"
    ):
        return "forward"
    if one_way == "-1":
        return "reverse"
    if one_way in {"no", "false", "0", ""}:
        return "both"
    return "unknown"


def quantized_node(point, tile, level, shift):
    origin = tile_origin(tile, level)
    coord = (nds_coordinate(point[0]), nds_coordinate(point[1]))
    return tuple(
        origin[i] + round((coord[i] - origin[i]) / (1 << shift)) * (1 << shift)
        for i in range(2)
    )


def append_unique(points, point):
    if not points or points[-1] != point:
        points.append(point)


def split_polyline_by_tile(points, level, shift):
    """Return contiguous tile-local quantized polylines for a lon/lat path."""
    groups = []
    for a, b in itertools.pairwise(points):
        for tile, pair in split_segment_by_tile(a, b, level, shift):
            qa, qb = tuple(pair[0]), tuple(pair[1])
            expected_b = quantized_node(b, tile, level, shift)
            if groups and groups[-1]["tile"] == tile and groups[-1]["points"][-1] == qa:
                append_unique(groups[-1]["points"], qb)
            else:
                groups.append({"tile": tile, "points": [qa, qb]})
            # Keep an OSM shape vertex when the current part reaches it.
            if qb == expected_b:
                append_unique(groups[-1]["points"], expected_b)
    return [
        g for g in groups if len(g["points"]) >= 2 and g["points"][0] != g["points"][-1]
    ]


def make_attrs(points, tags, direction, policy):
    attrs = build_link_attributes(points[0], points[-1], tags, direction, policy)
    meters = sum(
        distance_meters(
            (a[0] / (1 << 31) * 180, a[1] / (1 << 31) * 180),
            (b[0] / (1 << 31) * 180, b[1] / (1 << 31) * 180),
        )
        for a, b in itertools.pairwise(points)
    )
    attrs["length_raw"] = max(1, round(meters * 100))
    return attrs


def merge_degree2_chains(tiles, node_tile_copies):
    """Collapse same-class bidirectional degree-2 joints within each tile."""
    totals = Counter()
    for tile, graph in tiles.items():
        edges = list(graph["links"])
        adjacency = defaultdict(set)
        for i, e in enumerate(edges):
            adjacency[e["a"]].add(i)
            adjacency[e["b"]].add(i)
        queue = list(adjacency)
        cursor = 0
        while cursor < len(queue):
            node = queue[cursor]
            cursor += 1
            if len(node_tile_copies.get(node, {})) > 1:
                continue
            active = [i for i in adjacency[node] if edges[i] is not None]
            if len(active) != 2:
                continue
            i, j = active
            e1, e2 = edges[i], edges[j]
            a1 = (e1["attrs"]["road_raw"] >> 6) & 3
            a2 = (e2["attrs"]["road_raw"] >> 6) & 3
            class1 = e1["attrs"]["road_raw"] >> 12
            class2 = e2["attrs"]["road_raw"] >> 12
            if a1 != 3 or a2 != 3 or class1 != class2:
                continue
            outer1 = e1["b"] if e1["a"] == node else e1["a"]
            outer2 = e2["b"] if e2["a"] == node else e2["a"]
            if outer1 == outer2:
                continue

            def oriented(edge, start, end):
                if edge["a"] == start and edge["b"] == end:
                    return edge["points"]
                if edge["b"] == start and edge["a"] == end:
                    return list(reversed(edge["points"]))
                raise ValueError("Broken degree-2 adjacency")

            p1 = oriented(e1, outer1, node)
            p2 = oriented(e2, node, outer2)
            points = p1 + p2[1:]
            attrs = dict(e1["attrs"])
            attrs["length_raw"] = e1["attrs"]["length_raw"] + e2["attrs"]["length_raw"]
            attrs["scalar8"] = min(e1["attrs"]["scalar8"], e2["attrs"]["scalar8"])
            lonlat = [
                (x / (1 << 31) * 180, y / (1 << 31) * 180)
                for x, y in (points[0], points[-1])
            ]
            attrs["scalar6_a"] = (
                math.floor(bearing_degrees(*lonlat) / 5.625 + 0.5) & 63
            )
            attrs["scalar6_b"] = (
                math.floor(bearing_degrees(lonlat[1], lonlat[0]) / 5.625 + 0.5)
                & 63
            )
            merged = {
                "a": outer1,
                "b": outer2,
                "points": points,
                "attrs": attrs,
                "way_id": e1["way_id"],
                "run": e1["run"],
            }
            edges[i] = None
            edges[j] = None
            for endpoint in (e1["a"], e1["b"], e2["a"], e2["b"]):
                adjacency[endpoint].discard(i)
                adjacency[endpoint].discard(j)
            new_index = len(edges)
            edges.append(merged)
            adjacency[outer1].add(new_index)
            adjacency[outer2].add(new_index)
            if len(adjacency[outer1]) == 2:
                queue.append(outer1)
            if len(adjacency[outer2]) == 2:
                queue.append(outer2)
            totals["merged_links"] += 1
        graph["links"] = [e for e in edges if e is not None]
        graph["nodes"] = {
            key: xy
            for key, xy in graph["nodes"].items()
            if adjacency[key] and any(edges[i] is not None for i in adjacency[key])
        }
    new_copies = defaultdict(lambda: defaultdict(list))
    for tile, graph in tiles.items():
        for idx, e in enumerate(graph["links"]):
            new_copies[e["a"]][tile].append((idx, 1))
            new_copies[e["b"]][tile].append((idx, 0))
    return new_copies, totals


def build(
    pbf,
    boundary_geometry,
    output,
    *,
    level,
    shift,
    simplify_tolerance_degrees,
    policy,
    region_id,
    source_metadata=None,
):
    try:
        import osmium
    except ImportError as exc:
        raise RuntimeError(
            "ROUTE compiler requires optional dependency pyosmium"
        ) from exc
    try:
        from shapely.geometry import LineString, Point, shape
        from shapely.prepared import prep
    except ImportError as exc:
        raise RuntimeError(
            "ROUTE compiler requires optional dependency shapely"
        ) from exc
    output.mkdir(parents=True, exist_ok=True)
    if not isinstance(boundary_geometry, dict):
        raise ValueError("A resolved Polygon/MultiPolygon geometry is required")  # noqa: TRY004 -- ValueError is the established validation exception; changing its class changes caller-visible behavior
    if policy.get("profile_id") != "neusoft-curitiba-v9":
        raise ValueError(
            "This graph builder is pinned to the reviewed neusoft-curitiba-v9 policy"
        )
    if (level, shift) != (13, 4):
        raise ValueError("The reviewed v9 route graph requires level 13 / shift 4")
    boundary = prep(shape(boundary_geometry))

    # Junction candidates are nodes shared by two or more routable OSM ways.
    usage = Counter()
    counts = Counter()
    bits = osmium.osm.osm_entity_bits.WAY
    for way in osmium.FileProcessor(str(pbf), entities=bits):
        ok, _ = eligible(way, policy)
        if not ok:
            continue
        counts["eligible_ways"] += 1
        for node in way.nodes:
            usage[int(node.ref)] += 1

    tiles = defaultdict(lambda: {"nodes": {}, "links": []})
    node_tile_copies = defaultdict(lambda: defaultdict(list))
    entities = osmium.osm.osm_entity_bits.NODE | osmium.osm.osm_entity_bits.WAY
    for way in osmium.FileProcessor(str(pbf), entities=entities).with_locations():
        if not way.is_way():
            continue
        ok, tags = eligible(way, policy)
        if not ok:
            continue
        refs = list(way.nodes)
        if len(refs) < 2:
            continue
        important = {0, len(refs) - 1}
        important.update(i for i, n in enumerate(refs) if usage[int(n.ref)] > 1)
        boundaries = sorted(important)
        direction = direction_for(tags)
        for run_no, (start_i, end_i) in enumerate(itertools.pairwise(boundaries)):
            run_refs = refs[start_i : end_i + 1]
            valid = [n.location.valid() for n in run_refs]
            if not all(valid):
                counts["missing_location_runs"] += 1
                continue
            path = [(n.location.lon, n.location.lat) for n in run_refs]
            # Reduce cartographic shape vertices while retaining endpoints.
            simplified = list(
                LineString(path)
                .simplify(simplify_tolerance_degrees, preserve_topology=False)
                .coords
            )
            if len(simplified) < 2:
                continue
            start_ref, end_ref = int(run_refs[0].ref), int(run_refs[-1].ref)
            start_is_node = usage[start_ref] > 1 and boundary.covers(Point(path[0]))
            end_is_node = usage[end_ref] > 1 and boundary.covers(Point(path[-1]))
            start_key = (
                ("osm", start_ref)
                if start_is_node
                else ("clip", int(way.id), run_no, 0)
            )
            end_key = (
                ("osm", end_ref) if end_is_node else ("clip", int(way.id), run_no, 1)
            )

            # Split at city edge by retaining only source segments whose midpoint
            # is inside, then split each retained path at level-13 tile borders.
            fragments = []
            fragment = []
            fragment_start = start_key
            for seg_no, (a, b) in enumerate(itertools.pairwise(simplified)):
                if not boundary.covers(Point((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)):
                    if len(fragment) > 1:
                        fragments.append(
                            (
                                fragment_start,
                                ("clip", int(way.id), run_no, seg_no),
                                fragment,
                            )
                        )
                    fragment = []
                    fragment_start = ("clip", int(way.id), run_no, seg_no + 1)
                    counts["outside_segments"] += 1
                    continue
                if not fragment:
                    fragment = [a, b]
                else:
                    append_unique(fragment, b)
            if len(fragment) > 1:
                fragments.append((fragment_start, end_key, fragment))

            for frag_no, (frag_start, frag_end, frag_points) in enumerate(fragments):
                pieces = split_polyline_by_tile(frag_points, level, shift)
                for piece_no, piece in enumerate(pieces):
                    coords = piece["points"]
                    tile = piece["tile"]
                    first = quantized_node(frag_points[0], tile, level, shift)
                    last = quantized_node(frag_points[-1], tile, level, shift)
                    # Repeated source endpoints keep their original graph identity;
                    # tile-edge copies share a generated gateway key.
                    if piece_no == 0 and coords[0] == first:
                        ka = frag_start
                    else:
                        ka = (
                            "gateway",
                            int(way.id),
                            run_no,
                            coords[0][0],
                            coords[0][1],
                        )
                    if piece_no == len(pieces) - 1 and coords[-1] == last:
                        kb = frag_end
                    else:
                        kb = (
                            "gateway",
                            int(way.id),
                            run_no,
                            coords[-1][0],
                            coords[-1][1],
                        )
                    if ka == kb or coords[0] == coords[-1]:
                        continue
                    graph = tiles[tile]
                    for key, xy in ((ka, coords[0]), (kb, coords[-1])):
                        graph["nodes"][key] = xy
                    attrs = make_attrs(coords, tags, direction, policy)
                    edge = {
                        "a": ka,
                        "b": kb,
                        "points": coords,
                        "attrs": attrs,
                        "way_id": int(way.id),
                        "run": run_no,
                    }
                    graph["links"].append(edge)
                    counts["route_link_pieces"] += 1
                    node_tile_copies[ka][tile].append((len(graph["links"]) - 1, 1))
                    node_tile_copies[kb][tile].append((len(graph["links"]) - 1, 0))

    if not tiles:
        raise ValueError("No route graph generated")

    node_tile_copies, merge_counts = merge_degree2_chains(tiles, node_tile_copies)
    counts.update(merge_counts)
    cross_pairs = set()
    external_refs_by_tile = {t: defaultdict(list) for t in tiles}
    for key, copies in node_tile_copies.items():
        for source_tile in copies:
            for target_tile in copies:
                if source_tile == target_tile:
                    continue
                for remote_link, remote_dir in copies[target_tile]:
                    external_refs_by_tile[source_tile][key].append(
                        (target_tile, remote_link, remote_dir)
                    )
                    cross_pairs.add((source_tile, target_tile, key))
    ext_tiles_by_tile = {
        tile: sorted({target for src, target, _ in cross_pairs if src == tile})
        for tile in tiles
    }

    manifests = []
    model_tiles = []
    all_bounds = [math.inf, math.inf, -math.inf, -math.inf]
    for tile, graph in sorted(tiles.items()):
        keys = sorted(graph["nodes"], key=lambda k: (graph["nodes"][k], k))
        node_links = defaultdict(list)
        for idx, edge in enumerate(graph["links"]):
            node_links[edge["a"]].append((False, None, idx, 1))
            node_links[edge["b"]].append((False, None, idx, 0))
        ext_tiles = ext_tiles_by_tile[tile]
        ext_idx = {other: i for i, other in enumerate(ext_tiles)}
        ext_refs = defaultdict(list)
        for key, rows in external_refs_by_tile[tile].items():
            if key not in graph["nodes"]:
                continue
            for other, link, direction_bit in rows:
                ext_refs[key].append((True, ext_idx[other], link, direction_bit))
        ordered = {k: graph["nodes"][k] for k in keys}
        main = encode_route_tile(
            tile, level, shift, ordered, graph["links"], node_links, ext_refs, ext_tiles
        )
        main_path = output / f"{tile}.route.bin"
        main_path.write_bytes(main)
        model_links = []
        for edge in graph["links"]:
            boundary_adjacent = (
                len(node_tile_copies.get(edge["a"], {})) > 1
                or len(node_tile_copies.get(edge["b"], {})) > 1
            )
            model_links.append(
                {
                    "points": edge["points"],
                    "attrs": edge["attrs"],
                    "boundary_adjacent": boundary_adjacent,
                    "curved": len(edge["points"]) > 2,
                    "retain_aux_geometry": boundary_adjacent or len(edge["points"]) > 2,
                }
            )
        model_tiles.append({"tile_id": tile, "links": model_links})
        parsed = parse_intersections(main, tile, level, shift)
        _header = parse_main_header(main)
        if len(parsed) != len(keys):
            raise ValueError(f"ROUTE roundtrip failed: {tile}")
        for row in parsed:
            for ref in row["links"]:
                if ref["external"]:
                    remote = ext_tiles[ref["external_index"]]
                    if ref["link"] >= len(tiles[remote]["links"]):
                        raise ValueError(
                            f"External link out of range: {tile}->{remote}"
                        )
        manifests.append(
            {
                "id": tile,
                "level": level,
                "shift": shift,
                "route_blob": main_path.name,
                "intersections": len(keys),
                "links": len(graph["links"]),
                "external_tiles": ext_tiles,
                "route_bytes": len(main),
                "route_sha256": hashlib.sha256(main).hexdigest(),
            }
        )
        for x, y in graph["nodes"].values():
            lon, lat = x / (1 << 31) * 180, y / (1 << 31) * 180
            all_bounds[0] = min(all_bounds[0], lon)
            all_bounds[1] = min(all_bounds[1], lat)
            all_bounds[2] = max(all_bounds[2], lon)
            all_bounds[3] = max(all_bounds[3], lat)

    source_metadata = source_metadata or {}
    result = {
        "status": "EXPERIMENTAL SIMPLIFIED OSM ROUTE GRAPH; SDK ROUTE RESULT REQUIRED",
        "source_pbf": pbf.name,
        "source_sha256": _sha256_file(pbf),
        "source_attribution": source_metadata.get("attribution"),
        "license": source_metadata.get("license"),
        "region_id": region_id,
        "bbox_lon_lat": all_bounds,
        "level": level,
        "shift": shift,
        "simplify_tolerance_degrees": simplify_tolerance_degrees,
        "counts": dict(counts),
        "tiles": manifests,
        "intersections": sum(t["intersections"] for t in manifests),
        "links": sum(t["links"] for t in manifests),
        "external_tile_refs": sum(len(t["external_tiles"]) for t in manifests),
        "limitations": [
            "OSM turn-restriction relations are not compiled.",
            "Conditional access, lane guidance, toll/avoidance, signs and maneuver guidance are absent.",
            "Prototype speed fallbacks are not official road speed limits.",
            "Do not use for driving until a real route and maneuver output are validated.",
        ],
    }
    result["source"] = {**source_metadata, "sha256": _sha256_file(pbf)}
    (output / "route-manifest.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    )
    (output / "route-model.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profile_id": policy["profile_id"],
                "tiles": model_tiles,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    )
    return result
