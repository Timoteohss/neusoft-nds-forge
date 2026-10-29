"""Address layer compiler and native query evaluator for NDS.Classic 2.4.4.

Implements verified house number and address range behavior (Issue #30 / [4.5]):
1. Schema contracts for `ftsHouseNumberRangeTable`, `namedObject2HouseNRTable`, and `houseNR2NamedObjectTable`.
2. Bitwise packing:
   - `namedObject2HouseNumberRangeId = (namedObjectId << 32) | rangeId`
   - `houseNumberRange2NamedObjectId = (rangeId << 32) | namedObjectId`
3. Verified types:
   - 0: Continuous / Mixed parity / Single number
   - 1: Even numbers only
   - 2: Odd numbers only
   - 3: Discrete address point (string identifier)
4. Native SQL query emulation matching `nds_fts_getHouseNumberNamedObjIds` in `libNdsLib.so`.
5. Audit and reporting for unsupported forms (unknown matrix 4.4 prevents release claims; UI acceptance via gate 6.5).
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any

from .names import strip_accents


class AddressRangeType(IntEnum):
    """NDS house number range type codes."""
    CONTINUOUS = 0
    EVEN = 1
    ODD = 2
    POINT = 3


class UnsupportedAddressFormError(ValueError):
    """Raised when an address form is invalid or unsupported by the verified schema."""


class AddressReleaseBlockedError(RuntimeError):
    """Raised when claiming release readiness without required routing link interpolation or UI gate 6.5."""


@dataclass(frozen=True)
class AddressRange:
    """Represents a verified house number range record."""
    range_id: int
    type: int
    min_hnr: int | None = None
    max_hnr: int | None = None
    language_code: int | None = None
    prefix: str | None = None
    suffix: str | None = None
    address_point: str | None = None

    def __post_init__(self) -> None:
        if self.min_hnr is None and self.address_point is None:
            raise UnsupportedAddressFormError(
                f"Range {self.range_id} must have at least min_hnr or address_point defined"
            )

        if self.type == AddressRangeType.POINT:
            if not self.address_point:
                raise UnsupportedAddressFormError(
                    f"Range {self.range_id} has type POINT (3) but address_point is empty"
                )
        else:
            if self.min_hnr is None or self.max_hnr is None:
                raise UnsupportedAddressFormError(
                    f"Range {self.range_id} of type {self.type} requires both min_hnr and max_hnr"
                )
            if self.min_hnr > self.max_hnr:
                raise UnsupportedAddressFormError(
                    f"Range {self.range_id} min_hnr ({self.min_hnr}) cannot exceed max_hnr ({self.max_hnr})"
                )
            if self.type == AddressRangeType.EVEN:
                if self.min_hnr % 2 != 0 or self.max_hnr % 2 != 0:
                    raise UnsupportedAddressFormError(
                        f"Range {self.range_id} has type EVEN (1) but min/max parity is odd"
                    )
            elif self.type == AddressRangeType.ODD:  # noqa: SIM102 -- Keep nested guards to make validation branches explicit while preserving short-circuit order
                if self.min_hnr % 2 == 0 or self.max_hnr % 2 == 0:
                    raise UnsupportedAddressFormError(
                        f"Range {self.range_id} has type ODD (2) but min/max parity is even"
                    )


@dataclass(frozen=True)
class AddressBinding:
    """Binds a street (namedObjectId) to a house number range."""
    named_object_id: int
    range_id: int
    update_region_id: int


def pack_named_object_to_range(named_object_id: int, range_id: int) -> int:
    """Bit-pack namedObjectId into hi 32-bits and rangeId into lo 32-bits."""
    return (int(named_object_id) << 32) | (int(range_id) & 0xFFFFFFFF)


def unpack_named_object_to_range(packed_id: int) -> tuple[int, int]:
    """Unpack (namedObjectId << 32) | rangeId."""
    return packed_id >> 32, packed_id & 0xFFFFFFFF


def pack_range_to_named_object(range_id: int, named_object_id: int) -> int:
    """Bit-pack rangeId into hi 32-bits and namedObjectId into lo 32-bits."""
    return (int(range_id) << 32) | (int(named_object_id) & 0xFFFFFFFF)


def unpack_range_to_named_object(packed_id: int) -> tuple[int, int]:
    """Unpack (rangeId << 32) | namedObjectId."""
    return packed_id >> 32, packed_id & 0xFFFFFFFF


def infer_range_type(min_hnr: int, max_hnr: int) -> AddressRangeType:
    """Infer the appropriate AddressRangeType from min and max house numbers."""
    if min_hnr % 2 == 0 and max_hnr % 2 == 0:
        return AddressRangeType.EVEN
    if min_hnr % 2 == 1 and max_hnr % 2 == 1:
        return AddressRangeType.ODD
    return AddressRangeType.CONTINUOUS


def compile_address_layer(
    records: list[dict[str, Any]],
    update_region_id: int,
) -> dict[str, Any]:
    """Compile raw address records into verified range structures and bindings.

    Audits supported forms vs unsupported forms.
    """
    ranges: list[AddressRange] = []
    bindings: list[AddressBinding] = []
    audit: dict[str, Any] = {
        "total_inputs": len(records),
        "compiled_ranges": 0,
        "compiled_bindings": 0,
        "unsupported_forms": [],
    }

    for idx, rec in enumerate(records):
        try:
            range_id = int(rec.get("range_id", idx + 1))
            named_obj_id = int(rec["named_object_id"])

            address_point = rec.get("address_point")
            min_hnr = rec.get("min_hnr")
            max_hnr = rec.get("max_hnr")

            # Check if this is an explicit address point or number range
            if address_point is not None:
                rtype: AddressRangeType = AddressRangeType.POINT
            elif min_hnr is not None and max_hnr is not None:
                record_type = rec.get("type")
                if record_type is None:
                    rtype = infer_range_type(int(min_hnr), int(max_hnr))
                else:
                    rtype = AddressRangeType(int(record_type))
            else:
                raise UnsupportedAddressFormError(
                    f"Record {idx} lacks both valid range bounds (min_hnr/max_hnr) and address_point"
                )

            # Check for unsupported side-of-street flags or uncompiled spatial interpolation
            if "side" in rec or "interpolation_curve" in rec:
                audit["unsupported_forms"].append({
                    "record_index": idx,
                    "reason": "Side-of-street and continuous metric interpolation require ROUTE link binding (nds_rt_getHouseNumbersOfLink) not compiled in FTS",
                })
                # We strip unsupported attributes to compile only the verified FTS subset

            rng = AddressRange(
                range_id=range_id,
                type=int(rtype),
                min_hnr=int(min_hnr) if min_hnr is not None else None,
                max_hnr=int(max_hnr) if max_hnr is not None else None,
                language_code=rec.get("language_code"),
                prefix=rec.get("prefix"),
                suffix=rec.get("suffix"),
                address_point=str(address_point) if address_point is not None else None,
            )
            ranges.append(rng)
            bindings.append(AddressBinding(
                named_object_id=named_obj_id,
                range_id=range_id,
                update_region_id=int(update_region_id),
            ))
            audit["compiled_ranges"] += 1
            audit["compiled_bindings"] += 1

        except Exception as exc:  # noqa: BLE001 -- Broad catch is intentional at this recovery/reporting boundary; preserve rollback/error aggregation behavior
            audit["unsupported_forms"].append({
                "record_index": idx,
                "reason": str(exc),
            })

    return {
        "schema_version": 1,
        "update_region_id": int(update_region_id),
        "ranges": [asdict(r) for r in ranges],
        "bindings": [asdict(b) for b in bindings],
        "audit": audit,
    }


_WHITESPACE = re.compile(r"\s+")

def _normalized_street_map(names: dict[str, int]) -> dict[str, int]:
    normalized: dict[str, int] = {}
    for name, ident in names.items():
        normalized.setdefault(strip_accents(name).casefold().strip(), ident)
    return normalized

def _representative_point(feature: dict[str, Any], collection: str) -> tuple[float, float] | None:
    """Return the address point as (lat, lon) from a retained node or way."""
    if collection == "nodes":
        lat, lon = feature.get("lat"), feature.get("lon")
        if lat is None or lon is None:
            return None
        return round(float(lat), 7), round(float(lon), 7)
    coordinates = feature.get("coordinates") or []
    if not coordinates:
        return None
    lat = sum(float(point[1]) for point in coordinates) / len(coordinates)
    lon = sum(float(point[0]) for point in coordinates) / len(coordinates)
    return round(lat, 7), round(lon, 7)

def compile_osm_addresses(dataset_path: Path, street_names_path: Path,
                          update_region_id: int) -> dict[str, Any]:
    """Compile OSM ``addr:housenumber`` objects into verified FTS house-number tables.

    Street identity is the BMD/NAME ``namedObjectId`` (the same value the FTS
    match returns), so the native house-number query resolves the street the
    user selected. Ranges are deduplicated globally on
    (type, min, max, language, prefix, suffix, point), mirroring the OEM shape
    where many streets share one range row. Consecutive numbers are not merged:
    the native ``minHnr``/``maxHnr`` test would then match numbers that do not
    exist on that street.
    """
    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    names = {str(name): int(ident)
             for name, ident in json.loads(Path(street_names_path).read_text(encoding="utf-8")).items()}
    normalized_names = _normalized_street_map(names)

    audit: dict[str, Any] = {
        "address_objects": 0,
        "resolved": 0,
        "missing_street": 0,
        "unknown_street": 0,
        "duplicate_bindings": 0,
        "numeric_ranges": 0,
        "point_ranges": 0,
    }
    addresses: set[tuple[int, str]] = set()
    points: dict[tuple[int, str], tuple[float, float]] = {}
    for collection in ("nodes", "ways"):
        for feature in dataset.get(collection, []):
            tags = feature.get("tags") or {}
            house_number = tags.get("addr:housenumber")
            if not house_number:
                continue
            audit["address_objects"] += 1
            street = tags.get("addr:street") or tags.get("addr:place")
            if not street:
                audit["missing_street"] += 1
                continue
            street_id = names.get(str(street)) or normalized_names.get(
                strip_accents(str(street)).casefold().strip())
            if street_id is None:
                audit["unknown_street"] += 1
                continue
            text = _WHITESPACE.sub(" ", str(house_number)).strip().upper()
            if not text:
                audit["missing_street"] += 1
                continue
            key = (street_id, text)
            if key in addresses:
                audit["duplicate_bindings"] += 1
                continue
            addresses.add(key)
            point = _representative_point(feature, collection)
            if point is not None:
                points[key] = point

    ranges_by_key: dict[tuple[int, int | None, int | None, str | None], int] = {}
    ranges: list[AddressRange] = []
    bindings: list[dict[str, Any]] = []
    for street_id, text in sorted(addresses):
        bounds: tuple[int | None, int | None, str | None]
        if text.isdigit():
            number = int(text)
            range_type = int(infer_range_type(number, number))
            bounds = (number, number, None)
            audit["numeric_ranges"] += 1
        else:
            range_type = int(AddressRangeType.POINT)
            bounds = (None, None, text)
            audit["point_ranges"] += 1
        range_key = (range_type, bounds[0], bounds[1], bounds[2])
        range_id = ranges_by_key.get(range_key)
        if range_id is None:
            range_id = len(ranges) + 1
            ranges_by_key[range_key] = range_id
            ranges.append(AddressRange(
                range_id=range_id, type=range_type,
                min_hnr=bounds[0], max_hnr=bounds[1], address_point=bounds[2]))
        binding: dict[str, Any] = {"named_object_id": street_id, "range_id": range_id,
                                   "update_region_id": int(update_region_id)}
        point = points.get((street_id, text))
        if point is not None:
            binding["lat"], binding["lon"] = point
        bindings.append(binding)
    audit["resolved"] = len(addresses)
    audit["ranges"] = len(ranges)
    audit["bindings"] = len(bindings)
    audit["with_coordinates"] = len(points)
    return {
        "schema_version": 1,
        "update_region_id": int(update_region_id),
        "ranges": [asdict(item) for item in ranges],
        "bindings": bindings,
        "audit": audit,
    }

def write_address_tables(fts_db: sqlite3.Connection, address_doc: dict[str, Any]) -> dict[str, int]:
    """Write compiled address ranges and bindings into FTS.NDS SQLite tables."""
    fts_db.execute("DELETE FROM ftsHouseNumberRangeTable")
    fts_db.execute("DELETE FROM namedObject2HouseNRTable")
    fts_db.execute("DELETE FROM houseNR2NamedObjectTable")

    ranges = address_doc.get("ranges", [])
    bindings = address_doc.get("bindings", [])

    for r in ranges:
        fts_db.execute(
            """INSERT INTO ftsHouseNumberRangeTable(
                rangeId, type, minHnr, maxHnr, languageCode, prefix, suffix, addressPoint
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                r["range_id"],
                r["type"],
                r.get("min_hnr"),
                r.get("max_hnr"),
                r.get("language_code"),
                r.get("prefix"),
                r.get("suffix"),
                r.get("address_point"),
            ),
        )

    for b in bindings:
        named_obj_id = b["named_object_id"]
        range_id = b["range_id"]
        reg_id = b["update_region_id"]

        no2hnr_id = pack_named_object_to_range(named_obj_id, range_id)
        hnr2no_id = pack_range_to_named_object(range_id, named_obj_id)

        fts_db.execute(
            "INSERT INTO namedObject2HouseNRTable(namedObject2HouseNumberRangeId, updateRegionId) VALUES(?, ?)",
            (no2hnr_id, reg_id),
        )
        fts_db.execute(
            "INSERT INTO houseNR2NamedObjectTable(houseNumberRange2NamedObjectId, updateRegionId) VALUES(?, ?)",
            (hnr2no_id, reg_id),
        )

    fts_db.commit()
    return {
        "ranges_written": len(ranges),
        "bindings_written": len(bindings),
    }


