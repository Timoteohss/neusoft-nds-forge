"""Street search and alias resolution engine.

Provides end-to-end street name lookup with locale-aware prefix stripping,
accent insensitivity, alias matching, region scoping, stable ID deduplication,
and deterministic ranking over canonical name records and FTS databases.
"""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def strip_accents(value: str) -> str:
    """Remove diacritical marks from string."""
    return "".join(
        c for c in unicodedata.normalize("NFD", value)
        if unicodedata.category(c) != "Mn"
    )


@dataclass
class SearchPolicy:
    """Configurable street search and alias matching policy."""
    locale: str = "pt-BR"
    strip_accents: bool = True
    common_prefixes: list[str] = field(default_factory=lambda: [
        "Rua ", "Avenida ", "Alameda ", "Travessa ", "Largo ", "Praça ",
        "Estrada ", "Rodovia ", "Beco ", "Viela ", "Passarela ",
    ])
    prefix_abbreviations: dict[str, str] = field(default_factory=lambda: {
        "r.": "rua",
        "r ": "rua ",
        "av.": "avenida",
        "av ": "avenida ",
        "al.": "alameda",
        "tv.": "travessa",
        "pç.": "praça",
        "praca": "praça",
        "rod.": "rodovia",
        "est.": "estrada",
    })
    min_query_length: int = 1

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> SearchPolicy:
        names_p = profile.get("names", profile)
        alias_p = names_p.get("alias_policy", {})
        locale = str(names_p.get("locale", "pt-BR"))
        prefixes = list(alias_p.get("prefixes", [])) if "prefixes" in alias_p else None
        policy = cls(locale=locale)
        if prefixes is not None:
            policy.common_prefixes = prefixes
        return policy


@dataclass(frozen=True)
class SearchResult:
    """Stable search candidate result for street lookup."""
    named_object_id: int
    name: str
    matched_term: str
    match_type: str  # "exact", "prefix_stripped", "alias", "unaccented", "partial"
    score: float
    region_id: int
    morton: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "named_object_id": self.named_object_id,
            "name": self.name,
            "matched_term": self.matched_term,
            "match_type": self.match_type,
            "score": round(self.score, 3),
            "region_id": self.region_id,
            "morton": self.morton,
        }


def _normalize_query_variants(query: str, policy: SearchPolicy) -> list[tuple[str, str, float]]:
    """Generate search variants (clean, prefix-stripped, unaccented, abbreviation-expanded).

    Returns list of (variant, match_type, base_score).
    """
    variants: list[tuple[str, str, float]] = []
    q_raw = query.strip()
    if not q_raw:
        return []

    # 1. Exact raw query (lowercased)
    q_lower = q_raw.lower()
    variants.append((q_lower, "exact", 100.0))

    # 2. Unaccented query
    if policy.strip_accents:
        q_unacc = strip_accents(q_lower)
        if q_unacc != q_lower:
            variants.append((q_unacc, "unaccented", 95.0))

    # 3. Abbreviation expansion (e.g. "r. flores" -> "rua flores")
    for abbr, full in policy.prefix_abbreviations.items():
        if q_lower.startswith(abbr):
            expanded = full + q_lower[len(abbr):]
            variants.append((expanded, "alias", 90.0))
            if policy.strip_accents:
                variants.append((strip_accents(expanded), "alias", 88.0))
            break

    # 4. Prefix stripping (e.g. "rua das flores" -> "das flores")
    for prefix in policy.common_prefixes:
        pref_lower = prefix.lower()
        if q_lower.startswith(pref_lower):
            stripped = q_lower[len(pref_lower):].strip()
            if stripped:
                variants.append((stripped, "prefix_stripped", 85.0))
                if policy.strip_accents:
                    variants.append((strip_accents(stripped), "prefix_stripped", 83.0))
            break

    return variants


def _match_record(
    record: dict[str, Any],
    query_variants: list[tuple[str, str, float]],
    policy: SearchPolicy,
    region_id: int | None = None,
) -> SearchResult | None:
    """Evaluate record against query variants, returning highest matching SearchResult or None."""
    rec_region = record.get("region_id", 0)
    if region_id is not None and rec_region != region_id:
        return None

    # Check class (streets are class 11 or >= 10, admin records are 0..10)
    _cls = int(record.get("class", record.get("namedObjectClass", 11)))
    # If admin record, still can match if needed, but streets are primary
    name = str(record.get("name", ""))
    aliases_raw = record.get("aliases", record.get("criterionI", ""))
    if isinstance(aliases_raw, list):
        aliases = [str(a) for a in aliases_raw]
    elif isinstance(aliases_raw, str):
        aliases = [a.strip() for a in aliases_raw.split("\x1c") if a.strip()]
    else:
        aliases = []

    _search_tags = str(record.get("search_tags", record.get("searchTags", ""))).lower()
    name_lower = name.lower()
    name_unacc = strip_accents(name_lower)

    # Collect candidate terms for this record
    terms: list[tuple[str, str]] = [(name_lower, "name")]
    if name_unacc != name_lower:
        terms.append((name_unacc, "name_unacc"))
    for a in aliases:
        a_lower = a.lower()
        terms.append((a_lower, "alias"))
        if policy.strip_accents:
            a_unacc = strip_accents(a_lower)
            if a_unacc != a_lower:
                terms.append((a_unacc, "alias_unacc"))

    # Also test stripped prefix on record name
    for prefix in policy.common_prefixes:
        pref_lower = prefix.lower()
        if name_lower.startswith(pref_lower):
            short = name_lower[len(pref_lower):].strip()
            terms.append((short, "prefix_stripped"))
            if policy.strip_accents:
                terms.append((strip_accents(short), "prefix_stripped"))
            break

    best_match: tuple[float, str, str] | None = None  # (score, match_type, term)

    for q_text, q_type, q_base_score in query_variants:
        for t_text, t_kind in terms:
            score = 0.0
            m_type = "partial"

            if t_text == q_text:
                score = q_base_score
                m_type = "exact" if t_kind == "name" and q_type == "exact" else q_type
            elif t_text.startswith(q_text):
                # Prefix match (e.g. query "flores" matches "florescer")
                score = q_base_score * 0.8
                m_type = "prefix"
            elif q_text in t_text:
                # Substring match
                score = q_base_score * 0.6
                m_type = "substring"

            if score > 0 and (best_match is None or score > best_match[0]):
                best_match = (score, m_type, t_text)

    if best_match is not None:
        score, mtype, term = best_match
        ident = int(record.get("id", record.get("namedObjectId", 0)))
        morton = int(record.get("morton", record.get("mortonCode", 0)))
        return SearchResult(
            named_object_id=ident,
            name=name,
            matched_term=term,
            match_type=mtype,
            score=score,
            region_id=rec_region,
            morton=morton,
        )

    return None


