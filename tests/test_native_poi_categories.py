"""Taxonomy/source binding checks, not a native parser or vehicle validation."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

from nds_forge.config import canonical_hash, load_config
from nds_forge.layers.poi_categories import (
    NATIVE_CATEGORY_PROFILE,
    validate_native_category_profile,
    validate_native_category_sources,
)
from nds_forge.layers.steps import validate_poi_profile


class NativePoiCategoryTests(unittest.TestCase):
    def profile(self):
        catalogue = NATIVE_CATEGORY_PROFILE
        return {
            "source": "osm", "native_category_profile": catalogue["id"],
            "native_category_profile_sha256": canonical_hash(catalogue),
            "osm_tag_categories": deepcopy(catalogue["osm_mappings"]),
            "poi_table_category_ids": deepcopy(catalogue["poi_table_category_ids"]),
        }

    def test_parana_configuration_matches_pinned_catalogue(self):
        config = load_config(Path(__file__).parents[1] / "examples/neusoft-parana-v9.toml")
        validate_poi_profile(config.neusoft["poi"])
        self.assertEqual(config.neusoft["poi"], {"region_id": 439, **self.profile()})
        evidence = validate_native_category_profile(config.neusoft["poi"])
        self.assertEqual(evidence["sha256"], canonical_hash(NATIVE_CATEGORY_PROFILE))

    def test_unknown_profile_hash_tag_and_pair_are_rejected(self):
        cases = []
        profile = self.profile()
        profile["native_category_profile"] = "unrecognized"
        cases.append((profile, "Unknown native_category_profile"))
        profile = self.profile()
        profile["native_category_profile_sha256"] = "0" * 64
        cases.append((profile, "SHA-256"))
        profile = self.profile()
        profile["osm_tag_categories"]["amenity=unverified"] = next(iter(profile["poi_table_category_ids"]))
        cases.append((profile, "not evidenced"))
        profile = self.profile()
        path = next(iter(profile["poi_table_category_ids"]))
        profile["poi_table_category_ids"][path] = 1006
        cases.append((profile, "not evidenced"))
        profile = self.profile()
        tag = next(iter(profile["osm_tag_categories"]))
        path = profile["osm_tag_categories"][tag]
        profile["osm_tag_categories"][tag] = path.split()[-1]
        profile["poi_table_category_ids"][path.split()[-1]] = profile["poi_table_category_ids"][path]
        cases.append((profile, "not evidenced"))
        for profile, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                validate_poi_profile(profile)

    def test_sources_must_match_verified_wrapped_seed_hashes(self):
        profile = self.profile()
        records = {path: {"sha256": sha} for path, sha in NATIVE_CATEGORY_PROFILE["source_hashes"].items()}
        validate_native_category_sources(profile, records)
        for path in list(records):
            changed = deepcopy(records)
            changed[path]["sha256"] = "0" * 64
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "bootstrap source"):
                validate_native_category_sources(profile, changed)
        with self.assertRaisesRegex(ValueError, "bootstrap source"):
            validate_native_category_sources(profile, {})

    def test_curated_curitiba_and_explicit_synthetic_mapping_still_work(self):
        config = load_config(Path(__file__).parents[1] / "examples/neusoft-curitiba-v9.toml")
        validate_poi_profile(config.neusoft["poi"])
        self.assertEqual(config.neusoft["poi"]["source"], "profile_curated")
        validate_poi_profile({"source": "osm", "osm_tag_categories": {"amenity=pharmacy": "fixture"},
                              "poi_table_category_ids": {"fixture": 999}})

    def test_catalogue_matches_recorded_evidence_and_eligible_subset(self):
        root = Path(__file__).parents[1]
        path = root / NATIVE_CATEGORY_PROFILE["evidence_document"]
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         NATIVE_CATEGORY_PROFILE["evidence_document_sha256"])
        evidence = json.loads(path.read_text())
        selected = [category for category in evidence["categories"]
                    if category["observed_memberships"] == [category["poi_table_category_id"]]]
        self.assertEqual(NATIVE_CATEGORY_PROFILE["osm_mappings"],
                         {tag: category["fts_category"] for category in selected for tag in category["osm_tags"]})
        self.assertEqual(NATIVE_CATEGORY_PROFILE["poi_table_category_ids"],
                         {category["fts_category"]: category["poi_table_category_id"] for category in selected})
        self.assertEqual(NATIVE_CATEGORY_PROFILE["source_hashes"],
                         {source["relative_path"]: source["wrapped_sha256"] for source in evidence["sources"]})

    def test_registered_assembly_rejects_wrong_seed_before_copy_or_secrets(self):
        from nds_forge import pipeline  # noqa: F401 -- registers the real package executor for this test
        from nds_forge.registry import StepContext, get_executor
        root = Path(__file__).parents[1]
        config = load_config(root / "examples/neusoft-parana-v9.toml")
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            output = directory / "must-not-exist"
            resolver = Mock(side_effect=AssertionError("must reject before resolving secrets"))
            context = StepContext(config, output, {"bootstrap.verify": directory},
                                  {"neusoft.bootstrap_dir": directory}, False, True, resolver)
            with (
                patch("nds_forge.layers.package_assemble._read_json", return_value={"profile_id": "neusoft-curitiba-v9"}),
                patch("nds_forge.layers.package_assemble.verify_bootstrap", return_value={"files": []}),
                patch("nds_forge.layers.package_assemble._copy_bootstrap_assets") as copier,
                self.assertRaisesRegex(ValueError, "bootstrap source"),
            ):
                get_executor("package.assemble")(context)
            copier.assert_not_called()
            resolver.assert_not_called()
            self.assertFalse(output.exists())