def query_house_number_native(
    fts_db: sqlite3.Connection,
    named_object_id: int,
    house_number: int | str,
    language_code: int | None = None,
    prefix: str | None = None,
    suffix: str | None = None,
) -> list[int]:
    """Execute the exact native query used by libNdsLib (nds_fts_getHouseNumberNamedObjIds).

    Matches house numbers against ftsHouseNumberRangeTable and namedObject2HouseNRTable.
    """
    if isinstance(house_number, int) or (isinstance(house_number, str) and house_number.isdigit()):
        num = int(house_number)
        parity_type = 1 if (num % 2 == 0) else 2
        address_point_val = str(house_number)
    else:
        num = -1  # Sentinel that will not match numeric range
        parity_type = -1
        address_point_val = str(house_number)

    no_id = int(named_object_id)
    lower_bound = no_id << 32
    upper_bound = (no_id + 1) << 32

    # Verbatim SQL extracted from libNdsLib.so
    sql = """
    SELECT hnr.rangeId
    FROM ftsHouseNumberRangeTable hnr
    WHERE ((hnr.minHnr <= ? AND hnr.maxHnr >= ? AND (hnr.type = ? OR hnr.type = 0)
     AND (hnr.prefix is ? AND hnr.suffix is ?))
     OR (hnr.type = 3 AND hnr.addressPoint = ?))
    AND (hnr.languageCode is ? OR hnr.languageCode IS NULL)
    AND hnr.rangeId IN (
        SELECT no2hnrrange.namedObject2HouseNumberRangeId & 4294967295
        FROM namedObject2HouseNRTable no2hnrrange
        WHERE ((no2hnrrange.namedObject2HouseNumberRangeId >= ?)
          AND (no2hnrrange.namedObject2HouseNumberRangeId < ?))
    )
    """
    cursor = fts_db.execute(
        sql,
        (num, num, parity_type, prefix, suffix, address_point_val, language_code, lower_bound, upper_bound),
    )
    return [row[0] for row in cursor.fetchall()]


