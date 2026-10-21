"""Unit tests for UI POI selection and preview contract (Issue #28 / [4.3])."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from nds_forge.layers.poi import compile_osm_pois
from nds_forge.osm_model import OSMDataset, OSMNode, OSMProvenance
from nds_forge.validation.ui_contract import (
    POISelectionError,
    POISelectionManager,
    UISelectionEvent,
    _record_coordinates,
)


class UIContractTests(unittest.TestCase):
    """Test suite for POI selection contract, preview coordinates, and mock stability."""

    def setUp(self) -> None:
        self.candidate_hash = "f1e2d3c4b5a60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef0"
        self.records = [
            {
                "id": 1001,
                "name": "Museu Oscar Niemeyer",
                "category": "museum",
                "coordinates": [-49.2667, -25.4103],
                "morton": 500123,
                "search_tags": "MON Museu do Olho",
            },
            {
                "id": 1002,
                "name": "Jardim Botânico de Curitiba",
                "category": "attraction",
                "coordinates": [-49.2394, -25.4428],
                "morton": 500456,
                "search_tags": "Botanico Estufa",
            },
        ]
        self.mgr = POISelectionManager(self.records, candidate_hash=self.candidate_hash)

    def test_select_poi_by_id_success(self) -> None:
        event = self.mgr.select_poi_by_id(1001)
        self.assertIsInstance(event, UISelectionEvent)
        self.assertEqual(event.item_type, "poi")
        self.assertEqual(event.item_id, 1001)
        self.assertEqual(event.display_name, "Museu Oscar Niemeyer")
        self.assertEqual(event.category, "museum")
        self.assertEqual(event.coord_lon, -49.2667)
        self.assertEqual(event.coord_lat, -25.4103)
        self.assertEqual(event.morton_code, 500123)
        self.assertEqual(event.candidate_hash, self.candidate_hash)

    def test_select_missing_poi_raises(self) -> None:
        with self.assertRaises(POISelectionError):
            self.mgr.select_poi_by_id(9999)

    def test_compiler_record_longitude_latitude_populates_preview_coordinates(self) -> None:
        dataset = OSMDataset(
            schema_version=1,
            provenance=OSMProvenance(
                source="synthetic_ui_contract_fixture",
                license="ODbL-1.0",
                attribution="© OpenStreetMap contributors",
            ),
        )
        dataset.add_node(OSMNode(
            id=9001, lat=-25.4, lon=-49.2,
            tags={"amenity": "cafe", "name": "Café de teste"},
        ))
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            compile_osm_pois(dataset, output_dir, {
                "profile_id": "ui-test", "region_id": 1,
                "grid": {"bmd": {"level": 11}},
            })
            document = json.loads((output_dir / "poi-records.json").read_text(encoding="utf-8"))

        record = document["records"][0]
        self.assertIn("longitude", record)
        self.assertIn("latitude", record)
        self.assertEqual(_record_coordinates(record), (-49.2, -25.4))

    def test_coordinate_formats_and_invalid_records(self) -> None:
        records = [
            {"id": 1, "name": "coordinates", "coordinates": [-49.2, -25.4]},
            {"id": 2, "name": "compiler keys", "longitude": -48.1, "latitude": -24.3},
            {"id": 3, "name": "short keys", "lon": -47.0, "lat": -23.2},
        ]
        manager = POISelectionManager(records, self.candidate_hash)
        expected = {1: (-49.2, -25.4), 2: (-48.1, -24.3), 3: (-47.0, -23.2)}
        for record_id, coordinates in expected.items():
            with self.subTest(record_id=record_id):
                event = manager.select_poi_by_id(record_id)
                self.assertEqual((event.coord_lon, event.coord_lat), coordinates)

        invalid_records = [
            {"id": 4, "name": "missing"},
            {"id": 5, "name": "partial", "longitude": -49.2},
            {"id": 6, "name": "nan", "coordinates": [float("nan"), -25.4]},
            {"id": 7, "name": "infinite", "longitude": float("inf"), "latitude": -25.4},
            {"id": 8, "name": "longitude out of range", "lon": 181, "lat": 0},
            {"id": 9, "name": "latitude out of range", "lon": 0, "lat": -91},
            {"id": 10, "name": "string coordinate", "coordinates": ["-49.2", -25.4]},
            {"id": 11, "name": "boolean coordinate", "lon": True, "lat": 0},
        ]
        invalid_manager = POISelectionManager(invalid_records, self.candidate_hash)
        for record in invalid_records:
            with self.subTest(record=record["name"]):
                with self.assertRaisesRegex(POISelectionError, "coordinate"):
                    invalid_manager.select_poi_by_id(record["id"])

    def test_search_and_select_by_name_and_tags(self) -> None:
        # Match by name
        ev1 = self.mgr.search_and_select("Oscar")
        self.assertIsNotNone(ev1)
        self.assertEqual(ev1.item_id, 1001)

        # Match by tag alias
        ev2 = self.mgr.search_and_select("Estufa")
        self.assertIsNotNone(ev2)
        self.assertEqual(ev2.item_id, 1002)

        # No match
        self.assertIsNone(self.mgr.search_and_select("NonExistentPlace"))


if __name__ == "__main__":
    unittest.main()