def search_streets_in_records(
    query: str,
    records: list[dict[str, Any]],
    region_id: int | None = None,
    policy: SearchPolicy | None = None,
    limit: int = 50,
) -> list[SearchResult]:
    """Search streets in-memory from canonical name-records.json records."""
    if policy is None:
        policy = SearchPolicy()

    query_variants = _normalize_query_variants(query, policy)
    if not query_variants:
        return []

    # Map named_object_id to best SearchResult
    best_by_id: dict[int, SearchResult] = {}

    for rec in records:
        res = _match_record(rec, query_variants, policy, region_id)
        if res is not None:  # noqa: SIM102 -- Keep nested guards to make validation branches explicit while preserving short-circuit order
            if res.named_object_id not in best_by_id or res.score > best_by_id[res.named_object_id].score:
                best_by_id[res.named_object_id] = res

    # Sort results by score DESC, then name ASC, then named_object_id ASC
    results = sorted(best_by_id.values(), key=lambda r: (-r.score, r.name, r.named_object_id))
    return results[:limit]


def search_streets_in_fts_db(
    query: str,
    db: sqlite3.Connection,
    region_id: int | None = None,
    policy: SearchPolicy | None = None,
    limit: int = 50,
) -> list[SearchResult]:
    """Search streets using SQLite FTS database and namedObjectTable."""
    if policy is None:
        policy = SearchPolicy()

    # Read records from namedObjectTable and nameFtsTable
    cur = db.execute("SELECT namedObjectId, namedObjectClass, mortonCode, updateRegionId, criterionI, searchTags FROM nameFtsTable")
    rows = cur.fetchall()

    # Read names from namedObjectTable
    names_cur = db.execute("SELECT namedObjectId, nameBlob FROM namedObjectTable")
    names_by_id: dict[int, str] = {}
    for nid, blob in names_cur.fetchall():
        if isinstance(blob, bytes) and len(blob) > 8:
            # Decode NDS name blob
            name_len = blob[7]
            names_by_id[nid] = blob[8:8 + name_len].decode("utf-8", errors="replace")
        else:
            names_by_id[nid] = f"Street {nid}"

    records: list[dict[str, Any]] = []
    for row in rows:
        nid = row[0]
        records.append({
            "namedObjectId": nid,
            "namedObjectClass": row[1],
            "mortonCode": row[2],
            "updateRegionId": row[3],
            "region_id": row[3],
            "name": names_by_id.get(nid, ""),
            "criterionI": row[4] or "",
            "searchTags": row[5] or "",
        })

    return search_streets_in_records(query, records, region_id, policy, limit)


def search_streets(
    query: str,
    target: Path | sqlite3.Connection | dict[str, Any] | list[dict[str, Any]],
    region_id: int | None = None,
    policy: SearchPolicy | None = None,
    limit: int = 50,
) -> list[SearchResult]:
    """Unified street search entry point.

    Accepts:
    - Path to FTS.NDS (SQLite) or name-records.json
    - sqlite3.Connection
    - name-records.json dictionary or records list
    """
    if isinstance(target, sqlite3.Connection):
        return search_streets_in_fts_db(query, target, region_id, policy, limit)

    if isinstance(target, (str, Path)):
        p = Path(target)
        if p.suffix == ".json":
            doc = json.loads(p.read_text(encoding="utf-8"))
            recs = doc.get("records", []) if isinstance(doc, dict) else doc
            return search_streets_in_records(query, recs, region_id, policy, limit)
        # SQLite database
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            return search_streets_in_fts_db(query, conn, region_id, policy, limit)
        finally:
            conn.close()

    if isinstance(target, dict):
        recs = target.get("records", [])
        return search_streets_in_records(query, recs, region_id, policy, limit)

    if isinstance(target, list):
        return search_streets_in_records(query, target, region_id, policy, limit)

    raise TypeError(f"Unsupported search target type: {type(target)}")