def query_named_objects_for_range(fts_db: sqlite3.Connection, range_id: int) -> list[int]:
    """Execute the reverse native query from libNdsLib to find streets for a range."""
    rng_id = int(range_id)
    lower_bound = rng_id << 32
    upper_bound = (rng_id + 1) << 32

    sql = """
    SELECT hnr2no.houseNumberRange2NamedObjectId & 4294967295
    FROM houseNR2NamedObjectTable hnr2no
    WHERE hnr2no.houseNumberRange2NamedObjectId >= ?
      AND hnr2no.houseNumberRange2NamedObjectId < ?
    """
    cursor = fts_db.execute(sql, (lower_bound, upper_bound))
    return [row[0] for row in cursor.fetchall()]


def verify_address_release_readiness(address_doc: dict[str, Any]) -> dict[str, Any]:
    """Check whether address compilation can be promoted to production release.

    Per contract unknown 4.4, link-level routing interpolation (nds_rt_getHouseNumbersOfLink)
    and UI search/preview gate 6.5 are required before full production address claims.
    """
    audit = address_doc.get("audit", {})
    unsupported = audit.get("unsupported_forms", [])

    return {
        "release_allowed": False,
        "reason": "Unknown 4.4 blocker: spatial ROUTE link interpolation (nds_rt_getHouseNumbersOfLink) is not compiled; UI acceptance requires gate 6.5.",
        "compiled_ranges": audit.get("compiled_ranges", 0),
        "unsupported_forms_count": len(unsupported),
        "status": "DEVELOPMENT_SUBSET_ONLY",
    }

