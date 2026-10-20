"""Unit tests for TargetProfile, evidence binding, and curated POI validation.

Enforces:
1. Synthetic profiles accepted for tests, rejected for release.
2. Missing or guessed IDs rejected fail-closed.
3. Real values require authorized evidence binding.
4. Curated POIs are labeled curated and never represented as complete OSM POI coverage.
5. ISR/UR439 is rejected if asserted as an authentic Brazil national authority profile.
6. Schema conformance against schemas/profile.schema.json.
"""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from nds_forge.profile import (
    BuildingBlockSpec,
    CuratedPOISpec,
    EvidenceRecord,
    EvidenceSourceType,
    GridSpec,
    ProfileError,
    TargetProfile,
    validate_curated_poi_document,
)


class TargetProfileTests(unittest.TestCase):
    """Validation test suite for TargetProfile contracts."""

    def _make_valid_synthetic_profile(self) -> TargetProfile:
        return TargetProfile(
            profile_id="synthetic-test-v1",
            maturity="synthetic",
            country_id=999,
            region_id=888,
            product_id="SYNTHETIC_PRODUCT",
            ur_code="UR888",
            building_blocks={
                "bmd": BuildingBlockSpec(building_block_id=1, name="BMD"),
                "route": BuildingBlockSpec(building_block_id=2, name="ROUTE"),
            },
            grid=GridSpec(route_level=13, route_shift=4, bmd_level=11, bmd_shift=4),
            index_levels={"route": 9},
            key_providers={"db/NAV/UR888/BMD.NDS": "env:SYNTHETIC_KEY"},
            curated_poi_spec=CuratedPOISpec(
                source="test/curated-pois.json",
                is_curated=True,
                max_records=10,
                coverage_scope="curated_baseline",
            ),
            evidence_records=[
                EvidenceRecord(
                    field="country_id", value=999,
                    source_type=EvidenceSourceType.SYNTHETIC, reference="tests/synthetic",
                ),
                EvidenceRecord(
                    field="region_id", value=888,
                    source_type=EvidenceSourceType.SYNTHETIC, reference="tests/synthetic",
                ),
                EvidenceRecord(
                    field="product_id", value="SYNTHETIC_PRODUCT",
                    source_type=EvidenceSourceType.SYNTHETIC, reference="tests/synthetic",
                ),
                EvidenceRecord(
                    field="ur_code", value="UR888",
                    source_type=EvidenceSourceType.SYNTHETIC, reference="tests/synthetic",
                ),
            ],
            claim_brazil_authority=False,
            allow_guessed_ids=False,
        )

    def test_synthetic_profile_accepted_for_tests_rejected_for_release(self) -> None:
        profile = self._make_valid_synthetic_profile()

        # 1. Accepted for test / development mode
        profile.validate(for_release=False)

        # 2. Schema validates cleanly
        profile.validate_against_schema()

        # 3. Rejected for release
        with self.assertRaisesRegex(ProfileError, "Synthetic profile is accepted for tests but rejected as release"):
            profile.validate(for_release=True)

        # 4. Rejected if maturity is set to production with synthetic evidence
        profile.maturity = "production"
        with self.assertRaisesRegex(ProfileError, "Release profile cannot bind synthetic evidence"):
            profile.validate(for_release=False)

    def test_missing_opaque_identifiers_rejected(self) -> None:
        profile = self._make_valid_synthetic_profile()
        profile.product_id = ""
        with self.assertRaisesRegex(ProfileError, "Missing required opaque identifier: product_id"):
            profile.validate()

        profile = self._make_valid_synthetic_profile()
        profile.building_blocks = {}
        with self.assertRaisesRegex(ProfileError, "Profile must declare at least one building block"):
            profile.validate()

    def test_guessed_ids_rejected(self) -> None:
        profile = self._make_valid_synthetic_profile()
        profile.allow_guessed_ids = True
        with self.assertRaisesRegex(ProfileError, "Guessed IDs are strictly rejected"):
            profile.validate()

        profile = self._make_valid_synthetic_profile()
        profile.evidence_records.append(EvidenceRecord(
            field="traffic_id",
            value=123,
            source_type=EvidenceSourceType.SYNTHETIC,
            reference="guessed based on similar country",
        ))
        with self.assertRaisesRegex(ProfileError, "Guessed evidence reference rejected"):
            profile.validate()

    def test_missing_authorized_evidence_binding_rejected(self) -> None:
        profile = self._make_valid_synthetic_profile()
        # Remove country_id evidence
        profile.evidence_records = [
            ev for ev in profile.evidence_records if ev.field != "country_id"
        ]
        with self.assertRaisesRegex(ProfileError, "Missing authorized evidence binding for opaque fields:.*country_id"):
            profile.validate()

    def test_isr_ur439_cannot_claim_brazil_authority(self) -> None:
        profile = self._make_valid_synthetic_profile()
        profile.region_id = 439
        profile.ur_code = "UR439"
        profile.claim_brazil_authority = True  # Falsely claiming Israel UR439 as official Brazil authority
        with self.assertRaisesRegex(ProfileError, "Do not infer Brazil IDs; ISR/UR439 is an Israel-derived"):
            profile.validate()

    def test_curated_poi_constraints(self) -> None:
        profile = self._make_valid_synthetic_profile()
        profile.curated_poi_spec = CuratedPOISpec(
            source="pois.json",
            is_curated=False,  # Invalid: must be true
        )
        with self.assertRaisesRegex(ProfileError, "curated_poi_spec must have is_curated=True"):
            profile.validate()

        profile.curated_poi_spec = CuratedPOISpec(
            source="pois.json",
            is_curated=True,
            coverage_scope="complete_osm_poi_coverage",  # Invalid: cannot claim complete
        )
        with self.assertRaisesRegex(ProfileError, "Profile-curated POIs must be labeled curated and never represented"):
            profile.validate()

    def test_curated_poi_document_validator(self) -> None:
        # Valid 10-record curated document
        valid_doc = {
            "schema_version": 1,
            "scope": "ten curated baseline POIs; not a general OSM POI extraction",
            "records": [{"id": i, "name": f"POI {i}"} for i in range(1, 11)],
        }
        validate_curated_poi_document(valid_doc, max_records=10)

        # Rejects if scope claims complete
        complete_doc = {
            "scope": "complete OSM POI extraction for Brazil",
            "records": [{"id": 1, "name": "POI 1"}],
        }
        with self.assertRaisesRegex(ProfileError, "never be represented as complete OSM POI coverage"):
            validate_curated_poi_document(complete_doc, max_records=10)

        # Rejects if records exceed max
        overflow_doc = {
            "scope": "curated baseline",
            "records": [{"id": i, "name": f"POI {i}"} for i in range(1, 12)],
        }
        with self.assertRaisesRegex(ProfileError, "Curated POI count.*exceeds maximum allowed"):
            validate_curated_poi_document(overflow_doc, max_records=10)

    def test_raw_key_bytes_rejected_in_key_providers(self) -> None:
        profile = self._make_valid_synthetic_profile()
        profile.key_providers = {"db/NAV/UR888/BMD.NDS": "0x1122334455667788"}
        with self.assertRaisesRegex(ProfileError, "must be an indirect 'env:' secret reference"):
            profile.validate()

    def test_concrete_curitiba_v9_profile_file_validates(self) -> None:
        profile_file = Path(__file__).resolve().parent.parent / "profiles" / "neusoft-curitiba-v9.profile.json"
        self.assertTrue(profile_file.is_file(), f"Profile file not found at {profile_file}")
        profile = TargetProfile.from_json(profile_file.read_text(encoding="utf-8"))
        profile.validate(for_release=False)
        profile.validate_against_schema()
        self.assertEqual(profile.profile_id, "neusoft-curitiba-v9")
        self.assertEqual(profile.country_id, 972)
        self.assertEqual(profile.region_id, 439)
        self.assertFalse(profile.claim_brazil_authority)


if __name__ == "__main__":
    unittest.main()
