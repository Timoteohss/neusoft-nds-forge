"""Unit tests for UI POI selection and preview contract (Issue #28 / [4.3])."""

from __future__ import annotations

import unittest

from nds_forge.validation.ui_contract import (
    POISelectionError,
    POISelectionManager,
    UISelectionEvent,
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
