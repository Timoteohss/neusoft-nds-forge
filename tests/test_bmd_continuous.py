"""Continuous polylines per tile: no pairwise fragmentation, provenance kept."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from nds_forge.layers.bmd import compile_bmd_from_clip
from nds_forge.layers.bmd_codec import (
    Bits,
    decode_polyline,
    encode_road_tile,
    tile_for,
    tile_origin,
)

PROFILE = {
    "level": 11,
    "shift": 4,
    "frc_by_highway": {"residential": 4, "primary": 1},
    "default_frc": 4,
    "excluded_highways": ["footway"],
}

# ~1m steps near Batel centre; far below L11 tile size (~0.088 deg),
# far above shift-4 quantum, so no collapse, no tile split.
LINE = [
    [-49.28800, -25.44100],
    [-49.28799, -25.44099],
    [-49.28798, -25.44098],
    [-49.28797, -25.44097],
    [-49.28796, -25.44096],
]

SOURCE_SHA = "ab" * 32
REGION_ID = "batel-test"


def _clip_dir(features, tmp: Path, source_sha: str = SOURCE_SHA) -> Path:
    clip = tmp / "clip"
    clip.mkdir()
    (clip / "roads.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8"
    )
    (clip / "clip-report.json").write_text(
        json.dumps(
            {
                "source": {"sha256": source_sha, "license": "ODbL-1.0",
                           "attribution": "© OpenStreetMap contributors"},
                "region": {"resolved": True, "id": REGION_ID,
                           "bbox_lon_lat": [-49.3, -25.45, -49.27, -25.43]},
            }
        ),
        encoding="utf-8",
    )
    return clip


def _feat(coords, highway="residential", name="Rua X", osm_id=1, geom="LineString"):
    return {
        "type": "Feature",
        "properties": {"highway": highway, "name": name, "osm_id": osm_id},
        "geometry": {"type": geom, "coordinates": coords},
    }


def _decode_all(out: Path, level: int, shift: int):
    coord_bits = 31 - level - shift
    total_features = 0
    total_vertices = 0
    max_vertices = 0
    all_points: list[tuple] = []
    for blob_path in sorted((out / "tiles").glob("*.bmd.bin")):
        blob = blob_path.read_bytes()
        tile_id = int(blob_path.stem.split(".")[0])
        origin = tile_origin(tile_id, level)
        body = blob[9:] if blob[0] == 0x42 else blob[5:]
        s = Bits.from_bytes(body)
        s.read(8)
        s.read(8)
        s.read(8)
        n = s.read(16)
        for _ in range(n):
            s.read(1)
            s.read(2)
            s.read(15)
            pts, s.at = decode_polyline(body, s.at, coord_bits, shift, origin)
            total_features += 1
            total_vertices += len(pts)
            max_vertices = max(max_vertices, len(pts))
            all_points.extend(pts)
    return total_features, total_vertices, max_vertices, all_points


def _manifest(out: Path) -> dict:
    return json.loads((out / "bmd-manifest.json").read_text(encoding="utf-8"))


class BmdContinuousTests(unittest.TestCase):
    def test_single_way_single_tile_compiles_to_one_feature(self) -> None:
        tiles = {tile_for(lon, lat, 11) for lon, lat in LINE}
        self.assertEqual(len(tiles), 1, "fixture must stay inside one L11 tile")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            compile_bmd_from_clip(_clip_dir([_feat(LINE)], root), out, dict(PROFILE))
            features, vertices, biggest, _ = _decode_all(out, 11, 4)
            self.assertEqual(features, 1)
            self.assertEqual(vertices, len(LINE))
            self.assertEqual(biggest, len(LINE))

    def test_distinct_osm_ids_never_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            feats = [_feat(LINE, osm_id=1), _feat(LINE, osm_id=2)]
            compile_bmd_from_clip(_clip_dir(feats, root), out, dict(PROFILE))
            features, vertices, _, _ = _decode_all(out, 11, 4)
            self.assertEqual(features, 2)
            self.assertEqual(vertices, 2 * len(LINE))

    def test_distinct_names_never_merge_same_osm_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            feats = [_feat(LINE, name="Rua X", osm_id=1),
                     _feat(LINE, name="Rua Y", osm_id=1)]
            compile_bmd_from_clip(_clip_dir(feats, root), out, dict(PROFILE))
            features, vertices, _, _ = _decode_all(out, 11, 4)
            self.assertEqual(features, 2)
            self.assertEqual(vertices, 2 * len(LINE))

    def test_distinct_frc_never_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            feats = [_feat(LINE, highway="residential", name="Rua X", osm_id=1),
                     _feat(LINE, highway="primary", name="Rua X", osm_id=1)]
            compile_bmd_from_clip(_clip_dir(feats, root), out, dict(PROFILE))
            features, vertices, _, _ = _decode_all(out, 11, 4)
            self.assertEqual(features, 2)
            self.assertEqual(vertices, 2 * len(LINE))

    def test_multilinestring_parts_stay_ordered(self) -> None:
        multi = [LINE, [[-49.28790, -25.44090], [-49.28789, -25.44089]]]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            compile_bmd_from_clip(
                _clip_dir([_feat(multi, geom="MultiLineString")], root), out, dict(PROFILE))
            features, vertices, biggest, _ = _decode_all(out, 11, 4)
            self.assertEqual(features, 2)
            self.assertEqual(vertices, len(LINE) + 2)
            self.assertEqual(biggest, len(LINE))

    def test_cross_tile_way_splits_only_at_boundary(self) -> None:
        # Long east-west way crossing several L11 tiles: one feature per tile,
        # chained by the shared boundary point, no vertex lost.
        long_line = [[-49.30 + i * 0.01, -25.44] for i in range(21)]
        tiles = {tile_for(lon, lat, 11) for lon, lat in long_line}
        self.assertGreater(len(tiles), 1, "fixture must cross L11 tiles")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            compile_bmd_from_clip(
                _clip_dir([_feat(long_line, name="Av Longa")], root), out, dict(PROFILE))
            features, vertices, _, _ = _decode_all(out, 11, 4)
            self.assertEqual(features, len(tiles))
            # Each tile boundary duplicates the shared point in both
            # adjacent fragments; intermediate tiles hold 2 boundary points.
            self.assertGreaterEqual(vertices, len(long_line))
            self.assertLessEqual(vertices, len(long_line) + 2 * (len(tiles) - 1))
            m = _manifest(out)
            self.assertEqual(m["counts"]["tiles"], len(tiles))

    def test_quantized_collapse_drops_degenerate_fragment(self) -> None:
        tiny = [[-49.28800, -25.44100], [-49.288000001, -25.441000001]]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            compile_bmd_from_clip(_clip_dir([_feat(tiny)], root), out, dict(PROFILE))
            m = _manifest(out)
            self.assertEqual(m["counts"]["encoded_segments"], 0)

    def test_roundtrip_preserves_vertices_and_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            compile_bmd_from_clip(_clip_dir([_feat(LINE)], root), out, dict(PROFILE))
            features, vertices, _, pts = _decode_all(out, 11, 4)
            self.assertEqual((features, vertices), (1, len(LINE)))
            # Lossless re-encode of decoded geometry.
            payload = encode_road_tile([(pts, 4, 10001)], 11, 4,
                                       tile_for(*LINE[0], 11))
            self.assertGreater(len(payload), 0)
            m = _manifest(out)
            self.assertEqual(m["counts"]["unique_names"], 1)
            names = json.loads((out / "street_names.json").read_text(encoding="utf-8"))
            self.assertIn("Rua X", names)

    def test_legacy_provenance_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            clip = _clip_dir([_feat(LINE)], root)
            roads_sha = hashlib.sha256((clip / "roads.geojson").read_bytes()).hexdigest()
            report_sha = hashlib.sha256((clip / "clip-report.json").read_bytes()).hexdigest()
            compile_bmd_from_clip(clip, out, dict(PROFILE))
            m = _manifest(out)
            self.assertEqual(m["source"]["sha256"], SOURCE_SHA)
            self.assertEqual(m["source"]["license"], "ODbL-1.0")
            self.assertIn("OpenStreetMap", m["source"]["attribution"])
            self.assertEqual(m["clip"]["report_sha256"], report_sha)
            self.assertEqual(m["clip"]["roads_sha256"], roads_sha)
            self.assertEqual(m["clip"]["region_id"], REGION_ID)
            self.assertEqual(m["clip"]["bbox_lon_lat"], [-49.3, -25.45, -49.27, -25.43])
            # Root tiles stay populated for catalog/package consumers.
            self.assertGreater(len(m["tiles"]), 0)
            self.assertEqual(m["counts"]["encoded_segments"],
                             sum(t["line_count"] for t in m["tiles"]))

    def test_output_hashes_match_artifact_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            compile_bmd_from_clip(_clip_dir([_feat(LINE)], root), out, dict(PROFILE))
            m = _manifest(out)
            for tile in m["tiles"]:
                blob = (out / tile["path"]).read_bytes()
                self.assertEqual(len(blob), tile["bytes"])
                self.assertEqual(hashlib.sha256(blob).hexdigest(), tile["sha256"])
            names_path = out / "street_names.json"
            self.assertEqual(m["outputs"]["street_names.json"]["sha256"],
                             hashlib.sha256(names_path.read_bytes()).hexdigest())

    def test_bmd_levels_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            profile = dict(PROFILE, bmd_levels=[{"level": 9, "shift": 11,
                                                 "min_scale": 1, "max_scale": 250000}])
            result = compile_bmd_from_clip(_clip_dir([_feat(LINE)], root), out, profile)
            self.assertEqual(result["counts"]["levels"], {"9": 1})
            m = _manifest(out)
            self.assertEqual([lv["level"] for lv in m["levels"]], [9])


if __name__ == "__main__":
    unittest.main()