def write_house_index(address_doc: dict[str, Any], street_names_path: Path,
                      output_path: Path) -> dict[str, int]:
    """Write the runtime lookup index used to resolve a typed street + number.

    One line per street: ``normalized_name<TAB>display_name<TAB>entry;entry``
    where each entry is ``number:lat,lon``. Kept line-oriented so a device-side
    resolver can load it as bytes and parse only the matching street.
    """
    names = {int(ident): str(name) for name, ident in
             json.loads(Path(street_names_path).read_text(encoding="utf-8")).items()}
    ranges = {int(item["range_id"]): item for item in address_doc.get("ranges", [])}
    per_street: dict[int, list[str]] = {}
    for binding in address_doc.get("bindings", []):
        if "lat" not in binding or "lon" not in binding:
            continue
        item = ranges.get(int(binding["range_id"]))
        if item is None:
            continue
        number = item["address_point"] if item["address_point"] is not None else str(item["min_hnr"])
        per_street.setdefault(int(binding["named_object_id"]), []).append(
            f"{number}:{binding['lat']:.7f},{binding['lon']:.7f}")
    lines = []
    for named_object_id, entries in per_street.items():
        display = names.get(named_object_id)
        if not display:
            continue
        key = strip_accents(display).casefold().strip()
        lines.append((key, display, ";".join(sorted(entries))))
    lines.sort()
    Path(output_path).write_text(
        "".join(f"{key}\t{display}\t{entries}\n" for key, display, entries in lines),
        encoding="utf-8")
    return {"streets": len(lines), "entries": sum(len(e.split(";")) for _, _, e in lines),
            "bytes": Path(output_path).stat().st_size}
