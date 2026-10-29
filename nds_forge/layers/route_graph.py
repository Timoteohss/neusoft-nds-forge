"""Build a version-pinned compact level-13 OSM route graph.

Ported from the v9 legacy builder and limited to the explicit Curitiba v9
profile policy. Unlike a link-per-OSM-segment graph, it preserves junctions and
route shape between them as polylines. It targets the highest route level in
the stock Neusoft catalog and keeps auxiliary geometry offsets small.

This experimental output preserves graph topology and compiles via-node turn
restrictions into native transition masks. Conditional access, via-way
restrictions, maneuver guidance, and legal speed semantics remain unencoded.
"""

import hashlib
import itertools
import json
import math
from collections import Counter, defaultdict
from typing import Any

from .bmd_codec import nds_coordinate, tile_origin
from .route_attributes import bearing_degrees, build_link_attributes, distance_meters
from .route_codec import encode_route_tile, parse_intersections, parse_main_header
from .route_rules import (
    DirectedLink,
    RestrictionType,
    RouteRulesCompiler,
    TransitionStatus,
    TurnRestriction,
)
from .route_support import split_segment_by_tile


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def eligible(way, policy, denied=None, driveable=None):
    # Check the highway tag before materializing a dict: most PBF ways are not
    # driveable and are discarded here.
    driveable = set(policy["driveable_highways"]) if driveable is None else driveable
    if way.tags.get("highway") not in driveable:
        return False, {}
    tags = {t.k: t.v for t in way.tags}
    denied = set(policy["denied_access_values"]) if denied is None else denied
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


def merge_degree2_chains(tiles, node_tile_copies, protected_nodes=frozenset()):
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
            # A restriction via-node is part of routing topology even when it
            # has degree two. Merging it would erase the transition boundary.
            if node in protected_nodes:
                continue
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
                "direction": "both",
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


def _read_turn_restrictions(pbf, osmium):
    """Read OSM restriction relations and the via nodes/ways they protect."""
    restrictions = []
    via_nodes, via_ways = set(), set()
    relation_bits = osmium.osm.osm_entity_bits.RELATION
    for relation in osmium.FileProcessor(str(pbf), entities=relation_bits):
        if not relation.is_relation():
            continue
        tags = {tag.k: tag.v for tag in relation.tags}
        if tags.get("type") != "restriction":
            continue
        members = [(member.type, int(member.ref), member.role) for member in relation.members]
        from_ways = [ref for kind, ref, role in members if kind == "w" and role == "from"]
        to_ways = [ref for kind, ref, role in members if kind == "w" and role == "to"]
        via_members = [(kind, ref) for kind, ref, role in members if role == "via"]
        via_node_ids = [ref for kind, ref in via_members if kind == "n"]
        via_way_ids = [ref for kind, ref in via_members if kind == "w"]
        via_nodes.update(via_node_ids)
        via_ways.update(via_way_ids)
        restriction_value = tags.get("restriction:motorcar", tags.get("restriction: motorcar"))
        restriction_value = restriction_value or tags.get("restriction")
        conditional = tags.get("restriction:conditional") or tags.get("restriction:motorcar:conditional")
        kind = RestrictionType.from_tag(conditional or restriction_value or "unknown")
        if conditional:
            kind = RestrictionType.CONDITIONAL
        # Keep malformed relations as audited unknown records where possible.
        from_way = from_ways[0] if len(from_ways) == 1 else -1
        to_way = to_ways[0] if len(to_ways) == 1 else -1
        via_node = via_node_ids[0] if len(via_node_ids) == 1 and not via_way_ids else None
        via_way = via_way_ids[0] if len(via_way_ids) == 1 else None
        restrictions.append(TurnRestriction(
            relation_id=int(relation.id), restriction_type=kind,
            from_way_id=from_way, to_way_id=to_way,
            via_node_id=via_node, via_way_id=via_way,
            conditional_tag=conditional,
        ))
    return restrictions, via_nodes, via_ways


def _compile_tile_transitions(graph, restrictions, tile):
    """Compile a diagnostic transition table over this tile's directed links."""
    links = []
    # The restriction compiler keys nodes by OSM id; graph nodes use tagged
    # keys ("osm", id) or synthetic clip keys, so translate them to ints here.
    node_ids: dict[Any, int] = {}

    def node_id(key):
        if key not in node_ids:
            node_ids[key] = key[1] if key[0] == "osm" else -(len(node_ids) + 1)
        return node_ids[key]

    for index, edge in enumerate(graph["links"]):
        direction = edge.get("direction", "unknown")
        if direction in ("forward", "both"):
            links.append(DirectedLink(index * 2, edge["way_id"], node_id(edge["a"]),
                                      node_id(edge["b"]), edge["attrs"]["length_raw"] / 100))
        if direction in ("reverse", "both"):
            links.append(DirectedLink(index * 2 + 1, edge["way_id"], node_id(edge["b"]),
                                      node_id(edge["a"]), edge["attrs"]["length_raw"] / 100))
    if not restrictions or not links:
        return [], [], None
    # A relation can be outside this tile or only partially represented after
    # clipping. Keep unsupported records auditable; compile the represented
    # subset without claiming that the native ROUTE codec consumes it.
    compiler = RouteRulesCompiler(fail_closed_unknown=False)
    compiled = compiler.compile(links, restrictions)
    rows = []
    for (from_link, to_link), status in sorted(compiled.transitions.items()):
        rows.append({"from_directed_link": from_link, "to_directed_link": to_link,
                     "status": TransitionStatus(status).name})
    audits = [dict(row, tile_id=tile) for row in compiled.unsupported_restrictions]
    return rows, audits, compiled


