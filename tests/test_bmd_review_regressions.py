"""Synthetic reproductions of the Batel multi-LOD review failures."""
from __future__ import annotations

import itertools
import json
import sqlite3
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from test_bmd_continuous import LINE, PROFILE, _clip_dir, _feat
from test_bmd_multilevel import BASE, LEVELS, _clip

from nds_forge.layers.bmd import _split_polyline_by_tile, compile_bmd_from_clip
from nds_forge.layers.bmd_codec import (
    Bits,
    decode_polyline,
    nds_coordinate,
    tile_for,
    tile_origin,
)
from nds_forge.layers.catalog import compile_sqlite_catalog
from nds_forge.layers.package_assemble import _write_product


def decoded_features(out: Path, level: int = 11, shift: int = 4):
    """Read geometry, inline attributes and feature/name bindings independently."""
    manifest = json.loads((out / "bmd-manifest.json").read_text())
    result = []
    for tile in manifest["tiles"]:
        blob = (out / tile["path"]).read_bytes()
        names = {}
        if blob[0] == 0x42:
            attr = int.from_bytes(blob[5:9], "big")
            descriptor = int.from_bytes(blob[attr + 4:attr + 9], "big")
            table = (descriptor & ((1 << 36) - 1)) >> 4
            count = Bits.from_bytes(blob[table:])
            assert count.read(2) == 0
            size = count.read(8)
            if size & 0x80:
                size = ((size & 0x7f) << 8) | count.read(8)
            pos = table + (count.at + 7) // 8
            for _ in range(size):
                index = int.from_bytes(blob[pos:pos + 2], "big")
                names[index] = int.from_bytes(blob[pos + 2:pos + 6], "big")
                pos += 6
        body = blob[int.from_bytes(blob[1:5], "big"):]
        bits = Bits.from_bytes(body)
        assert [bits.read(8) for _ in range(3)] == [0, 1, 1]
        size = bits.read(16)
        for index in range(size):
            assert bits.read(1) == 0
            assert bits.read(2) == 3
            attributes = bits.read(15)
            points, bits.at = decode_polyline(
                body, bits.at, 31 - level - shift, shift, tile_origin(tile["tile_id"], level))
            result.append((tile["tile_id"], attributes, points, names.get(index)))
    return result


