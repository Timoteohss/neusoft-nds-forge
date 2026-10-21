"""Unit tests for Address Layer compiler and native query evaluator (Issue #30 / [4.5])."""

from __future__ import annotations

import sqlite3
import unittest

from nds_forge.layers.address import (
    AddressRange,
    AddressRangeType,
    UnsupportedAddressFormError,
    compile_address_layer,
    infer_range_type,
    pack_named_object_to_range,
    pack_range_to_named_object,
    query_house_number_native,
    query_named_objects_for_range,
    unpack_named_object_to_range,
    unpack_range_to_named_object,
    verify_address_release_readiness,
    write_address_tables,
)


class AddressLayerTests(unittest.TestCase):
    """Test suite for address range structures, bitwise packing, compilation, and native query."""

    def setUp(self) -> None:
        self.fts_db = sqlite3.connect(":memory:")
        self.fts_db.execute("""
            CREATE TABLE ftsHouseNumberRangeTable(
                rangeId INTEGER NOT NULL,
                type INTEGER NOT NULL,
                minHnr INTEGER,
                maxHnr INTEGER,
                languageCode INTEGER,
                prefix TEXT,
                suffix TEXT,
                addressPoint TEXT,
                primary key(rangeId),
                check(minHnr is not NULL or addressPoint is not NULL)
            )
        """)
        self.fts_db.execute("""
            CREATE TABLE namedObject2HouseNRTable(
                namedObject2HouseNumberRangeId INTEGER NOT NULL,
                updateRegionId INTEGER NOT NULL,
                primary key(namedObject2HouseNumberRangeId, updateRegionId)
            )
        """)
        self.fts_db.execute("""
            CREATE TABLE houseNR2NamedObjectTable(
                houseNumberRange2NamedObjectId INTEGER NOT NULL,
                updateRegionId INTEGER NOT NULL,
                primary key(houseNumberRange2NamedObjectId, updateRegionId)
            )
        """)

    def tearDown(self) -> None:
        self.fts_db.close()

    def test_bitwise_packing_roundtrip(self) -> None:
        named_obj_id = 35824
        range_id = 1

        packed_no2hnr = pack_named_object_to_range(named_obj_id, range_id)
        # Expected: 0x8bf000000001 == 153862908411905
        self.assertEqual(packed_no2hnr, 153862908411905)
        unpacked_no, unpacked_r = unpack_named_object_to_range(packed_no2hnr)
        self.assertEqual(unpacked_no, named_obj_id)
        self.assertEqual(unpacked_r, range_id)

        packed_hnr2no = pack_range_to_named_object(range_id, named_obj_id)
        # Expected: 0x100008bf0 == 4295003120
        self.assertEqual(packed_hnr2no, 4295003120)
        unpacked_r2, unpacked_no2 = unpack_range_to_named_object(packed_hnr2no)
        self.assertEqual(unpacked_r2, range_id)
        self.assertEqual(unpacked_no2, named_obj_id)

    def test_range_validation_constraints(self) -> None:
        # Valid continuous
        r_cont = AddressRange(range_id=1, type=AddressRangeType.CONTINUOUS, min_hnr=10, max_hnr=25)
        self.assertEqual(r_cont.type, 0)

        # Valid even
        r_even = AddressRange(range_id=2, type=AddressRangeType.EVEN, min_hnr=2, max_hnr=20)
        self.assertEqual(r_even.type, 1)

        # Valid odd
        r_odd = AddressRange(range_id=3, type=AddressRangeType.ODD, min_hnr=1, max_hnr=19)
        self.assertEqual(r_odd.type, 2)

        # Valid point
        r_pt = AddressRange(range_id=4, type=AddressRangeType.POINT, address_point="500-A")
        self.assertEqual(r_pt.type, 3)

        # Parity mismatch: EVEN with odd bounds
        with self.assertRaises(UnsupportedAddressFormError):
            AddressRange(range_id=5, type=AddressRangeType.EVEN, min_hnr=1, max_hnr=20)

        # Parity mismatch: ODD with even bounds
        with self.assertRaises(UnsupportedAddressFormError):
            AddressRange(range_id=6, type=AddressRangeType.ODD, min_hnr=1, max_hnr=20)

        # Inverted bounds
        with self.assertRaises(UnsupportedAddressFormError):
            AddressRange(range_id=7, type=AddressRangeType.CONTINUOUS, min_hnr=100, max_hnr=50)

        # POINT without address_point
        with self.assertRaises(UnsupportedAddressFormError):
            AddressRange(range_id=8, type=AddressRangeType.POINT, address_point=None)

    def test_infer_range_type(self) -> None:
        self.assertEqual(infer_range_type(2, 20), AddressRangeType.EVEN)
        self.assertEqual(infer_range_type(1, 19), AddressRangeType.ODD)
        self.assertEqual(infer_range_type(1, 20), AddressRangeType.CONTINUOUS)

    def test_compile_address_layer_and_audit(self) -> None:
        raw_records = [
            {"named_object_id": 100, "min_hnr": 2, "max_hnr": 100},  # Even
            {"named_object_id": 100, "min_hnr": 1, "max_hnr": 99},   # Odd
            {"named_object_id": 200, "address_point": "Bloco B"},     # Point
            {"named_object_id": 300, "min_hnr": 50, "max_hnr": 20},  # Invalid: inverted
            {"named_object_id": 100, "min_hnr": 10, "max_hnr": 20, "side": "left"}, # Unsupported side flag
        ]

        doc = compile_address_layer(raw_records, update_region_id=439)
        self.assertEqual(doc["schema_version"], 1)
        self.assertEqual(doc["update_region_id"], 439)
        self.assertEqual(len(doc["ranges"]), 4)
        self.assertEqual(len(doc["bindings"]), 4)

        audit = doc["audit"]
        self.assertEqual(audit["total_inputs"], 5)
        self.assertEqual(audit["compiled_ranges"], 4)
        self.assertEqual(len(audit["unsupported_forms"]), 2)  # inverted + side flag audit

    def test_write_and_native_query_end_to_end(self) -> None:
        street_a = 1000
        street_b = 2000

        raw_records = [
            # Street A: Even side 2..20 (range 1), Odd side 1..19 (range 2)
            {"range_id": 1, "named_object_id": street_a, "type": 1, "min_hnr": 2, "max_hnr": 20},
            {"range_id": 2, "named_object_id": street_a, "type": 2, "min_hnr": 1, "max_hnr": 19},
            # Street B: Continuous 100..200 (range 3), Point 500-B (range 4)
            {"range_id": 3, "named_object_id": street_b, "type": 0, "min_hnr": 100, "max_hnr": 200},
            {"range_id": 4, "named_object_id": street_b, "type": 3, "address_point": "500-B"},
        ]

        doc = compile_address_layer(raw_records, update_region_id=439)
        stats = write_address_tables(self.fts_db, doc)
        self.assertEqual(stats["ranges_written"], 4)
        self.assertEqual(stats["bindings_written"], 4)

        # 1. Query Even number on Street A: 10 matches range 1, not range 2
        matches = query_house_number_native(self.fts_db, street_a, 10)
        self.assertEqual(matches, [1])

        # 2. Query Odd number on Street A: 11 matches range 2, not range 1
        matches = query_house_number_native(self.fts_db, street_a, 11)
        self.assertEqual(matches, [2])

        # 3. Query Out of Range number on Street A: 30 matches none
        matches = query_house_number_native(self.fts_db, street_a, 30)
        self.assertEqual(matches, [])

        # 4. Query number on wrong street: 10 on Street B matches none
        matches = query_house_number_native(self.fts_db, street_b, 10)
        self.assertEqual(matches, [])

        # 5. Query Continuous range on Street B: matches both 150 (even) and 151 (odd)
        self.assertEqual(query_house_number_native(self.fts_db, street_b, 150), [3])
        self.assertEqual(query_house_number_native(self.fts_db, street_b, 151), [3])

        # 6. Query Address Point on Street B: "500-B" matches range 4
        matches = query_house_number_native(self.fts_db, street_b, "500-B")
        self.assertEqual(matches, [4])

        # 7. Reverse query: range 1 -> Street A (1000)
        streets = query_named_objects_for_range(self.fts_db, 1)
        self.assertEqual(streets, [street_a])

        # 8. Reverse query: range 4 -> Street B (2000)
        streets = query_named_objects_for_range(self.fts_db, 4)
        self.assertEqual(streets, [street_b])

    def test_verify_address_release_readiness_blocks_unknown(self) -> None:
        doc = compile_address_layer([
            {"named_object_id": 1, "min_hnr": 10, "max_hnr": 20},
        ], update_region_id=439)

        readiness = verify_address_release_readiness(doc)
        self.assertFalse(readiness["release_allowed"])
        self.assertEqual(readiness["status"], "DEVELOPMENT_SUBSET_ONLY")
        self.assertIn("Unknown 4.4 blocker", readiness["reason"])


if __name__ == "__main__":
    unittest.main()
