"""Compile per-link house-number ranges into the ROUTE attribute layer model.

The native `nds_rt_getHouseNumbersOfLink` accepts the attribute layer's by-link
family (reference type 2/4, values under attribute 0xB6). This compiler assigns
each compiled address point to its nearest link and emits one range per link,
using the dominant street of that link to avoid mixing different streets.

Only numeric ranges are used: point-style addresses (type 3) contain no numeric
range and remain available through the FTS tables.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .bmd_codec import nds_coordinate, tile_for
from .route_attribute_layer import HOUSE_NUMBER_ATTRIBUTE

# 1 NDS unit = 180/2**31 degrees, about 0.93 cm. 20 000 units is roughly 186 m.
MAX_LINK_DISTANCE_UNITS = 20_000
ROUTE_LEVEL = 13
# Attribute-layer house numbers use the native var29 encoding.
MAX_HOUSE_NUMBER = (1 << 29) - 1

# The native collector maps 0x96 to kind 0, which getNamesOfLink requests.
# 0x90/0x94/0x95 are other name families, not interchangeable street labels.
STREET_NAME_ATTRIBUTE = 0x96


def compile_link_names(route_model: dict[str, Any], roads: dict[str, Any],
                       name_doc: dict[str, Any],
                       aux_link_ordinals: dict[int, list[int]] | None = None,
                       ) -> dict[int, dict[str, Any]]:
    """Bind exact OSM way identities to existing NAME ids at each link ordinal.

    No nearest-road inference: parallel roads must never share a label merely
    because their geometries are close. Unnamed/missing ways remain unnamed.
    Labels apply in both directions, independently of travel access rules.
    Marker-2 links resolve through the AUX geometry-row ordinal (refType 4),
    whereas marker-0 links resolve by MAIN link ordinal (refType 2). Supply the
    actual AUX row order when available, including any retain-first fallback.
    """
    way_names: dict[int, str] = {}
    for feature in roads.get("features", []):
        props = feature.get("properties") or {}
        name = props.get("name")
        if not name or props.get("osm_id") is None:
            continue
        way = int(props["osm_id"])
        if way in way_names and way_names[way] != name:
            raise ValueError(f"Conflicting street names for OSM way {way}")
        way_names[way] = str(name)
    name_ids: dict[str, int] = {}
    for record in name_doc.get("records", []):
        if record.get("origin") != "osm_way_names":
            continue
        name, ident = str(record["name"]), int(record["id"])
        if name in name_ids and name_ids[name] != ident:
            raise ValueError(f"Conflicting NAME ids for street {name!r}")
        name_ids[name] = ident
    models: dict[int, dict[str, Any]] = {}
    for tile in route_model.get("tiles", []):
        tile_id = int(tile["tile_id"])
        links = tile.get("links", [])
        aux_links = (aux_link_ordinals.get(tile_id, []) if aux_link_ordinals is not None
                     else [i for i, link in enumerate(links) if link.get("retain_aux_geometry")])
        if len(set(aux_links)) != len(aux_links) or any(i < 0 or i >= len(links) for i in aux_links):
            raise ValueError(f"Invalid AUX row to MAIN link mapping for tile {tile_id}")
        aux_indices = {link: ordinal for ordinal, link in enumerate(aux_links)}
        groups = []
        aux_groups = []
        for index, link in enumerate(links):
            name = way_names.get(int(link["way_id"]))
            if not name:
                continue
            if name not in name_ids:
                raise ValueError(f"No NAME record for named OSM way {link['way_id']}: {name!r}")
            groups.append({
                "references": [{"index": index, "direction_mask": 3}],
                "values": [{"attribute_id": STREET_NAME_ATTRIBUTE, "value": {
                    "kind": "name", "named_object_id": name_ids[name]}}],
            })
            if index in aux_indices:
                aux_groups.append({
                    "references": [{"index": aux_indices[index], "direction_mask": 3}],
                    "values": [{"attribute_id": STREET_NAME_ATTRIBUTE, "value": {
                        "kind": "name", "named_object_id": name_ids[name]}}],
                })
        if groups:
            maps = [{
                "attribute_ids": [STREET_NAME_ATTRIBUTE], "reference_type": 2,
                "container": {"mode": 0, "groups": groups}}]
            if aux_groups:
                maps.append({"attribute_ids": [STREET_NAME_ATTRIBUTE], "reference_type": 4,
                             "container": {"mode": 0, "groups": aux_groups}})
            models[tile_id] = {"extension": 0, "maps": maps}
    return models


def mirror_aux_house_numbers(attribute_models: dict[int, dict[str, Any]],
                             aux_link_ordinals: dict[int, list[int]]) -> None:
    """Preserve house lookups when adding refType-4 street names.

    A type-4 name map enables the native AUX reference path for house numbers
    too. Existing MAIN-indexed ranges therefore need a second map using the
    same geometry-row ordinals. Original by-link groups stay unchanged.
    Mutates only the supplied models, never the source ROUTE database.
    """
    for tile, model in attribute_models.items():
        if not any(m["reference_type"] == 4 and STREET_NAME_ATTRIBUTE in m["attribute_ids"]
                   for m in model["maps"]):
            continue
        if any(m["reference_type"] == 4 and HOUSE_NUMBER_ATTRIBUTE in m["attribute_ids"]
               for m in model["maps"]):
            raise ValueError(f"AUX house-number map already exists for tile {tile}")
        indices = {link: row for row, link in enumerate(aux_link_ordinals.get(tile, []))}
        extra = []
        for source in model["maps"]:
            if source["reference_type"] != 2 or HOUSE_NUMBER_ATTRIBUTE not in source["attribute_ids"]:
                continue
            groups = []
            for group in source["container"]["groups"]:
                for ref in group["references"]:
                    if ref["index"] in indices:
                        groups.append({"references": [{"index": indices[ref["index"]],
                                                       "direction_mask": ref["direction_mask"]}],
                                       "values": group["values"]})
            if groups:
                extra.append({"attribute_ids": source["attribute_ids"], "reference_type": 4,
                              "container": {"mode": 0, "groups": groups}})
        model["maps"].extend(extra)


def _links_by_tile(route_model: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    tiles: dict[int, list[dict[str, Any]]] = {}
    for tile in route_model.get("tiles", []):
        tiles[int(tile["tile_id"])] = list(tile.get("links") or [])
    return tiles


def _build_tree(links: list[dict[str, Any]]) -> Any:
    from shapely.geometry import LineString
    from shapely.strtree import STRtree

    geometries = [LineString([(float(x), float(y)) for x, y in link["points"]])
                  for link in links if isinstance(link.get("points"), list)
                  and len(link["points"]) >= 2]
    # Keep the index mapping explicit: geometries were filtered.
    kept = [index for index, link in enumerate(links)
            if isinstance(link.get("points"), list) and len(link["points"]) >= 2]
    return STRtree(geometries), kept


def compile_link_numbers(route_model: dict[str, Any],
                         address_doc: dict[str, Any],
                         max_distance_units: int = MAX_LINK_DISTANCE_UNITS,
                         ) -> dict[int, dict[str, Any]]:
    """Build one attribute-layer model per tile that has numeric addresses.

    Returns ``{tile_id: model}`` accepted by
    :func:`nds_forge.layers.route_attribute_layer.write_attribute_layer`.
    """
    from shapely.geometry import Point

    ranges = {int(item["range_id"]): item for item in address_doc.get("ranges", [])}
    tiles = _links_by_tile(route_model)
    trees: dict[int, tuple[Any, list[int]]] = {}
    # (tile, link) -> street id -> [min, max]
    per_link: dict[tuple[int, int], dict[int, list[int]]] = defaultdict(
        lambda: defaultdict(lambda: [1 << 30, -1]))
    for binding in address_doc.get("bindings", []):
        if "lat" not in binding or "lon" not in binding:
            continue
        record = ranges.get(int(binding["range_id"]))
        if record is None or record["min_hnr"] is None or record["max_hnr"] is None:
            continue
        # OSM occasionally carries corrupt numeric values outside var29; the
        # native attribute layer cannot represent them, so leave those ranges
        # to the FTS tables instead of failing the whole tile.
        if not 0 <= int(record["min_hnr"]) <= int(record["max_hnr"]) <= MAX_HOUSE_NUMBER:
            continue
        lon, lat = float(binding["lon"]), float(binding["lat"])
        tile_id = tile_for(lon, lat, ROUTE_LEVEL)
        links = tiles.get(tile_id)
        if not links:
            continue
        if tile_id not in trees:
            trees[tile_id] = _build_tree(links)
        tree, kept = trees[tile_id]
        point = Point(float(nds_coordinate(lon)), float(nds_coordinate(lat)))
        index = int(tree.nearest(point))
        link_index = kept[index]
        if point.distance(tree.geometries[index]) > max_distance_units:
            continue
        entry = per_link[(tile_id, link_index)][int(binding["named_object_id"])]
        entry[0] = min(entry[0], int(record["min_hnr"]))
        entry[1] = max(entry[1], int(record["max_hnr"]))
    models: dict[int, dict[str, Any]] = {}
    for (tile_id, link_index), streets in sorted(per_link.items()):
        _, bounds = max(streets.items(), key=lambda item: item[1][1] - item[1][0])
        groups = models.setdefault(tile_id, {
            "extension": 0,
            "maps": [{
                "attribute_ids": [HOUSE_NUMBER_ATTRIBUTE],
                "reference_type": 2,
                "container": {"mode": 0, "groups": []},
            }],
        })["maps"][0]["container"]["groups"]
        groups.append({
            "references": [{"index": link_index, "direction_mask": 3}],
            "values": [{"attribute_id": HOUSE_NUMBER_ATTRIBUTE, "value": {
                "kind": "house_number",
                "encoded_type": 1,
                "label": {"text": "*", "bytes_hex": "2a"},
                "flag": 1,
                "numbers": [int(bounds[0]), int(bounds[1])],
            }}],
        })
    return models
