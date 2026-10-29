"""Declared Brazil/Paraná identity, path derivation, and package metadata."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from nds_forge.adapters.container import pack_package_tree
from nds_forge.config import ConfigError, load_config
from nds_forge.layers.package_assemble import (
    _rewrite_metadata_identity,
    _write_product,
    _write_shared,
)
from nds_forge.pipeline import plan
from nds_forge.profile import TargetProfile
from nds_forge.region_binding import (
    BRAZIL_PARANA,
    LEGACY_ISRAEL_V9,
    RegionBinding,
    RegionBindingError,
    TimeZoneSpec,
    resolve_region_binding,
)

ROOT = Path(__file__).parents[1]


class BrazilRegionBindingTests(unittest.TestCase):
    def test_independent_brazil_identity_and_paths(self) -> None:
        binding = BRAZIL_PARANA
        self.assertEqual(binding.product_group_id, 9000)
        self.assertEqual(binding.nav_product_id, 90000011)
        self.assertEqual(binding.deco_product_id, 90000012)
        self.assertEqual(binding.update_region_set_id, 9100)
        self.assertEqual(binding.update_region_id, 9102)
        self.assertEqual(binding.ur_code, "UR9102")
        self.assertEqual(binding.area_code, "AC")
        self.assertEqual(binding.version_name, "N.J6.AC.26.00.02.00")
        self.assertEqual(binding.iso_country_code, "BRA")
        self.assertEqual(binding.timezone.olson, "America/Sao_Paulo")
        self.assertEqual(binding.timezone.utc_offset_quarters, -12)
        self.assertIn("db/NAV/UR9102/BMD.NDS", binding.generated_zv_paths())
        self.assertEqual(len(binding.required_paths()), 21)
        self.assertNotIn("db/NAV/UR439/BMD.NDS", binding.required_paths())
        self.assertEqual(binding.formats()["db/NAV/UR9102/BMD.NDS"], "zv_zlib")
        self.assertEqual(binding.remap_path("db/NAV/UR439/NAME.NDS"), "db/NAV/UR9102/NAME.NDS")

    def test_binding_roundtrips_through_schema(self) -> None:
        parsed = RegionBinding.from_dict(BRAZIL_PARANA.to_dict())
        self.assertEqual(parsed, BRAZIL_PARANA)
        self.assertIs(parsed.validate_against_schema(), True)

    def test_invalid_identity_fails_closed(self) -> None:
        cases = [
            ({"ur_code": "UR439"}, "ur_code must equal"),
            ({"area_code": "ZZ", "version_name": "N.J6.AC.26.00.02.00"}, "version_name area"),
            ({"update_region_set_id": 9102}, "must be distinct"),
            ({"timezone": TimeZoneSpec(6, "America/Sao_Paulo", -8, "X", "X")}, "UTC-3"),
        ]
        for changes, message in cases:
            with self.subTest(message=message):
                data = BRAZIL_PARANA.to_dict()
                data.update({key: value.to_dict() if isinstance(value, TimeZoneSpec) else value
                             for key, value in changes.items()})
                with self.assertRaisesRegex(RegionBindingError, message):
                    RegionBinding.from_dict(data)

    def test_config_cross_checks_region_and_key_paths(self) -> None:
        data = BRAZIL_PARANA.to_dict()
        with self.assertRaisesRegex(RegionBindingError, "names.region_id"):
            resolve_region_binding({
                "profile_id": "neusoft-brazil-parana",
                "region_binding": data,
                "names": {"region_id": 439},
            })
        with self.assertRaisesRegex(RegionBindingError, "container_keys"):
            resolve_region_binding({
                "profile_id": "neusoft-brazil-parana",
                "region_binding": data,
                "container_keys": {"db/NAV/UR439/BMD.NDS": "env:KEY"},
            })

    def test_legacy_preset_is_explicit_and_curitiba_config_uses_it(self) -> None:
        self.assertEqual(LEGACY_ISRAEL_V9.ur_code, "UR439")
        config = load_config(ROOT / "examples/neusoft-parana-v9.toml")
        self.assertEqual(config.region_binding, LEGACY_ISRAEL_V9)
        with self.assertRaisesRegex(RegionBindingError, "region_binding is required"):
            resolve_region_binding({"profile_id": "unrelated-profile"})

    def test_brazil_example_loads_and_plans_without_side_effects(self) -> None:
        config = load_config(ROOT / "examples/neusoft-brazil-parana.toml")
        self.assertEqual(config.region_binding, BRAZIL_PARANA)
        result = plan(config, "container.pack")
        self.assertEqual(result["side_effects"], "none")
        self.assertEqual(result["network"], "not accessed")
        ids = [step["id"] for step in result["steps"]]
        self.assertIn("package.assemble", ids)
        self.assertIn("container.pack", ids)

    def test_brazil_profile_file_validates(self) -> None:
        path = ROOT / "profiles/neusoft-brazil-parana.profile.json"
        profile = TargetProfile.from_json(path.read_text(encoding="utf-8"))
        profile.validate(for_release=False)
        self.assertIs(profile.validate_against_schema(), True)
        self.assertEqual((profile.country_id, profile.region_id), (76, 9102))
        self.assertEqual(profile.ur_code, "UR9102")
        self.assertFalse(profile.claim_brazil_authority)


class BrazilPackageIdentityTests(unittest.TestCase):
    def _product_db(self, path: Path) -> None:
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE tileContentIndexTable("
                       "region,buildingBlockId,level,sequence,tile,a,b,c)")
            db.execute("CREATE TABLE updateRegionTable("
                       "updateRegionId,versionId,isDirty,mapDataReleaseDate,releaseYear,"
                       "releaseMonth,versionName,nameString)")
            db.execute("CREATE TABLE updateRegionNameTable("
                       "updateRegionId,languageCode,nameString)")
            db.execute("CREATE TABLE updateRegionSetTable("
                       "updateRegionSetId,updateRegionId,nameString)")
            db.execute("CREATE TABLE updateRegionSetNameTable("
                       "updateRegionSetId,languageCode,nameString)")
            db.execute("CREATE TABLE productNameTable(languageCode,productName)")
            db.execute("CREATE TABLE versionTable(versionName)")
            db.execute("CREATE TABLE urBuildingBlockVersionTable(updateRegionId,uri)")
            db.execute("INSERT INTO updateRegionTable VALUES("
                       "439,7,0,'2025-01-01',2025,1,'old','Old')")
            db.execute("INSERT INTO updateRegionNameTable VALUES(439,1,'Old')")
            db.execute("INSERT INTO updateRegionSetNameTable VALUES(9100,1,'Old set')")
            db.execute("INSERT INTO productNameTable VALUES(1,'Old')")
            db.execute("INSERT INTO versionTable VALUES('old')")
            db.execute("INSERT INTO urBuildingBlockVersionTable VALUES(439,'UR439/BMD.NDS')")

    def test_product_and_shared_rows_use_declared_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = {"layer_coverage": {
                "bmd": {"level": 11, "shift": 4, "tile_ids": [1]},
                "route": {"payload_level": 13, "payload_shift": 4,
                          "index_level": 9, "index_tile_ids": [2]},
                "poi": {"level": 11, "tile_ids": []},
            }}
            (root / "catalogs.json").write_text(json.dumps(catalog), encoding="utf-8")
            product = root / "PRODUCT.sqlite"
            self._product_db(product)
            _write_product(product, catalog, {"route": {"region_id": 9102}},
                           catalog_dir=root, binding=BRAZIL_PARANA)
            with sqlite3.connect(product) as db:
                self.assertEqual(db.execute(
                    "SELECT updateRegionId,versionName,isDirty,mapDataReleaseDate,releaseYear,"
                    "releaseMonth FROM updateRegionTable").fetchall(),
                    [(9102, "N.J6.AC.26.00.02.00", 0, "2026-01-01", 2026, 1)])
                self.assertEqual(db.execute(
                    "SELECT DISTINCT updateRegionId FROM updateRegionNameTable").fetchall(),
                    [(9102,)])
                self.assertEqual(db.execute(
                    "SELECT updateRegionSetId,updateRegionId FROM updateRegionSetTable").fetchall(),
                    [(9100, 9102)])
                self.assertEqual(db.execute("SELECT uri FROM urBuildingBlockVersionTable").fetchall(),
                                 [("UR9102/BMD.NDS",)])
                self.assertEqual(db.execute(
                    "SELECT DISTINCT region FROM tileContentIndexTable").fetchall(),
                    [(9102,)])

            shared = root / "SHARED.sqlite"
            with sqlite3.connect(shared) as db:
                db.execute("CREATE TABLE levelMetadataTable("
                           "level,buildingBlockId,shift,a,b,c)")
                db.execute("CREATE TABLE regionMetadataTable(isoCountryCode)")
                db.execute("INSERT INTO regionMetadataTable VALUES('ISR')")
                db.execute("CREATE TABLE timeZoneTable(timeZoneId,validFrom,utcOffset,dst,nameId)")
                db.execute("CREATE TABLE timeZoneNameTable("
                           "timeZoneNameId,languageCode,timeZoneNameString)")
                db.execute("CREATE TABLE timeZoneOlsonIdTable(timeZoneId,olson)")
                db.execute("CREATE TABLE regionTimeZoneTable(regionId,timeZoneId)")
            _write_shared(shared, catalog, {"route": {"region_id": 9102}},
                          catalog_dir=root, binding=BRAZIL_PARANA)
            with sqlite3.connect(shared) as db:
                self.assertEqual(db.execute(
                    "SELECT isoCountryCode FROM regionMetadataTable").fetchall(), [("BRA",)])
                self.assertEqual(db.execute(
                    "SELECT olson FROM timeZoneOlsonIdTable").fetchall(),
                    [("America/Sao_Paulo",)])
                self.assertEqual(db.execute(
                    "SELECT utcOffset FROM timeZoneTable").fetchall(), [(-12,)])
                self.assertEqual(db.execute(
                    "SELECT timeZoneNameId,timeZoneNameString FROM timeZoneNameTable "
                    "ORDER BY timeZoneNameId").fetchall(),
                    [(0, "Brasilia Time"), (1, "BRT")])

    def test_metadata_paths_and_ids_are_rewritten(self) -> None:
        document = {
            "ndsDb": {
                "products": [{
                    "productId": 10800701, "productGroupId": 1, "productName": "Brazil",
                    "files": [{"filePath": "NAV/UR439/BMD.NDS"}],
                    "updateRegions": [{
                        "updateRegionId": 439, "updateRegionSetId": 9100,
                        "urName": "Old", "urSetName": "Old",
                        "files": [{"filePath": "NAV/UR439/NAME.NDS"}],
                    }],
                }, {
                    "productId": 10800703, "productGroupId": 1, "productName": "WorldMap",
                    "files": [],
                }],
            },
        }
        _rewrite_metadata_identity(document, BRAZIL_PARANA)
        product, world = document["ndsDb"]["products"]
        self.assertEqual((product["productId"], product["productGroupId"]), (90000011, 9000))
        self.assertEqual((world["productId"], world["productGroupId"]), (90000012, 9000))
        region = product["updateRegions"][0]
        self.assertEqual((region["updateRegionId"], region["updateRegionSetId"]), (9102, 9100))
        self.assertEqual(region["urName"], "Paraná")
        self.assertEqual(region["files"][0]["filePath"], "NAV/UR9102/NAME.NDS")
        self.assertEqual(product["files"][0]["filePath"], "NAV/UR9102/BMD.NDS")


class BrazilContainerTests(unittest.TestCase):
    def test_container_pack_uses_brazil_paths_and_manifest(self) -> None:
        binding = BRAZIL_PARANA
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "package"
            db = package / "db"
            db.mkdir(parents=True)
            generated = binding.generated_zv_paths()
            for relative in binding.required_paths():
                path = db / Path(relative).relative_to("db")
                path.parent.mkdir(parents=True, exist_ok=True)
                if relative in generated:
                    with sqlite3.connect(path) as conn:
                        conn.execute("CREATE TABLE test(id INTEGER)")
                elif relative in binding.static_zv_paths():
                    path.write_bytes(b"ZV-zlib\0" + b"\0" * 200)
                elif relative in {"db/DECO/PRODUCT.NDS", "db/NAV/PRODUCT.NDS", "db/ROOT.NDS"}:
                    with sqlite3.connect(path) as conn:
                        conn.execute("CREATE TABLE test(id INTEGER)")
                elif relative == "db/Area.txt":
                    path.write_text("AC", encoding="utf-8")
                elif relative == "db/ROOT.NDK":
                    path.write_text("# Neusoft NDK 1.0\n", encoding="utf-8")
            (db / "metadata.json").write_text(json.dumps({"files": []}), encoding="utf-8")
            (package / "package-layout.json").write_text(json.dumps({
                "schema_version": 1,
                "profile_id": binding.profile_id,
                "region_binding": binding.to_dict(),
                "required_paths": sorted(binding.required_paths()),
                "pack_zv_sqlite_paths": sorted(generated),
                "files": [],
            }), encoding="utf-8")
            keys = {path: b"0123456789ABCDEF" for path in generated}
            report = pack_package_tree(package, root / "final", keys)
            self.assertEqual(report["file_count"], 21)
            self.assertEqual(report["region_binding"]["ur_code"], "UR9102")
            actual = {"db/" + path.relative_to(root / "final/db").as_posix()
                      for path in (root / "final/db").rglob("*") if path.is_file()}
            self.assertEqual(actual, binding.required_paths())
            self.assertTrue((root / "final/db/NAV/UR9102/BMD.NDS").read_bytes().startswith(b"ZV-zlib\0"))
            self.assertFalse((root / "final/db/NAV/UR439").exists())


class BrazilConfigValidationTests(unittest.TestCase):
    def test_mismatched_example_region_id_is_rejected(self) -> None:
        text = (ROOT / "examples/neusoft-brazil-parana.toml").read_text(encoding="utf-8")
        broken = text.replace("[neusoft.names]\nadmin_scope", "[neusoft.names]\nregion_id = 439\nadmin_scope")
        # Remove the original region_id line so the duplicate key does not hide the mismatch.
        broken = broken.replace("region_id = 9102\nstreet_class", "street_class", 1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.toml"
            path.write_text(broken, encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "Region binding inválido"):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