MASK_MAX_ENTRIES = 4  # attr 8 is one u16: entryCount bits per entry


def _intersection_masks(graph, keys, node_links, compiled, tile):
    """Map compiled prohibited transitions onto native attr-8 mask words.

    The native getter returns one entry per intersection link reference, in
    reference order, and entry ``i`` carries one ``entryCount``-bit field per
    candidate. Clearing bit ``entry * count + candidate`` forbids that
    transition. Only intersections with a real prohibition get a word.
    """
    if compiled is None:
        return [], []
    index_of = {key: position for position, key in enumerate(keys)}
    positions = {
        key: {ref[2]: position for position, ref in enumerate(refs)}
        for key, refs in node_links.items()
    }
    words: dict[Any, int] = {}
    audits = []
    for (from_id, to_id), status in sorted(compiled.transitions.items()):
        if status != TransitionStatus.PROHIBITED_LEGAL:
            continue
        edge_index, direction = from_id >> 1, from_id & 1
        edge = graph["links"][edge_index]
        via = edge["b"] if direction == 0 else edge["a"]
        entry = positions.get(via, {}).get(edge_index)
        target = positions.get(via, {}).get(to_id >> 1)
        if entry is None or target is None:
            audits.append({"tile_id": tile, "reason": "transition endpoint outside the tile",
                           "from_directed_link": from_id, "to_directed_link": to_id})
            continue
        count = len(node_links[via])
        if count > MASK_MAX_ENTRIES:
            audits.append({"tile_id": tile,
                           "reason": f"intersection degree {count} exceeds the 16-bit mask",
                           "from_directed_link": from_id, "to_directed_link": to_id})
            continue
        word = words.get(via)
        if word is None:
            word = sum(((1 << count) - 1) << (index * count) for index in range(count))
        words[via] = word & ~(1 << (entry * count + target))
    masks = [(index_of[via], word) for via, word in sorted(words.items(), key=lambda item: index_of[item[0]])]
    return masks, audits


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
    # Brazil/Paraná reuses the reviewed v9 policy values under its own declared
    # local identity; both names are pinned to the same level-13/shift-4 contract.
    if policy.get("profile_id") not in ("neusoft-curitiba-v9", "neusoft-brazil-parana"):
        raise ValueError(
            "This graph builder is pinned to a reviewed neusoft-curitiba-v9 policy"
        )
    if (level, shift) != (13, 4):
        raise ValueError("The reviewed v9 route graph requires level 13 / shift 4")
    boundary = prep(shape(boundary_geometry))
    restrictions, via_node_ids, via_way_ids = _read_turn_restrictions(pbf, osmium)
    protected_nodes = {("osm", node_id) for node_id in via_node_ids}

    # Junction candidates are nodes shared by two or more routable OSM ways.
    usage = Counter()
    counts = Counter()
    denied = set(policy["denied_access_values"])
    driveable = set(policy["driveable_highways"])
    bits = osmium.osm.osm_entity_bits.WAY
    for way in osmium.FileProcessor(str(pbf), entities=bits):
        ok, _ = eligible(way, policy, denied, driveable)
        if not ok:
            continue
        counts["eligible_ways"] += 1
        if int(way.id) in via_way_ids:
            protected_nodes.update(("osm", int(node.ref)) for node in way.nodes)
        for node in way.nodes:
            usage[int(node.ref)] += 1

    tiles = defaultdict(lambda: {"nodes": {}, "links": []})
    node_tile_copies = defaultdict(lambda: defaultdict(list))
    entities = osmium.osm.osm_entity_bits.NODE | osmium.osm.osm_entity_bits.WAY
    for way in osmium.FileProcessor(str(pbf), entities=entities).with_locations():
        if not way.is_way():
            continue
        ok, tags = eligible(way, policy, denied, driveable)
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
                        "direction": direction,
                    }
                    graph["links"].append(edge)
                    counts["route_link_pieces"] += 1
                    node_tile_copies[ka][tile].append((len(graph["links"]) - 1, 1))
                    node_tile_copies[kb][tile].append((len(graph["links"]) - 1, 0))

    if not tiles:
        raise ValueError("No route graph generated")

    node_tile_copies, merge_counts = merge_degree2_chains(
        tiles, node_tile_copies, protected_nodes
    )
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
    transition_audits = []
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
        transitions, audits, compiled = _compile_tile_transitions(graph, restrictions, tile)
        masks, mask_audits = _intersection_masks(graph, keys, node_links, compiled, tile)
        audits = audits + mask_audits
        main = encode_route_tile(
            tile, level, shift, ordered, graph["links"], node_links, ext_refs, ext_tiles,
            intersection_masks=masks or None,
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
                    "way_id": edge["way_id"],
                    "direction": edge.get("direction", "unknown"),
                    "boundary_adjacent": boundary_adjacent,
                    "curved": len(edge["points"]) > 2,
                    "retain_aux_geometry": boundary_adjacent or len(edge["points"]) > 2,
                }
            )
        transition_audits.extend(audits)
        model_tiles.append({"tile_id": tile, "links": model_links,
                            "turn_transitions": transitions})
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
        "osm_turn_restriction_relations": len(restrictions),
        "compiled_local_turn_transitions": sum(
            len(tile["turn_transitions"]) for tile in model_tiles
        ),
        "turn_restriction_audit": transition_audits,
        "limitations": [
            "Turn transitions are emitted only in route-model.json for offline inspection; the native ROUTE.NDS transition section is not encoded, so the Neusoft SDK does not yet enforce these restrictions.",
            "Via-way and conditional turn restrictions are not compiled into the native format.",
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
