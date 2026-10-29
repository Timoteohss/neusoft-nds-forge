"""Codec tests for the ROUTE auxiliary attribute layer."""

from __future__ import annotations

import os
import sqlite3
import unittest
from pathlib import Path

from nds_forge.layers.route_attribute_layer import (
    DEFAULT_REFERENCE_INDEX,
    LayerFormatError,
    read_attribute_layer,
    write_attribute_layer,
)


def _model() -> dict:
    return {
        "extension": 0,
        "maps": [
            {
                "attribute_ids": [148, 149],
                "reference_type": 1,
                "container": {
                    "mode": 1,
                    "groups": [
                        {"references": [{"index": 3, "direction_mask": 1},
                                        {"index": 7, "direction_mask": 2}],
                         "values": [
                             {"attribute_id": 148, "value": {"kind": "name", "named_object_id": 81655}},
                             {"attribute_id": 149, "value": {"kind": "name", "named_object_id": 40000}},
                         ]},
                    ],
                },
            },
            {
                "attribute_ids": [0xB6],
                "reference_type": 3,
                "container": {
                    "mode": 0,
                    "groups": [
                        {"references": [{"index": 2, "direction_mask": 1}],
                         "values": [{"attribute_id": 0xB6, "value": {
                             "kind": "house_number", "encoded_type": 1,
                             "label": {"text": "*", "bytes_hex": "2a"}, "flag": 1,
                             "numbers": [2, 6]}}]},
                        {"references": [{"index": 0, "direction_mask": 2}],
                         "values": [{"attribute_id": 0xB6, "value": {
                             "kind": "house_number", "encoded_type": 0,
                             "label": {"text": "A", "bytes_hex": "41"}, "flag": 0,
                             "numbers": [21, 21]}}]},
                    ],
                },
            },
            {
                "attribute_ids": [0xB6],
                "reference_type": 4,
                "container": {
                    "mode": 0,
                    "groups": [
                        {"references": [{"index": 65535 & 0x7FFF, "direction_mask": 3}],
                         "values": [{"attribute_id": 0xB6, "value": {
                             "kind": "house_number", "encoded_type": 2,
                             "label": {"text": "", "bytes_hex": ""}, "flag": 1,
                             "numbers": [5, 7, 9]}}]},
                    ],
                },
            },
            {
                "attribute_ids": [144],
                "reference_type": 10,
                "container": {
                    "mode": 1,
                    "groups": [
                        {"references": [{"index": DEFAULT_REFERENCE_INDEX, "direction_mask": 3}],
                         "values": [{"attribute_id": 144, "value": {"kind": "name",
                                                                    "named_object_id": 901}}]},
                    ],
                },
            },
        ],
    }


class AttributeLayerCodecTests(unittest.TestCase):
    def test_encode_decode_roundtrip(self) -> None:
        blob = write_attribute_layer(_model())
        decoded = read_attribute_layer(blob)
        self.assertEqual(decoded["map_count"], 4)
        self.assertEqual([m["attribute_ids"] for m in decoded["maps"]],
                         [[148, 149], [0xB6], [0xB6], [144]])
        self.assertEqual([m["reference_type"] for m in decoded["maps"]], [1, 3, 4, 10])
        first = decoded["maps"][0]["container"]
        self.assertEqual(first["mode"], 1)
        self.assertEqual(first["groups"][0]["references"],
                         [{"index": 3, "direction_mask": 1}, {"index": 7, "direction_mask": 2}])
        self.assertEqual(first["groups"][0]["values"][0]["value"]["named_object_id"], 81655)
        house = decoded["maps"][1]["container"]["groups"]
        self.assertEqual(house[0]["values"][0]["value"]["numbers"], [2, 6])
        self.assertEqual(house[1]["values"][0]["value"]["numbers"], [21, 21])
        self.assertEqual(house[1]["values"][0]["value"]["label"]["bytes_hex"], "41")
        multi = decoded["maps"][2]["container"]["groups"][0]
        self.assertEqual(multi["references"], [{"index": 0x7FFF, "direction_mask": 3}])
        self.assertEqual(multi["values"][0]["value"]["numbers"], [5, 7, 9])
        default = decoded["maps"][3]["container"]["groups"][0]["references"][0]
        self.assertEqual(default["index"], DEFAULT_REFERENCE_INDEX)

    def test_rejects_single_direction_on_group_reference_types(self) -> None:
        model = _model()
        model["maps"][0]["container"]["groups"][0]["references"][0]["direction_mask"] = 3
        with self.assertRaises(LayerFormatError):
            write_attribute_layer(model)

    def test_rejects_unknown_house_encoded_type(self) -> None:
        model = _model()
        model["maps"][1]["container"]["groups"][0]["values"][0]["value"]["encoded_type"] = 4
        with self.assertRaises(LayerFormatError):
            write_attribute_layer(model)

    @unittest.skipUnless(os.environ.get("NDS_OEM_LAYER_DB"),
                         "set NDS_OEM_LAYER_DB to a decrypted OEM ROUTE.NDS to check byte-identical roundtrip")
    def test_oem_blobs_roundtrip_byte_identical(self) -> None:
        database = sqlite3.connect(f"file:{Path(os.environ['NDS_OEM_LAYER_DB'])}?mode=ro", uri=True)
        try:
            rows = database.execute(
                "select id, nameAttributeLayer from routingAuxTileTable "
                "where nameAttributeLayer is not null").fetchall()
        finally:
            database.close()
        self.assertGreater(len(rows), 100)
        for tile, blob in rows:
            with self.subTest(tile=tile):
                self.assertEqual(write_attribute_layer(read_attribute_layer(blob)), blob)


if __name__ == "__main__":
    unittest.main()
