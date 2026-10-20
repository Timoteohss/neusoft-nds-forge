"""Tests for region boundary validation, geometry integrity, CRS, and relation resolution (Issue #9 / [5.1])."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from nds_forge.region import (
    BoundaryError,
    load_boundary_geojson,
    resolve_region,
    validate_boundary_geometry,
)


class RegionBoundaryTests(unittest.TestCase):

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

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
        self.assertTrue(boundary.is_valid)
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
        self.assertTrue(boundary.is_valid)
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
        self.assertTrue(boundary.is_valid)
        self.assertFalse(boundary.has_holes)
        self.assertEqual(boundary.license, "ODbL-1.0")
        self.assertEqual(boundary.attribution, "© OSM contributors")
        self.assertEqual(boundary.osm_relation_id, 12345)
        self.assertIsNotNone(boundary.sha256)

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
