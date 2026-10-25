import json
import tempfile
import unittest
from pathlib import Path

from nds_forge.layers.bmd_codec import tile_for, tile_origin
from nds_forge.layers.route_aux import compile_route_aux
from nds_forge.layers.route_codec import (
    encode_aux_sparse,
    parse_aux_geometries,
    parse_aux_header,
)


class RouteAuxPolicyTests(unittest.TestCase):
    def test_sparse_markers_include_straight_fallback_extension_bit(self):
        tile = tile_for(-49.2, -25.4, 13)
        x, y = tile_origin(tile, 13)
        links = [{"points": [[x + 16 * i, y], [x + 16 * (i + 1), y]],
                  "attrs": {}} for i in range(5)]
        blob, stats = encode_aux_sparse(tile, 13, 4, links, {1, 4})
        header = parse_aux_header(blob)
        start, end = header["offsets"][1], header["geometries_offset"]
        # count5 + 000/10/000/000/10 + byte padding. The third bit of each
        # omitted link is required by the native model15 decoder; 00 alone
        # consumes the next link's marker as an extension flag.
        self.assertEqual(blob[start:end], bytes.fromhex("0005 1010"))
        rows = parse_aux_geometries(blob, tile, 13, 4)
        self.assertEqual([row["link"] for row in rows], [1, 4])
        self.assertEqual([row["points"] for row in rows],
                         [[tuple(p) for p in links[i]["points"]] for i in (1, 4)])
        self.assertEqual(stats["retained_geometry_rows"], 2)

    def test_dense_markers_keep_the_original_marker2_encoding(self):
        tile = tile_for(-49.2, -25.4, 13)
        x, y = tile_origin(tile, 13)
        links = [{"points": [[x, y], [x + 16, y + 16]], "attrs": {}}
                 for _ in range(5)]
        blob, _ = encode_aux_sparse(tile, 13, 4, links, set(range(5)))
        header = parse_aux_header(blob)
        self.assertEqual(blob[header["offsets"][1]:header["geometries_offset"]],
                         bytes.fromhex("0005 aa80"))

    def test_omitted_links_cannot_discard_curves_or_have_no_segment(self):
        tile = tile_for(-49.2, -25.4, 13)
        x, y = tile_origin(tile, 13)
        for points in ([], [[x, y]], [[x, y], [x, y]],
                       [[x, y], [x + 16, y], [x + 16, y + 16]]):
            with self.subTest(points=points):
                with self.assertRaisesRegex(ValueError, "two distinct endpoints"):
                    encode_aux_sparse(tile, 13, 4, [{"points": points}], set())

    def test_empty_selection_requires_explicit_experimental_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "main"
            source.mkdir()
            tile = tile_for(-49.2, -25.4, 13)
            x, y = tile_origin(tile, 13)
            link = {"points": [[x, y], [x + 16, y + 16]], "attrs": {},
                    "retain_aux_geometry": False, "boundary_adjacent": False, "curved": False}
            (source / "route-model.json").write_text(json.dumps({
                "schema_version": 1, "profile_id": "neusoft-curitiba-v9",
                "tiles": [{"tile_id": tile, "links": [link]}]}))
            (source / "route-manifest.json").write_text(json.dumps({"tiles": [{"id": tile}]}))
            profile = {"profile_id": "neusoft-curitiba-v9", "level": 13, "shift": 4}
            with self.assertRaisesRegex(ValueError, "requires explicit opt-in"):
                compile_route_aux(source, root / "default", profile)
            compile_route_aux(source, root / "opted-in", {
                **profile, "empty_aux_policy": "retain_first_experimental"})
            report = json.loads((root / "opted-in/aux-manifest.json").read_text())
            self.assertEqual(report["counts"]["experimental_fallback_tiles"], 1)
            self.assertEqual(report["experimental_fallback_tile_ids"], [tile])
            self.assertEqual(report["validation_status"], "unvalidated")