class BmdReviewRegressionTests(unittest.TestCase):
    def test_versioned_multilod_profile_reaches_registered_bmd_executor(self):
        from nds_forge.config import load_config
        from nds_forge.layers.steps import _profile

        config = load_config(Path(__file__).parents[1] / "examples/batel-v9-bmd-multilod-repro.toml")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            compile_bmd_from_clip(_clip(root), root / "out", _profile(config, "bmd"))
            manifest = json.loads((root / "out/bmd-manifest.json").read_text())
            self.assertEqual(manifest["counts"]["levels"], {"11": 2, "9": 1})
            self.assertEqual([(lv["min_scale"], lv["max_scale"]) for lv in manifest["levels"]],
                             [(1, 50000), (50001, 250000)])

    def test_exact_boundary_vertices_split_both_directions_and_axes(self):
        for axis, edge in ((0, -49.306640625), (1, -25.400390625)):
            line = [[-49.29, -25.44] for _ in range(3)]
            for point, delta in zip(line, (-.01, 0, .01)):
                point[axis] = edge + delta
            for path in (line, list(reversed(line))):
                with self.subTest(axis=axis, path=path):
                    expected = [tile_for((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 11)
                                for a, b in itertools.pairwise(path)]
                    self.assertEqual(len(set(expected)), 2)
                    fragments = _split_polyline_by_tile(path, 11, 4)
                    self.assertEqual([tile for tile, _ in fragments], expected)
                    self.assertEqual(fragments[0][1][-1], fragments[1][1][0])

    def test_boundary_return_preserves_order(self):
        edge = -49.306640625
        line = [[edge - .01, -25.44], [edge, -25.44],
                [edge + .01, -25.44], [edge, -25.44], [edge - .01, -25.44]]
        fragments = _split_polyline_by_tile(line, 11, 4)
        west = tile_for(edge - .005, -25.44, 11)
        east = tile_for(edge + .005, -25.44, 11)
        self.assertEqual([tile for tile, _ in fragments], [west, east, west])
        self.assertEqual([len(points) for _, points in fragments], [2, 3, 2])

    def test_touching_parts_and_missing_ids_never_merge(self):
        for features in (
            [_feat([LINE[:3], LINE[2:]], geom="MultiLineString")],
            [_feat(LINE[:3], osm_id=None), _feat(LINE[2:], osm_id=None)],
            [_feat(LINE[:3], osm_id=1), _feat(LINE[2:], osm_id=1)],
        ):
            with self.subTest(features=features), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                compile_bmd_from_clip(_clip_dir(features, root), root / "out", dict(PROFILE))
                self.assertEqual(len(decoded_features(root / "out")), 2)

    def test_oriented_segments_attributes_and_name_bindings_are_preserved(self):
        features = [_feat(LINE), _feat(list(reversed(LINE)), highway="primary", name="Av Y", osm_id=2)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            compile_bmd_from_clip(_clip_dir(features, root), out, dict(PROFILE))
            names = json.loads((out / "street_names.json").read_text())
            expected = Counter()
            for feature in features:
                props = feature["properties"]
                points = [tuple(round(nds_coordinate(v) / 16) * 16 for v in p)
                          for p in feature["geometry"]["coordinates"]]
                for a, b in itertools.pairwise(points):
                    expected[(a, b, PROFILE["frc_by_highway"][props["highway"]] << 12,
                              names[props["name"]])] += 1
            actual = Counter((a, b, attrs, name) for _, attrs, points, name in decoded_features(out)
                             for a, b in itertools.pairwise(points))
            self.assertEqual(actual, expected)

    def test_invalid_level_policies_fail_before_writing(self):
        invalid = [
            [LEVELS[0], dict(LEVELS[0], shift=5)],
            [{"level": 11, "shift": 4}],
            [dict(LEVELS[0], min_scale=0)],
            [dict(LEVELS[0], max_scale=0)],
            [dict(LEVELS[0], min_scale=True)],
            [dict(LEVELS[0], max_scale=50000.5)],
            [dict(LEVELS[0], max_scale=1 << 32)],
            [dict(LEVELS[0], min_scale=50001)],
            [dict(LEVELS[0], max_scale=60000), LEVELS[1]],
        ]
        for levels in invalid:
            with self.subTest(levels=levels), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with self.assertRaises(ValueError):
                    compile_bmd_from_clip(_clip(root), root / "out", dict(BASE, bmd_levels=levels))
                self.assertFalse((root / "out").exists())

    def test_sqlite_catalog_keeps_level_for_each_bmd_tile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.db"
            compile_sqlite_catalog(path, {
                "profile_id": "test", "region_id": 439, "layer_coverage": {
                    "bmd": {"building_block_id": 1, "level": 11,
                            "tile_ids": [142209789, 34053935], "levels": [
                                {"level": 11, "tile_ids": [142209789]},
                                {"level": 9, "tile_ids": [34053935]}]},
                    "poi": {"building_block_id": 4, "level": 11, "tile_ids": [142209789]}}})
            db = sqlite3.connect(path)
            try:
                self.assertEqual(db.execute(
                    "SELECT level,tile_id FROM layer_coverage WHERE layer_name='bmd' ORDER BY level"
                ).fetchall(), [(9, 34053935), (11, 142209789)])
            finally:
                db.close()

    def test_product_poi_coverage_is_independent_of_bmd_order(self):
        levels = [dict(LEVELS[0], tile_ids=[142209789]), dict(LEVELS[1], tile_ids=[34053935])]
        for ordered, poi_tiles in itertools.product((levels, list(reversed(levels))), ([142209789], [])):
            with self.subTest(order=ordered, poi=poi_tiles), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                catalog = {"layer_coverage": {
                    "bmd": {"levels": ordered},
                    "poi": {"level": 11, "tile_ids": poi_tiles},
                    "route": {"index_level": 9, "index_tile_ids": [123]}}}
                (root / "catalogs.json").write_text(json.dumps(catalog))
                path = root / "product.db"
                db = sqlite3.connect(path)
                try:
                    db.execute("CREATE TABLE tileContentIndexTable(region,buildingBlockId,level,sequence,tile,a,b,c)")
                    db.execute("CREATE TABLE updateRegionNameTable(nameString,updateRegionId)")
                    db.commit()
                    _write_product(path, catalog, {"route": {"region_id": 439}}, catalog_dir=root)
                    self.assertEqual(db.execute(
                        "SELECT level,tile FROM tileContentIndexTable WHERE buildingBlockId=4"
                    ).fetchall(), [(11, t) for t in poi_tiles])
                    self.assertEqual(db.execute(
                        "SELECT level,tile FROM tileContentIndexTable WHERE buildingBlockId=2"
                    ).fetchall(), [(9, 123)])
                finally:
                    db.close()
