"""Tests for compiling per-link house-number attribute layers."""

from __future__ import annotations

import unittest

from nds_forge.layers.bmd_codec import SCALE, nds_coordinate, tile_for
from nds_forge.layers.route_attribute_compiler import compile_link_numbers
from nds_forge.layers.route_attribute_layer import (
    read_attribute_layer,
    write_attribute_layer,
)


def _lonlat(x: int, y: int) -> tuple[float, float]:
    return x / SCALE, y / SCALE


class LinkNumberCompilerTests(unittest.TestCase):
    def _inputs(self) -> tuple[dict, dict, int]:
        # Curitiba origin: keeps every synthetic point inside one level-13 tile.
        ox, oy = nds_coordinate(-49.25), nds_coordinate(-25.45)
        lon, lat = _lonlat(ox + 500, oy)
        tile = tile_for(lon, lat, 13)
        model = {"tiles": [{
            "tile_id": tile,
            "links": [
                {"points": [[ox, oy], [ox + 1000, oy]]},
                {"points": [[ox, oy + 5000], [ox + 1000, oy + 5000]]},
            ],
        }]}
        bindings = []
        ranges = []
        # Both addresses on link 0 belong to the same street (one range wins).
        for index, (x, y, number, street) in enumerate(
                [(100, 10, 10, 7000), (900, -10, 20, 7000), (500, 5010, 5, 7001)], start=1):
            lon, lat = _lonlat(ox + x, oy + y)
            bindings.append({"named_object_id": street, "range_id": index,
                             "update_region_id": 439, "lat": lat, "lon": lon})
            ranges.append({"range_id": index, "type": 0, "min_hnr": number,
                           "max_hnr": number, "language_code": None, "prefix": None,
                           "suffix": None, "address_point": None})
        return model, {"ranges": ranges, "bindings": bindings}, tile

    def test_assigns_addresses_to_nearest_link_and_keeps_range(self) -> None:
        model, address_doc, tile = self._inputs()
        layers = compile_link_numbers(model, address_doc)
        self.assertEqual(list(layers), [tile])
        groups = layers[tile]["maps"][0]["container"]["groups"]
        self.assertEqual([group["references"][0]["index"] for group in groups], [0, 1])
        self.assertEqual(groups[0]["values"][0]["value"]["numbers"], [10, 20])
        self.assertEqual(groups[1]["values"][0]["value"]["numbers"], [5, 5])

    def test_layer_is_encodable_and_decodable(self) -> None:
        model, address_doc, tile = self._inputs()
        layers = compile_link_numbers(model, address_doc)
        blob = write_attribute_layer(layers[tile])
        decoded = read_attribute_layer(blob)
        self.assertEqual(decoded["maps"][0]["attribute_ids"], [0xB6])
        self.assertEqual(decoded["maps"][0]["reference_type"], 2)
        groups = decoded["maps"][0]["container"]["groups"]
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["references"][0], {"index": 0, "direction_mask": 3})
        self.assertEqual(groups[0]["values"][0]["value"]["numbers"], [10, 20])

    def test_skips_numbers_outside_native_var29_range(self) -> None:
        model, address_doc, _tile = self._inputs()
        for range_id in (0, 1):
            address_doc["ranges"][range_id]["min_hnr"] = 1535571106
            address_doc["ranges"][range_id]["max_hnr"] = 1535571106
        layers = compile_link_numbers(model, address_doc)
        groups = layers[next(iter(layers))]["maps"][0]["container"]["groups"]
        self.assertEqual([group["values"][0]["value"]["numbers"] for group in groups],
                         [[5, 5]])

    def test_rejects_addresses_far_from_every_link(self) -> None:
        model, address_doc, _tile = self._inputs()
        ox, oy = nds_coordinate(-49.25), nds_coordinate(-25.45)
        lon, lat = _lonlat(ox + 500, oy + 200_000)
        address_doc = {"ranges": address_doc["ranges"][:1],
                       "bindings": [{"named_object_id": 7001, "range_id": 1,
                                     "update_region_id": 439, "lat": lat, "lon": lon}]}
        self.assertEqual(compile_link_numbers(model, address_doc), {})


if __name__ == "__main__":
    unittest.main()
