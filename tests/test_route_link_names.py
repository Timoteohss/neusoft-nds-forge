"""Street names must follow route link ordinals, not geographic proximity."""
import unittest
import json
import tempfile
from pathlib import Path

from nds_forge.layers.route_attribute_compiler import compile_link_names, mirror_aux_house_numbers
from nds_forge.layers.route_attribute_layer import read_attribute_layer, write_attribute_layer
from nds_forge.layers.route_aux import compile_route_aux
from nds_forge.layers.bmd_codec import tile_for, tile_origin


class LinkNameTests(unittest.TestCase):
    def test_adding_aux_names_preserves_house_ranges_via_aux_reference(self):
        value = {"attribute_id": 0xB6, "value": {"kind": "house_number", "encoded_type": 1,
                 "label": {"text": "*"}, "flag": 1, "numbers": [49, 88]}}
        original = {"references": [{"index": 12, "direction_mask": 3}], "values": [value]}
        models = {1: {"extension": 0, "maps": [
            {"attribute_ids": [0xB6], "reference_type": 2,
             "container": {"mode": 0, "groups": [original]}},
            {"attribute_ids": [0x96], "reference_type": 4,
             "container": {"mode": 0, "groups": []}}]}}
        mirror_aux_house_numbers(models, {1: [3, 12]})
        layer = read_attribute_layer(write_attribute_layer(models[1]))
        self.assertEqual(layer["maps"][0]["container"]["groups"][0]["references"][0]["index"], 12)
        mirrored = layer["maps"][-1]
        self.assertEqual(mirrored["reference_type"], 4)
        self.assertEqual(mirrored["container"]["groups"][0]["references"][0]["index"], 1)
        self.assertEqual(mirrored["container"]["groups"][0]["values"][0]["value"]["numbers"], [49,88])

    def test_exact_way_binding_preserves_ordinals_and_both_directions(self):
        route = {"tiles": [{"tile_id": 123, "links": [
            {"way_id": 9}, {"way_id": 8}, {"way_id": 9}, {"way_id": 7}]}]}
        roads = {"features": [
            {"properties": {"osm_id": 9, "name": "Rua Goiânia"}},
            {"properties": {"osm_id": 8, "name": "Rua paralela"}},
            {"properties": {"osm_id": 7}}]}
        names = {"records": [{"id": 101, "name": "Rua Goiânia", "origin": "osm_way_names"},
                             {"id": 102, "name": "Rua paralela", "origin": "osm_way_names"}]}
        models = compile_link_names(route, roads, names)
        decoded = read_attribute_layer(write_attribute_layer(models[123]))
        name_map = decoded["maps"][0]
        self.assertEqual(name_map["attribute_ids"], [0x96])
        self.assertEqual(name_map["reference_type"], 2)
        groups = name_map["container"]["groups"]
        self.assertEqual([g["references"] for g in groups], [
            [{"index": i, "direction_mask": 3}] for i in range(3)])
        self.assertEqual([g["values"][0]["value"]["named_object_id"] for g in groups],
                         [101, 102, 101])

    def test_named_way_missing_from_name_database_fails_instead_of_dangling_id(self):
        with self.assertRaisesRegex(ValueError, "NAME record"):
            compile_link_names({"tiles": [{"tile_id": 1, "links": [{"way_id": 1}]}]},
                               {"features": [{"properties": {"osm_id": 1, "name": "Rua X"}}]},
                               {"records": []})

    def test_aux_names_use_dense_geometry_row_ordinal_including_unnamed_rows(self):
        route = {"tiles": [{"tile_id": 1, "links": [
            {"way_id": 7}, {"way_id": 9}, {"way_id": 9}, {"way_id": 9}]}]}
        roads = {"features": [{"properties": {"osm_id": 9, "name": "Rua X"}}]}
        names = {"records": [{"origin": "osm_way_names", "id": 101, "name": "Rua X"}]}
        # Row 0 is unnamed, row 1 belongs to MAIN link 3. Do not use 3 or 0.
        model = compile_link_names(route, roads, names, {1: [0, 3]})[1]
        decoded = read_attribute_layer(write_attribute_layer(model))
        aux = decoded["maps"][1]
        self.assertEqual(aux["reference_type"], 4)
        self.assertEqual(aux["container"]["groups"][0]["references"],
                         [{"index": 1, "direction_mask": 3}])
        self.assertEqual(len(aux["container"]["groups"]), 1)

    def test_conflicting_names_for_one_way_fail(self):
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            compile_link_names({"tiles": []}, {"features": [
                {"properties": {"osm_id": 1, "name": "A"}},
                {"properties": {"osm_id": 1, "name": "B"}}]}, {"records": []})

    def test_aux_pipeline_wires_name_maps_and_retain_first_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for folder in ("main", "roads", "names"):
                (root / folder).mkdir()
            tile = tile_for(-49.2, -25.4, 13)
            x, y = tile_origin(tile, 13)
            links = [{"way_id": 9, "points": [[x, y], [x+16, y+16]],
                      "attrs": {}, "retain_aux_geometry": False,
                      "boundary_adjacent": False, "curved": False}]
            (root/"main/route-model.json").write_text(json.dumps({
                "schema_version": 1, "profile_id": "neusoft-curitiba-v9",
                "tiles": [{"tile_id": tile, "links": links}]}))
            (root/"main/route-manifest.json").write_text(json.dumps({"tiles": [tile]}))
            (root/"roads/roads.geojson").write_text(json.dumps({"features": [
                {"properties": {"osm_id": 9, "name": "Rua X"}}]}))
            (root/"names/name-records.json").write_text(json.dumps({"records": [
                {"origin": "osm_way_names", "name": "Rua X", "id": 101}]}))
            compile_route_aux(root/"main", root/"out", {
                "profile_id": "neusoft-curitiba-v9", "level": 13, "shift": 4,
                "empty_aux_policy": "retain_first_experimental"},
                roads_dir=root/"roads", names_dir=root/"names")
            layer = read_attribute_layer((root/"out"/f"{tile}.route-attrs.bin").read_bytes())
            self.assertEqual([m["reference_type"] for m in layer["maps"]], [2, 4])
            report = json.loads((root/"out/aux-manifest.json").read_text())
            self.assertEqual(report["counts"]["attribute_link_names"], 1)
            self.assertIn("street_name_attribute_layer", report["capabilities_produced"])


if __name__ == "__main__":
    unittest.main()
