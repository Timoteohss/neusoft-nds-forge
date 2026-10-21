"""Pinned native POI taxonomy evidence; never extrapolate category numbers.

The catalogue describes an observed seed taxonomy. It does not prove native
acceptance of generated POIs, OSM semantic completeness, or Brazilian coverage.
"""
from __future__ import annotations

from typing import Any

from ..config import canonical_hash

# Populated from the recorded source joins, not standalone POI compiler defaults.
NATIVE_CATEGORY_PROFILE: dict[str, Any] = {'evidence_document': 'docs/poi-category-evidence.json',
 'evidence_document_sha256': '524667acebcbc871fe5bf326a0c0ed71820d65cebe434d46e108bb87eb00e683',
 'id': 'neusoft-israel-poi-single-membership-v1',
 'limitations': ['Only observed single-membership native categories are enabled.',
                 'OSM tag-to-native label mapping is an explicit semantic policy, not an observed OSM join.',
                 ('No native acceptance, full POI coverage, routing access, or subtype inference is '
                 'established.')],
 'osm_mappings': {'amenity=atm': '\ue0001008 \ue0001024',
                  'amenity=bank': '\ue0001008 \ue0001030',
                  'amenity=cafe': '\ue0001001 \ue0001051',
                  'amenity=car_rental': '\ue0001008 \ue0001350',
                  'amenity=cinema': '\ue0001002 \ue0001303',
                  'amenity=fuel': '\ue0001008 \ue0001099',
                  'amenity=library': '\ue0001009 \ue0001355',
                  'amenity=parking': '\ue0001009 \ue0001097',
                  'amenity=pharmacy': '\ue0001007 \ue0001326',
                  'amenity=post_office': '\ue0001008 \ue0001341',
                  'amenity=restaurant': '\ue0001001 \ue0001112',
                  'shop=books': '\ue0001007 \ue0001329',
                  'shop=convenience': '\ue0001007 \ue0001054',
                  'shop=department_store': '\ue0001007 \ue0001059',
                  'shop=mall': '\ue0001007 \ue0001323',
                  'tourism=attraction': '\ue0001003 \ue0001072',
                  'tourism=hotel': '\ue0001005 \ue0001076',
                  'tourism=museum': '\ue0001003 \ue0001307'},
 'poi_table_category_ids': {'\ue0001001 \ue0001051': 1051,
                            '\ue0001001 \ue0001112': 1112,
                            '\ue0001002 \ue0001303': 1303,
                            '\ue0001003 \ue0001072': 1072,
                            '\ue0001003 \ue0001307': 1307,
                            '\ue0001005 \ue0001076': 1076,
                            '\ue0001007 \ue0001054': 1054,
                            '\ue0001007 \ue0001059': 1059,
                            '\ue0001007 \ue0001323': 1323,
                            '\ue0001007 \ue0001326': 1326,
                            '\ue0001007 \ue0001329': 1329,
                            '\ue0001008 \ue0001024': 1024,
                            '\ue0001008 \ue0001030': 1030,
                            '\ue0001008 \ue0001099': 1099,
                            '\ue0001008 \ue0001341': 1341,
                            '\ue0001008 \ue0001350': 1350,
                            '\ue0001009 \ue0001097': 1097,
                            '\ue0001009 \ue0001355': 1355},
 'source_hashes': {'db/NAV/UR439/FTS.NDS': 'e66028968c9ff043a0b41dca37aa07caf0f19a62ea022409fa444ef1447e41f9',
                   'db/NAV/UR439/POI.NDS': '638cf12d23c86925542dedc288608a772986152a1390bab12594fc1253ea24dc'}}


def validate_native_category_profile(profile: dict) -> dict | None:
    """Return evidence identity after checking an explicit profile and mappings."""
    identity = profile.get("native_category_profile")
    if identity is None:
        if "native_category_profile_sha256" in profile:
            raise ValueError("native_category_profile_sha256 requires native_category_profile")
        return None  # Synthetic/custom explicit mappings retain their prior contract.
    if identity != NATIVE_CATEGORY_PROFILE.get("id"):
        raise ValueError("Unknown native_category_profile")
    expected_hash = canonical_hash(NATIVE_CATEGORY_PROFILE)
    if profile.get("native_category_profile_sha256") != expected_hash:
        raise ValueError("Native category profile SHA-256 does not match the pinned catalogue")
    approved = NATIVE_CATEGORY_PROFILE["osm_mappings"]
    approved_ids = NATIVE_CATEGORY_PROFILE["poi_table_category_ids"]
    for tag, category in profile.get("osm_tag_categories", {}).items():
        if tag not in approved or approved[tag] != category:
            raise ValueError(f"OSM tag/category is not evidenced by the native category profile: {tag!r}")
    for category, category_id in profile.get("poi_table_category_ids", {}).items():
        if approved_ids.get(category) != category_id or type(category_id) is not int:
            raise ValueError("FTS category path/POI numeric category pair is not evidenced by the native category profile")
    return {"id": identity, "sha256": expected_hash,
            "evidence_document": NATIVE_CATEGORY_PROFILE["evidence_document"],
            "evidence_document_sha256": NATIVE_CATEGORY_PROFILE["evidence_document_sha256"],
            "limitations": NATIVE_CATEGORY_PROFILE["limitations"],
            "source_hashes": NATIVE_CATEGORY_PROFILE["source_hashes"]}


def validate_native_category_sources(profile: dict, records_by_name: dict) -> None:
    """Bind the selected taxonomy to the exact verified wrapped bootstrap seeds."""
    evidence = validate_native_category_profile(profile)
    if evidence is None:
        return
    for path, expected in evidence["source_hashes"].items():
        record = records_by_name.get(path)
        if not record or record.get("sha256") != expected:
            raise ValueError(f"Native POI category evidence does not match bootstrap source: {path}")
