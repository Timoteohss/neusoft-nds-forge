"""Tests for region boundary validation, geometry integrity, CRS, and relation resolution (Issue #9 / [5.1])."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nds_forge.region import (
    BoundaryError,
    load_boundary_geojson,
    resolve_region,
    validate_boundary_geometry,
)
from nds_forge.region.boundary import SUPPORTED_CRS, validate_crs


class RegionBoundaryTests(unittest.TestCase):

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def assert_topology_matches_available_backend(self, boundary) -> None:
        try:
            import shapely.geometry  # noqa: F401
        except ImportError:
            self.assertIsNone(boundary.is_valid)
            self.assertIs(boundary.topology_checked, False)
        else:
            self.assertIs(boundary.is_valid, True)
            self.assertIs(boundary.topology_checked, True)

    def test_valid_polygon_with_interior_holes(self) -> None:
        # Exterior box from (-10, -10) to (10, 10), hole from (-2, -2) to (2, 2)
        poly_with_hole = {
            "type": "Polygon",
            "coordinates": [
                [[-10.0, -10.0], [10.0, -10.0], [10.0, 10.0], [-10.0, 10.0], [-10.0, -10.0]],
                [[-2.0, -2.0], [2.0, -2.0], [2.0, 2.0], [-2.0, 2.0], [-2.0, -2.0]],
            ],
        }
        boundary = validate_boundary_geometry(poly_with_hole)
        self.assert_topology_matches_available_backend(boundary)
        self.assertTrue(boundary.has_holes)
        self.assertEqual(boundary.holes_count, 1)
        self.assertEqual(boundary.bbox, (-10.0, -10.0, 10.0, 10.0))

    def test_valid_multipolygon_with_holes(self) -> None:
        multipoly = {
            "type": "MultiPolygon",
            "coordinates": [
                # Poly 1: with hole
                [
                    [[-10.0, -10.0], [-5.0, -10.0], [-5.0, -5.0], [-10.0, -5.0], [-10.0, -10.0]],
                    [[-8.0, -8.0], [-6.0, -8.0], [-6.0, -6.0], [-8.0, -6.0], [-8.0, -8.0]],
                ],
                # Poly 2: no hole
                [
                    [[5.0, 5.0], [10.0, 5.0], [10.0, 10.0], [5.0, 10.0], [5.0, 5.0]],
                ],
            ],
        }
        boundary = validate_boundary_geometry(multipoly)
        self.assert_topology_matches_available_backend(boundary)
        self.assertTrue(boundary.has_holes)
        self.assertEqual(boundary.holes_count, 1)
        self.assertEqual(boundary.bbox, (-10.0, -10.0, 10.0, 10.0))

    def test_invalid_unclosed_ring_rejected(self) -> None:
        unclosed = {
            "type": "Polygon",
            "coordinates": [
                [[-10.0, -10.0], [10.0, -10.0], [10.0, 10.0], [-10.0, 10.0]],  # Missing closing point
            ],
        }
        with self.assertRaisesRegex(BoundaryError, "não está fechado"):
            validate_boundary_geometry(unclosed)

    def test_coordinate_out_of_bounds_rejected(self) -> None:
        out_of_bounds = {
            "type": "Polygon",
            "coordinates": [
                [[-10.0, -95.0], [10.0, -95.0], [10.0, 10.0], [-10.0, 10.0], [-10.0, -95.0]],
            ],
        }
        with self.assertRaisesRegex(BoundaryError, "fora dos limites geográficos"):
            validate_boundary_geometry(out_of_bounds)

    def test_self_intersecting_geometry_rejected(self) -> None:
        # Figure-8 / bow-tie self-intersecting polygon
        bowtie = {
            "type": "Polygon",
            "coordinates": [
                [[0.0, 0.0], [2.0, 2.0], [2.0, 0.0], [0.0, 2.0], [0.0, 0.0]],
            ],
        }
        # When shapely is installed, it detects self-intersection and explains validity
        try:
            import shapely  # noqa: F401
            with self.assertRaisesRegex(BoundaryError, "Geometria de limite inválida"):
                validate_boundary_geometry(bowtie)
        except ImportError:
            pass

    def test_without_shapely_only_structure_and_bbox_are_verified(self) -> None:
        polygon = {"type": "Polygon", "coordinates": [
            [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}
        bowtie = {"type": "Polygon", "coordinates": [
            [[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]]}
        for geometry in (polygon, bowtie):
            with self.subTest(geometry=geometry), patch.dict("sys.modules", {
                    "shapely.geometry": None, "shapely.validation": None}):
                boundary = validate_boundary_geometry(geometry)
                self.assertIsNone(boundary.is_valid)
                self.assertFalse(boundary.topology_checked)
                self.assertEqual(boundary.bbox, (0.0, 0.0, 2.0, 2.0))
                self.assertEqual(boundary.geometry, geometry)
                serialized = json.loads(json.dumps(boundary.to_dict()))
                self.assertIsNone(serialized["is_valid"])
                self.assertIs(serialized["topology_checked"], False)

    def test_shapely_validation_records_checked_topology(self) -> None:
        try:
            import shapely.geometry  # noqa: F401
        except ImportError:
            self.skipTest("requires optional shapely")
        polygon = {"type": "Polygon", "coordinates": [
            [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}
        boundary = validate_boundary_geometry(polygon)
        self.assertIs(boundary.is_valid, True)
        self.assertIs(boundary.topology_checked, True)
        self.assertIs(boundary.to_dict()["topology_checked"], True)
        bowtie = {"type": "Polygon", "coordinates": [
            [[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]]}
        with self.assertRaisesRegex(BoundaryError, "Geometria de limite inválida"):
            validate_boundary_geometry(bowtie)

    def test_region_json_preserves_unknown_topology_without_shapely(self) -> None:
        from nds_forge.config import load_config
        from nds_forge.pipeline import run
        path = self.root / "boundary.geojson"
        path.write_text(json.dumps({"type": "Polygon", "coordinates": [
            [[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]]}))
        config_path = self.root / "project.toml"
        config_path.write_text('''schema_version = 1
project_id = "boundary-topology-fixture"
workspace = "work"
[region]
source = "boundary_geojson"
boundary_geojson = "boundary.geojson"
[source]
kind = "pbf"
path = "unused-synthetic-source.pbf"
[pipeline]
target = "region.resolve"
''')
        with patch.dict("sys.modules", {"shapely.geometry": None, "shapely.validation": None}):
            loaded = load_boundary_geojson(path)
            self.assertIsNone(loaded.is_valid)
            self.assertFalse(loaded.topology_checked)
            run(load_config(config_path), "region.resolve")
        region = json.loads((self.root / "work/artifacts/region/region.json").read_text())
        self.assertIsNone(region["is_valid"])
        self.assertIs(region["topology_checked"], False)
        self.assertEqual(region["bbox"], [0.0, 0.0, 2.0, 2.0])

    def test_unsupported_crs_rejected(self) -> None:
        geojson_3857 = self.root / "proj3857.geojson"
        geojson_3857.write_text(json.dumps({
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::3857"}},
            "features": [{
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                },
            }],
        }), encoding="utf-8")

        with self.assertRaisesRegex(BoundaryError, "CRS não suportado"):
            load_boundary_geojson(geojson_3857)

    def test_all_supported_crs_names_use_exact_normalized_matching(self) -> None:
        for supported in SUPPORTED_CRS:
            for name in (supported, " \t" + supported.swapcase() + "\n"):
                for metadata in (name, {"properties": {"name": name}}, {"type": "name", "name": name}):
                    with self.subTest(metadata=metadata):
                        self.assertEqual(validate_crs(metadata), "EPSG:4326")

    def test_crs_prefixes_suffixes_and_empty_names_are_rejected(self) -> None:
        names = ["EPSG:43260", "EPSG:4326:foo", "WGS84_extra", "", " \t\n"]
        for supported in SUPPORTED_CRS:
            names.extend(["prefix:" + supported, supported + ":suffix"])
        for name in names:
            for metadata in (name, {"properties": {"name": name}}, {"type": "name", "name": name}):
                with self.subTest(metadata=metadata), self.assertRaises(BoundaryError):
                    validate_crs(metadata)

    def test_only_absent_crs_uses_existing_wgs84_default(self) -> None:
        self.assertEqual(validate_crs(None), "EPSG:4326")
        malformed = [{}, {"type": "name"}, {"properties": {"name": None}},
                     {"properties": {"name": ["EPSG:4326"]}}, {"properties": {"name": 4326}}]
        for metadata in malformed:
            with self.subTest(metadata=metadata), self.assertRaises(BoundaryError):
                validate_crs(metadata)

    def test_geojson_loader_rejects_substring_crs_and_preserves_wgs84_coordinates(self) -> None:
        path = self.root / "crs-regression.geojson"
        geometry = {"type": "Polygon", "coordinates": [
            [[-49.3, -25.5], [-49.2, -25.5], [-49.2, -25.4], [-49.3, -25.4], [-49.3, -25.5]]]}
        document = {"type": "Feature", "geometry": geometry,
                    "crs": {"properties": {"name": "EPSG:43260"}}}
        path.write_text(json.dumps(document))
        with self.assertRaisesRegex(BoundaryError, "CRS não suportado"):
            load_boundary_geojson(path)
        document["crs"]["properties"]["name"] = "  urn:OGC:def:crs:EPSG::4326  "
        path.write_text(json.dumps(document))
        boundary = load_boundary_geojson(path)
        self.assertEqual(boundary.crs, "EPSG:4326")
        self.assertEqual(boundary.geometry, geometry)

    def test_valid_wgs84_geojson_preserves_license_and_hash(self) -> None:
        valid_file = self.root / "valid.geojson"
        payload = {
            "type": "FeatureCollection",
            "licence": "ODbL-1.0",
            "features": [{
                "type": "Feature",
                "properties": {"osm_type": "relation", "osm_id": 12345, "attribution": "© OSM contributors"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-49.3, -25.5], [-49.2, -25.5], [-49.2, -25.4], [-49.3, -25.4], [-49.3, -25.5]]],
                },
            }],
        }
        valid_file.write_text(json.dumps(payload), encoding="utf-8")

        boundary = load_boundary_geojson(valid_file)
        self.assert_topology_matches_available_backend(boundary)
        self.assertFalse(boundary.has_holes)
        self.assertEqual(boundary.license, "ODbL-1.0")
        self.assertEqual(boundary.attribution, "© OSM contributors")
        self.assertEqual(boundary.osm_relation_id, 12345)
        self.assertIsNotNone(boundary.sha256)

    def test_feature_collection_with_multiple_features_fails_explicitly(self) -> None:
        path = self.root / "multiple-features.geojson"

        def feature(west: float, south: float) -> dict:
            east, north = west + 1, south + 1
            return {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "Polygon", "coordinates": [[
                    [west, south], [east, south], [east, north], [west, north], [west, south],
                ]]},
            }

        path.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [feature(0, 0), feature(10, 10)],
        }), encoding="utf-8")

        with self.assertRaisesRegex(BoundaryError, "múltiplas features não é suportada"):
            load_boundary_geojson(path)

    def test_single_feature_polygon_preserves_geometry_and_metadata(self) -> None:
        path = self.root / "single-feature.geojson"
        geometry = {
            "type": "MultiPolygon",
            "coordinates": [[[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]],
        }
        path.write_text(json.dumps({
            "type": "FeatureCollection",
            "licence": "ODbL-1.0",
            "features": [{
                "type": "Feature",
                "properties": {
                    "osm_type": "relation", "osm_id": 12345,
                    "attribution": "© OpenStreetMap contributors",
                },
                "geometry": geometry,
            }],
        }), encoding="utf-8")

        boundary = load_boundary_geojson(path)

        self.assertEqual(boundary.geometry, geometry)
        self.assertEqual(boundary.geom_type, "MultiPolygon")
        self.assertEqual(boundary.bbox, (0.0, 0.0, 1.0, 1.0))
        self.assertEqual(boundary.license, "ODbL-1.0")
        self.assertEqual(boundary.attribution, "© OpenStreetMap contributors")
        self.assertEqual(boundary.osm_relation_id, 12345)

    def test_feature_collection_rejects_non_polygon_geometry(self) -> None:
        path = self.root / "non-polygon-feature.geojson"
        path.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
            }],
        }), encoding="utf-8")

        with self.assertRaisesRegex(BoundaryError, "Tipo de geometria não suportado.*Polygon ou MultiPolygon"):
            load_boundary_geojson(path)

    def test_relation_without_file_unresolved_by_default_no_fetch(self) -> None:
        config = {
            "id": "relation-only",
            "name": "Relation Test",
            "source": "relation",
            "osm_relation_id": 99999,
        }
        # Without network opt-in: must remain unresolved, no network access performed
        result = resolve_region(config, self.root, allow_network=False)
        self.assertEqual(result["boundary_source"], "osm_relation_reference_only")
        self.assertEqual(result["osm_relation_id"], 99999)
        self.assertIn("unresolved", result["resolution"])

        # With network opt-in but unpinned/unconfigured source: must fail closed, no arbitrary fetch
        with self.assertRaisesRegex(BoundaryError, "exige region.boundary_url e region.boundary_sha256 pinados"):
            resolve_region(config, self.root, allow_network=True)


if __name__ == "__main__":
    unittest.main()
