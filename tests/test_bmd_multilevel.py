"""RED frente 2: multi-LOD honesto geometry.bmd -> catalog -> shared/product."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from nds_forge.layers.bmd import compile_bmd_from_clip
from nds_forge.layers.catalog import build_coverage_catalogs

LINE = [
    [-49.28800, -25.44100],
    [-49.28799, -25.44099],
    [-49.28798, -25.44098],
    [-49.28797, -25.44097],
    [-49.28796, -25.44096],
]

BASE = {
    "level": 11,
    "shift": 4,
    "frc_by_highway": {"residential": 4, "primary": 1},
    "default_frc": 4,
    "excluded_highways": ["footway"],
}

LEVELS = [
    {"level": 11, "shift": 4, "frc_max": None, "min_scale": 1, "max_scale": 50000},
    {"level": 9, "shift": 11, "frc_max": 3, "min_scale": 50001, "max_scale": 250000},
]


def _clip(tmp: Path) -> Path:
    clip = tmp / "clip"
    clip.mkdir()
    feats = [
        {"type": "Feature",
         "properties": {"highway": "residential", "name": "Rua X", "osm_id": 1},
         "geometry": {"type": "LineString", "coordinates": LINE}},
        {"type": "Feature",
         "properties": {"highway": "primary", "name": "Av Y", "osm_id": 2},
         "geometry": {"type": "LineString", "coordinates": LINE}},
    ]
    (clip / "roads.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    (clip / "clip-report.json").write_text(
        json.dumps({"source": {"sha256": "ab" * 32, "license": "ODbL-1.0",
                               "attribution": "© OpenStreetMap contributors"},
                    "region": {"resolved": True, "id": "t",
                               "bbox_lon_lat": [-49.3, -25.45, -49.27, -25.43]}}),
        encoding="utf-8")
    return clip


class BmdMultiLevelTests(unittest.TestCase):
    def test_levels_emit_manifest_tiles_and_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "bmd"
            compile_bmd_from_clip(_clip(root), out, dict(BASE, bmd_levels=LEVELS))
            manifest = json.loads((out / "bmd-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual([lv["level"] for lv in manifest["levels"]], [11, 9])
            self.assertEqual(manifest["counts"]["levels"], {"11": 2, "9": 1})
            # tiles raiz agregam todos os níveis: consumidor legado não quebra.
            by_level = {lv["level"]: lv for lv in manifest["levels"]}
            self.assertEqual(
                len(manifest["tiles"]),
                sum(len(by_level[lv]["tiles"]) for lv in (11, 9)))
            self.assertTrue((out / "levels" / "11" / "tiles").is_dir())
            self.assertTrue((out / "levels" / "9" / "tiles").is_dir())
            # capability multi sem esconder limitação de pacote.
            self.assertIn("bmd_multi_level", manifest["capabilities_produced"])

    def test_catalog_lists_bmd_levels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "bmd"
            compile_bmd_from_clip(_clip(root), out, dict(BASE, bmd_levels=LEVELS))
            manifest = json.loads((out / "bmd-manifest.json").read_text(encoding="utf-8"))
            names_dir = root / "names"
            names_dir.mkdir()
            (names_dir / "name-records.json").write_text(
                json.dumps({"records": [], "counts": {"street_records": 0,
                                                      "admin_records": 0}}),
                encoding="utf-8")
            route_dir = root / "route"
            route_dir.mkdir()
            (route_dir / "route-manifest.json").write_text(
                json.dumps({"tiles": [], "level": 13, "shift": 4}), encoding="utf-8")
            aux_dir = root / "aux"
            aux_dir.mkdir()
            (aux_dir / "aux-manifest.json").write_text(
                json.dumps({"tiles": [], "profile_id": "p", "counts": {"geometry_rows": 0}}),
                encoding="utf-8")
            cat_dir = root / "catalog"
            build_coverage_catalogs(
                bmd_dir=out, names_dir=names_dir, poi_dir=None,
                route_dir=route_dir, aux_dir=aux_dir, output_dir=cat_dir,
                profile={"profile_id": "p", "region_id": 439,
                         "bmd_levels": manifest["levels"],
                         "grid": {"bmd": {"level": 11, "shift": 4}},
                         "index_levels": {},
                         "poi": {"outside_bmd_policy": "reject"},
                         "layer_rules": {
                             "route": {"name": "route", "building_block_id": 2,
                                       "payload_level": 13, "payload_shift": 4,
                                       "is_optional": True},
                             "names": {"name": "names", "building_block_id": 3,
                                       "payload_level": 0, "payload_shift": 0,
                                       "is_optional": True},
                         }},
                generate_sqlite_index=True)
            # build_coverage_catalogs retorna summary; relê catalogs.json.
            catalog = json.loads((cat_dir / "catalogs.json").read_text(encoding="utf-8"))
            bmd_cov = catalog["layer_coverage"]["bmd"]
            self.assertIn("levels", bmd_cov)
            self.assertEqual(sorted(lv["level"] for lv in bmd_cov["levels"]), [9, 11])
            with sqlite3.connect(cat_dir / "catalog-index.db") as db:
                actual = db.execute(
                    "SELECT level,tile_id FROM layer_coverage WHERE layer_name='bmd' ORDER BY level,tile_id"
                ).fetchall()
            expected = sorted((lv["level"], t) for lv in bmd_cov["levels"] for t in lv["tile_ids"])
            self.assertEqual(actual, expected)

    def test_shared_product_write_all_levels(self) -> None:
        from nds_forge.layers.package_assemble import _write_product, _write_shared
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "bmd"
            compile_bmd_from_clip(_clip(root), out, dict(BASE, bmd_levels=LEVELS))
            manifest = json.loads((out / "bmd-manifest.json").read_text(encoding="utf-8"))
            catalog_dir = root / "catalog"
            catalog_dir.mkdir()
            catalog = {
                "layer_coverage": {
                    "bmd": {"building_block_id": 1, "levels": [
                        {"level": 11, "shift": 4, "min_scale": 1, "max_scale": 50000,
                         "tile_ids": [t["tile_id"] for t in manifest["levels"][0]["tiles"]]},
                        {"level": 9, "shift": 11, "min_scale": 50001, "max_scale": 250000,
                         "tile_ids": [t["tile_id"] for t in manifest["levels"][1]["tiles"]]}]},
                    "route": {"building_block_id": 2, "payload_level": 13, "payload_shift": 4,
                              "index_level": 9, "tile_ids": [], "index_tile_ids": []},
                    "poi": {"building_block_id": 4, "level": 11, "tile_ids": []},
                }
            }
            (catalog_dir / "catalogs.json").write_text(
                json.dumps(catalog), encoding="utf-8")
            shared_db = root / "shared.db"
            con = sqlite3.connect(shared_db)
            con.execute("CREATE TABLE levelMetadataTable(level, buildingBlockId, shift, a, b, c)")
            con.execute("CREATE TABLE regionMetadataTable(isoCountryCode)")
            con.execute("INSERT INTO regionMetadataTable VALUES ('XXX')")
            con.commit()
            con.close()
            _write_shared(shared_db, catalog,
                          {"route": {"region_id": 439}}, catalog_dir=catalog_dir)
            con = sqlite3.connect(shared_db)
            rows = con.execute(
                "SELECT level,buildingBlockId,shift,a,b FROM levelMetadataTable WHERE buildingBlockId=1 ORDER BY level").fetchall()
            con.close()
            self.assertEqual(rows, [(9, 1, 11, 50001, 250000), (11, 1, 4, 1, 50000)])
            product_db = root / "product.db"
            con = sqlite3.connect(product_db)
            con.execute("""CREATE TABLE tileContentIndexTable
                           (region, buildingBlockId, level, sequence, tile, a, b, c)""")
            con.execute("CREATE TABLE updateRegionNameTable(nameString, updateRegionId)")
            con.execute("INSERT INTO updateRegionNameTable VALUES ('x', 439)")
            con.commit()
            con.close()
            _write_product(product_db, catalog,
                           {"route": {"region_id": 439}, "package_label": "t"},
                           catalog_dir=catalog_dir)
            con = sqlite3.connect(product_db)
            bb1 = con.execute(
                "SELECT level,tile FROM tileContentIndexTable WHERE buildingBlockId=1 ORDER BY level,tile").fetchall()
            con.close()
            n11 = len(manifest["levels"][0]["tiles"])
            n9 = len(manifest["levels"][1]["tiles"])
            self.assertEqual(len(bb1), n11 + n9)
            self.assertEqual(sum(1 for c, _ in bb1 if c == 11), n11)
            self.assertEqual(sum(1 for c, _ in bb1 if c == 9), n9)


if __name__ == "__main__":
    unittest.main()
