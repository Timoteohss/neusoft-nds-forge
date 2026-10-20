"""Unit tests for street search and alias resolution (Issue #27 / [4.1]).

Validates:
1. Accents handling (e.g. 'São João' found with 'sao joao', 'São', 'joao').
2. Prefixes stripping and abbreviation expansion (e.g. 'Rua das Flores' found with 'Flores', 'R. das Flores').
3. Deduplication of multi-segment / multi-alias matches to stable result IDs.
4. Clean empty return on no-result queries.
5. Region scoping (results filtered by region_id).
6. Curitiba aliases do not count as generic coverage (profile-driven alias policies).
7. End-to-end lookup over both name-records.json and FTS.NDS SQLite databases.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from nds_forge.layers.package import _name_blob
from nds_forge.search import (
    SearchPolicy,
    SearchResult,
    search_streets,
    strip_accents,
)


class StreetSearchTests(unittest.TestCase):
    """Test suite for search_streets and SearchPolicy."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

        # Synthetic street records covering accents, prefixes, duplicates
        self.records = [
            {
                "id": 1001,
                "name": "Rua São João",
                "class": 11,
                "morton": 12345,
                "region_id": 439,
                "aliases": "Rua São João\x1cSao Joao\x1cSão João\x1cR. São João",
                "search_tags": "sao joao",
            },
            {
                "id": 1002,
                "name": "Avenida Sete de Setembro",
                "class": 11,
                "morton": 12346,
                "region_id": 439,
                "aliases": "Avenida Sete de Setembro\x1cSete de Setembro\x1cAv. Sete de Setembro",
                "search_tags": "sete de setembro",
            },
            {
                "id": 1003,
                "name": "Praça Tiradentes",
                "class": 11,
                "morton": 12347,
                "region_id": 439,
                "aliases": "Praça Tiradentes\x1cPraca Tiradentes\x1cTiradentes",
                "search_tags": "praca tiradentes",
            },
            # Street in different region (region 999)
            {
                "id": 2001,
                "name": "Rua São João",
                "class": 11,
                "morton": 99999,
                "region_id": 999,
                "aliases": "Rua São João\x1cSao Joao",
                "search_tags": "sao joao",
            },
        ]

        self.records_json_path = self.root / "name-records.json"
        self.records_json_path.write_text(json.dumps({"records": self.records}), encoding="utf-8")

        # Create SQLite FTS.NDS database
        self.fts_db_path = self.root / "FTS.NDS"
        self._init_sqlite_fts()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _init_sqlite_fts(self) -> None:
        with sqlite3.connect(str(self.fts_db_path)) as db:
            db.execute("CREATE TABLE namedObjectTable (namedObjectId INTEGER, stringCollId INTEGER, nameBlob BLOB)")
            db.execute("CREATE TABLE nameFtsTable (namedObjectId INTEGER, namedObjectClass INTEGER, mortonCode INTEGER, updateRegionId INTEGER, criterionA TEXT, criterionB TEXT, criterionC TEXT, criterionD TEXT, criterionI TEXT, criterionJ TEXT, searchTags TEXT)")

            for rec in self.records:
                db.execute(
                    "INSERT INTO namedObjectTable VALUES(?, 1, ?)",
                    (rec["id"], _name_blob(rec["name"])),
                )
                db.execute(
                    "INSERT INTO nameFtsTable VALUES(?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, NULL, ?)",
                    (rec["id"], rec["class"], rec["morton"], rec["region_id"], rec["aliases"], rec["search_tags"]),
                )

    def test_accents_fixture_handling(self) -> None:
        # 1. Query with unaccented text matches accented street
        results = search_streets("sao joao", self.records_json_path, region_id=439)
        self.assertGreater(len(results), 0)
        top = results[0]
        self.assertEqual(top.named_object_id, 1001)
        self.assertEqual(top.name, "Rua São João")

        # 2. Query with full accents matches exact
        results_acc = search_streets("São João", self.records_json_path, region_id=439)
        self.assertEqual(results_acc[0].named_object_id, 1001)

        # 3. Query with mixed accents
        results_mixed = search_streets("sAo JoAo", self.records_json_path, region_id=439)
        self.assertEqual(results_mixed[0].named_object_id, 1001)

    def test_prefixes_fixture_handling(self) -> None:
        # 1. Querying without prefix 'Rua' finds 'Rua São João'
        results = search_streets("São João", self.records_json_path, region_id=439)
        self.assertEqual(results[0].named_object_id, 1001)

        # 2. Querying with abbreviation 'R.' or 'r '
        results_abbr = search_streets("R. São João", self.records_json_path, region_id=439)
        self.assertEqual(results_abbr[0].named_object_id, 1001)

        # 3. Querying without prefix 'Avenida' finds 'Avenida Sete de Setembro'
        results_av = search_streets("Sete de Setembro", self.records_json_path, region_id=439)
        self.assertEqual(results_av[0].named_object_id, 1002)

        # 4. Querying with 'Av.'
        results_av_abbr = search_streets("Av. Sete de Setembro", self.records_json_path, region_id=439)
        self.assertEqual(results_av_abbr[0].named_object_id, 1002)

        # 5. Querying 'Praça' with 'Praca' or 'pç.'
        results_pr = search_streets("praca tiradentes", self.records_json_path, region_id=439)
        self.assertEqual(results_pr[0].named_object_id, 1003)

    def test_duplicates_fixture_and_stable_id_deduplication(self) -> None:
        # When querying 'joao', multiple variants match record 1001 (name, unaccented, alias)
        # Results must return a SINGLE deduplicated entry for ID 1001 in region 439
        results = search_streets("joao", self.records_json_path, region_id=439)
        matched_ids = [r.named_object_id for r in results]
        self.assertEqual(matched_ids, [1001])
        self.assertEqual(len(set(matched_ids)), len(matched_ids))

    def test_no_result_fixture_returns_cleanly(self) -> None:
        # Searching for non-existent street
        results = search_streets("Avenida dos Alienigenas Improvaveis", self.records_json_path)
        self.assertEqual(results, [])

        # Empty or whitespace query
        results_empty = search_streets("   ", self.records_json_path)
        self.assertEqual(results_empty, [])

    def test_region_scope_filtering(self) -> None:
        # Querying 'sao joao' in region 439 returns only ID 1001
        res_439 = search_streets("sao joao", self.records_json_path, region_id=439)
        self.assertEqual([r.named_object_id for r in res_439], [1001])

        # Querying 'sao joao' in region 999 returns only ID 2001
        res_999 = search_streets("sao joao", self.records_json_path, region_id=999)
        self.assertEqual([r.named_object_id for r in res_999], [2001])

        # Querying without region scope returns both
        res_all = search_streets("sao joao", self.records_json_path, region_id=None)
        self.assertEqual({r.named_object_id for r in res_all}, {1001, 2001})

    def test_curitiba_aliases_do_not_count_as_generic_coverage(self) -> None:
        # A generic policy does NOT assume Curitiba-specific abbreviations or substitutions
        generic_policy = SearchPolicy(locale="es-ES", common_prefixes=["Calle ", "Avenida "])
        self.assertNotIn("Rua ", generic_policy.common_prefixes)

        # Profile-driven policy loads explicitly declared prefixes
        custom_profile = {
            "names": {
                "locale": "pt-BR",
                "alias_policy": {
                    "prefixes": ["Rua ", "Avenida ", "Alameda ", "Travessa "],
                },
            },
        }
        profile_policy = SearchPolicy.from_profile(custom_profile)
        self.assertIn("Rua ", profile_policy.common_prefixes)
        self.assertEqual(profile_policy.locale, "pt-BR")

    def test_end_to_end_search_over_sqlite_fts_database(self) -> None:
        # Execute same search queries over the compiled SQLite FTS.NDS database
        results = search_streets("sao joao", self.fts_db_path, region_id=439)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].named_object_id, 1001)
        self.assertEqual(results[0].name, "Rua São João")

        # Prefix stripped query on SQLite
        results_prefix = search_streets("Tiradentes", self.fts_db_path, region_id=439)
        self.assertEqual(results_prefix[0].named_object_id, 1003)
        self.assertEqual(results_prefix[0].name, "Praça Tiradentes")

        # No result on SQLite
        results_none = search_streets("Rua Desconhecida", self.fts_db_path)
        self.assertEqual(results_none, [])


if __name__ == "__main__":
    unittest.main()
