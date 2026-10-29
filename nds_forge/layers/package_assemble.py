"""Assemble a package tree from explicit product/update-region identity.

The writer reuses only schema-bearing bootstrap databases and allowlisted
static assets. Output paths, catalogue IDs, update-region rows, time zone and
metadata are derived from the declared :class:`RegionBinding`; no UR directory
or national product ID is inferred from the bootstrap.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from ..bootstrap import verify_bootstrap
from ..region_binding import RegionBinding, resolve_region_binding
from ..registry import StepContext
from .address import write_address_tables
from .bmd_levels import validate_bmd_levels
from .container import unpack_sqlite
from .names import validate_admin_scope
from .poi_categories import validate_native_category_sources

_NAV_DB_NAMES = ("BMD.NDS", "NAME.NDS", "FTS.NDS", "POI.NDS", "ROUTE.NDS", "SHARED.NDS")


def _package_origin_role(path: str, original_role: str, binding: RegionBinding) -> str:
    # A database may be encrypted/generated as a container while its semantic
    # content is derived metadata. Storage format does not override lineage.
    if path in binding.derived_paths():
        return "derived"
    if path in binding.generated_zv_paths():
        return "generated"
    return original_role


def _table_columns(db: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1].lower() for row in db.execute(f'PRAGMA table_info("{table}")')}
    except sqlite3.DatabaseError:
        return set()


def _table_exists(db: sqlite3.Connection, table: str) -> bool:
    return bool(db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _values_for_columns(columns: set[str], values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if key.lower() in columns}


def _update_all_region_columns(db: sqlite3.Connection, old: int | None, new: int) -> None:
    tables = [row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    for table in tables:
        columns = _table_columns(db, table)
        if "updateregionid" not in columns:
            continue
        if old is None:
            db.execute(f'UPDATE "{table}" SET "updateRegionId"=? WHERE "updateRegionId"<>?',
                       (new, new))
        else:
            db.execute(f'UPDATE "{table}" SET "updateRegionId"=? WHERE "updateRegionId"=?',
                       (new, old))


def _apply_product_identity(db: sqlite3.Connection, binding: RegionBinding) -> None:
    """Rewrite catalogue rows to the declared product/set/region identity."""
    languages = [1]
    if _table_exists(db, "updateRegionNameTable"):
        columns = _table_columns(db, "updateRegionNameTable")
        if "languagecode" in columns:
            rows = db.execute(
                "SELECT languageCode FROM updateRegionNameTable WHERE updateRegionId=?",
                (binding.update_region_id,)).fetchall()
            languages = [row[0] for row in rows] or languages
    set_languages = languages
    if _table_exists(db, "updateRegionSetNameTable"):
        columns = _table_columns(db, "updateRegionSetNameTable")
        if "languagecode" in columns:
            rows = db.execute(
                "SELECT languageCode FROM updateRegionSetNameTable WHERE updateRegionSetId=?",
                (binding.update_region_set_id,)).fetchall()
            set_languages = [row[0] for row in rows] or set_languages
    version_id = 0
    if _table_exists(db, "updateRegionTable"):
        row = db.execute("SELECT versionId FROM updateRegionTable LIMIT 1").fetchone()
        if row:
            version_id = int(row[0])
    product_languages = languages
    if _table_exists(db, "productNameTable"):
        columns = _table_columns(db, "productNameTable")
        if "languagecode" in columns:
            rows = db.execute("SELECT languageCode FROM productNameTable").fetchall()
            product_languages = [row[0] for row in rows] or product_languages

    for table in ("updateRegionNameTable", "updateRegionSetNameTable",
                  "updateRegionSetTable", "updateRegionTable", "productNameTable"):
        if _table_exists(db, table):
            db.execute(f'DELETE FROM "{table}"')
    if _table_exists(db, "productNameTable"):
        columns = _table_columns(db, "productNameTable")
        for language in product_languages:
            db.execute(
                f'INSERT INTO productNameTable({",".join(sorted(columns))}) '
                f'VALUES({",".join("?" for _ in columns)})',
                [binding.product_name if c in {"productname", "namestring"} else language
                 for c in sorted(columns)])
    if _table_exists(db, "updateRegionTable"):
        columns = _table_columns(db, "updateRegionTable")
        values = {
            "updateRegionId": binding.update_region_id,
            "versionId": version_id,
            "versionName": binding.version_name,
            "date": "2026-01-01",
            "mapDataReleaseDate": "2026-01-01",
            "year": 2026,
            "releaseYear": 2026,
            "quarter": 1,
            "releaseMonth": 1,
            "isDirty": 0,
            "nameString": binding.region_name,
        }
        selected = _values_for_columns(columns, values)
        db.execute(
            f'INSERT INTO updateRegionTable({",".join(sorted(selected))}) '
            f'VALUES({",".join("?" for _ in selected)})',
            [selected[key] for key in sorted(selected)])
    if _table_exists(db, "updateRegionNameTable"):
        columns = _table_columns(db, "updateRegionNameTable")
        for language in languages:
            values = {"updateRegionId": binding.update_region_id,
                      "languageCode": language, "nameString": binding.region_name}
            selected = _values_for_columns(columns, values)
            db.execute(
                f'INSERT INTO updateRegionNameTable({",".join(sorted(selected))}) '
                f'VALUES({",".join("?" for _ in selected)})',
                [selected[key] for key in sorted(selected)])
    if _table_exists(db, "updateRegionSetTable"):
        columns = _table_columns(db, "updateRegionSetTable")
        values = {"updateRegionSetId": binding.update_region_set_id,
                  "updateRegionId": binding.update_region_id, "nameString": binding.set_name}
        selected = _values_for_columns(columns, values)
        db.execute(
            f'INSERT INTO updateRegionSetTable({",".join(sorted(selected))}) '
            f'VALUES({",".join("?" for _ in selected)})',
            [selected[key] for key in sorted(selected)])
    if _table_exists(db, "updateRegionSetNameTable"):
        columns = _table_columns(db, "updateRegionSetNameTable")
        for language in set_languages:
            values = {"updateRegionSetId": binding.update_region_set_id,
                      "languageCode": language, "nameString": binding.set_name}
            selected = _values_for_columns(columns, values)
            db.execute(
                f'INSERT INTO updateRegionSetNameTable({",".join(sorted(selected))}) '
                f'VALUES({",".join("?" for _ in selected)})',
                [selected[key] for key in sorted(selected)])
    if _table_exists(db, "urBuildingBlockVersionTable"):
        columns = _table_columns(db, "urBuildingBlockVersionTable")
        if "uri" in columns:
            rows = db.execute(
                "SELECT rowid, uri FROM urBuildingBlockVersionTable").fetchall()
            for rowid, uri in rows:
                remapped = binding.remap_path(str(uri))
                if remapped != uri:
                    db.execute("UPDATE urBuildingBlockVersionTable SET uri=? WHERE rowid=?",
                               (remapped, rowid))
    _update_all_region_columns(db, None, binding.update_region_id)
    if _table_exists(db, "versionTable"):
        columns = _table_columns(db, "versionTable")
        if "versionname" in columns:
            db.execute("UPDATE versionTable SET versionName=?", (binding.version_name,))


def _apply_timezone(db: sqlite3.Connection, binding: RegionBinding) -> None:
    if binding.timezone is None:
        return
    zone = binding.timezone
    if _table_exists(db, "timeZoneTable"):
        columns = _table_columns(db, "timeZoneTable")
        db.execute('DELETE FROM timeZoneTable')
        if len(columns) == 17:
            db.execute(
                "INSERT INTO timeZoneTable VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (zone.id, "1996-01-01", zone.utc_offset_quarters, 0, 1,
                 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0))
        else:
            values = {
                "timeZoneId": zone.id,
                "validFrom": "1996-01-01",
                "validFromDate": "1996-01-01",
                "utcOffset": zone.utc_offset_quarters,
                "hasDaylightSavingTime": 0,
                "dst": 0,
                "timeZoneNameId": 0,
                "timeZoneAbbreviationNameId": 1,
                "nameId": 1,
            }
            selected = _values_for_columns(columns, values)
            db.execute(
                f'INSERT INTO timeZoneTable({",".join(sorted(selected))}) '
                f'VALUES({",".join("?" for _ in selected)})',
                [selected[key] for key in sorted(selected)])
    if _table_exists(db, "timeZoneNameTable"):
        columns = _table_columns(db, "timeZoneNameTable")
        db.execute("DELETE FROM timeZoneNameTable")
        for index, text in enumerate((zone.name, zone.abbrev)):
            values = {"timeZoneNameId": index, "nameId": index,
                      "languageCode": 1,
                      "timeZoneNameString": text, "nameString": text}
            selected = _values_for_columns(columns, values)
            db.execute(
                f'INSERT INTO timeZoneNameTable({",".join(sorted(selected))}) '
                f'VALUES({",".join("?" for _ in selected)})',
                [selected[key] for key in sorted(selected)])
    if _table_exists(db, "timeZoneOlsonIdTable"):
        db.execute("DELETE FROM timeZoneOlsonIdTable")
        db.execute("INSERT INTO timeZoneOlsonIdTable VALUES(?,?)", (zone.id, zone.olson))
    if _table_exists(db, "regionTimeZoneTable"):
        db.execute("DELETE FROM regionTimeZoneTable")
        db.execute("INSERT INTO regionTimeZoneTable VALUES(?,?)", (1, zone.id))


def _apply_root_identity(db: sqlite3.Connection, binding: RegionBinding) -> None:
    if not _table_exists(db, "productDbTable"):
        return
    columns = _table_columns(db, "productDbTable")
    rows = {
        "NAV/PRODUCT.NDS": (binding.nav_product_id, binding.product_name),
        "DECO/PRODUCT.NDS": (binding.deco_product_id, None),
    }
    for uri, (product_id, name) in rows.items():
        values: dict[str, Any] = {"productId": product_id, "productGroupId": binding.product_group_id}
        if name is not None and "internalname" in columns:
            values["internalName"] = name
        assignments = ", ".join(f'"{key}"=?' for key in values)
        db.execute(f"UPDATE productDbTable SET {assignments} WHERE uri=?",
                   (*values.values(), uri))
    db.commit()


def _rewrite_metadata_identity(value: Any, binding: RegionBinding) -> None:
    if isinstance(value, dict):
        if isinstance(value.get("filePath"), str):
            value["filePath"] = binding.remap_path(value["filePath"])
        if "productId" in value and ("productName" in value or "updateRegions" in value):
            value["productGroupId"] = binding.product_group_id
            if "world" in str(value.get("productName", "")).lower():
                value["productId"] = binding.deco_product_id
            else:
                value["productId"] = binding.nav_product_id
                value["productName"] = binding.product_name
        if "updateRegionId" in value:
            value["updateRegionId"] = binding.update_region_id
            value["updateRegionSetId"] = binding.update_region_set_id
            value["urName"] = binding.region_name
            value["urSetName"] = binding.set_name
            value["versionName"] = binding.version_name
        for child in value.values():
            _rewrite_metadata_identity(child, binding)
    elif isinstance(value, list):
        for child in value:
            _rewrite_metadata_identity(child, binding)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _artifact_ref(step: str, root: Path, relative: str) -> dict[str, str]:
    path = root / relative
    if not path.is_file():
        raise FileNotFoundError(f"Required layer artifact is missing: {step}/{relative}")
    return {"artifact": step, "path": relative, "sha256": _sha(path)}


def _open_db(path: Path, writable: bool = True) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        raise FileNotFoundError(path)
    uri = f"file:{path}?mode=rw" if writable else f"file:{path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _quick_check(db: sqlite3.Connection, path: str) -> None:
    try:
        result = db.execute("PRAGMA quick_check").fetchall()
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f"SQLite check failed for {path}: {exc}") from exc
    if result != [("ok",)]:
        raise RuntimeError(f"SQLite quick_check failed for {path}: {result[:3]}")


def _copy_bootstrap_assets(bootstrap: Path, records: list[dict], db_root: Path,
                           binding: RegionBinding) -> tuple[dict[str, dict], dict[str, dict]]:
    by_name = {item["logical_name"]: item for item in records}
    origins: dict[str, dict] = {}
    for item in records:
        if item["role"] == "static_asset":
            relative = binding.remap_path(item["output_path"])
            source = (bootstrap / item["path"]).resolve()
            dest = (db_root / Path(relative).relative_to("db"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
            if _sha(dest) != item["sha256"]:
                raise ValueError(f"Copied bootstrap asset changed: {item['output_path']}")
            origins[relative] = {"origin": "copied_external_asset", "producer_step": "bootstrap.verify",
                                 "inputs": [{"artifact": "bootstrap.verify", "path": item["path"],
                                             "sha256": item["sha256"]}],
                                 "provenance": item["provenance"]}
    metadata = by_name.get("db/metadata.json")
    if metadata is None or metadata.get("role") != "reference_only":
        raise ValueError("Bootstrap must declare db/metadata.json as reference_only")
    source = (bootstrap / metadata["path"]).resolve()
    dest = db_root / "metadata.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    origins["db/metadata.json"] = {"origin": "derived", "producer_step": "package.assemble",
                                   "inputs": [{"artifact": "bootstrap.verify", "path": metadata["path"],
                                               "sha256": metadata["sha256"]}],
                                   "provenance": {"source": "bootstrap metadata reference; file hashes recomputed",
                                                  "license": metadata["provenance"]["license"]}}
    return by_name, origins


def _materialize_seed(context: StepContext, bootstrap: Path, records_by_name: dict,
                      output_db: Path, scratch: Path, keys: dict[str, bytes],
                      binding: RegionBinding) -> None:
    for name in _NAV_DB_NAMES:
        matches = [item for logical, item in records_by_name.items()
                   if Path(logical).name == name and item.get("role") == "schema_seed"]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one schema seed declaration for {name}")
        item = matches[0]
        output_path = binding.nav_path(name)
        source = bootstrap / item["path"]
        destination = output_db / Path(output_path).relative_to("db")
        destination.parent.mkdir(parents=True, exist_ok=True)
        plain = scratch / f"seed-{name}.sqlite"
        if item["format"] == "zv_zlib":
            key = keys.get(output_path)
            if key is None:
                raise ValueError(f"Missing runtime key for schema seed {output_path}")
            unpack_sqlite(source, plain, key)
        elif item["format"] == "sqlite3":
            shutil.copyfile(source, plain)
        else:
            raise ValueError(f"Unsupported schema seed format for {name}")
        shutil.copyfile(plain, destination)


def _name_blob(name: str, named_object_class: int = 11) -> bytes:
    """Encode a minimal NAME object with its actual class and one display name.

    The native NamedObject parser reads a big-endian u16 class first.
    Searchable streets use 11, rather than the former constant 16. Objects
    omit geometry and parent references; FTS supplies the result coordinate.
    """
    raw = name.encode("utf-8")
    if not raw or len(raw) > 255:
        raise ValueError(f"NDS name length outside supported 1..255 byte range: {name!r}")
    if not 0 <= named_object_class <= 65535:
        raise ValueError("NDS named-object class must fit an unsigned 16-bit integer")
    return (named_object_class.to_bytes(2, "big") + b"\x00\x00\x60\x80\x01"
            + bytes((len(raw),)) + raw + b"\x00")


def _write_bmd(db_path: Path, bmd_dir: Path) -> dict:
    manifest_path = bmd_dir / "bmd-manifest.json"
    manifest = _read_json(manifest_path)
    with sqlite3.connect(db_path) as db:
        db.execute("DELETE FROM bmdTileTable")
        db.execute("DELETE FROM bmdTilePatternTable")
        for tile in manifest["tiles"]:
            blob_path = bmd_dir / tile["path"]
            blob = blob_path.read_bytes()
            if len(blob) != tile["bytes"] or _sha(blob_path) != tile["sha256"]:
                raise ValueError(f"BMD tile hash mismatch: {tile['tile_id']}")
            db.execute("INSERT INTO bmdTileTable VALUES(?,1,1,?,NULL,NULL)",
                       (int(tile["tile_id"]), blob))
        db.commit()
        _quick_check(db, "BMD.NDS")
    return {"origin": "generated", "producer_step": "geometry.bmd",
            "inputs": [_artifact_ref("geometry.bmd", bmd_dir, "bmd-manifest.json")],
            "provenance": {"source": "OSM road geometry encoded as BMD tile blobs",
                           "license": "ODbL-1.0; © OpenStreetMap contributors"}}


def _poi_provenance(document: dict) -> dict:
    """Describe the actual compiler input, preserving its evidence and OSM lineage."""
    osm = any(row.get("origin") == "osm_compiled" for row in document.get("records", []))
    osm = osm or "osm_provenance" in document.get("source", {})
    return {
        "source": "OSM POIs selected by explicit native category mapping" if osm else "Curated POI profile input",
        "license": "ODbL-1.0; © OpenStreetMap contributors" if osm else "Curated profile input; underlying facts retain applicable rights",
        "compiler_source": document.get("source", {}),
    }


def _write_names(name_db: Path, fts_db: Path, names_dir: Path, poi_dir: Path,
                 config: dict[str, Any], region_id: int | None = None) -> tuple[dict, dict]:
    records_doc = _read_json(names_dir / "name-records.json")
    recs = records_doc.get("records")
    if not isinstance(recs, list):
        raise ValueError("name-records.json must contain records[]")  # noqa: TRY004 -- ValueError is the established validation exception; changing its class changes caller-visible behavior
    names_profile = config["names"]
    region_id = int(region_id if region_id is not None else names_profile["region_id"])
    admin = names_profile["admin_hierarchy"]
    scope = validate_admin_scope(names_profile)
    if scope == "municipality" and len(admin) != 4:
        raise ValueError("The v9 municipality profile requires four explicit admin records")
    if scope == "state" and any(int(item["class"]) in (9, 10) for item in recs):
        raise ValueError("State-only NAME records cannot contain a fixed municipality")
    with sqlite3.connect(name_db) as db:
        db.execute("DELETE FROM namedObjectTable")
        for item in recs:
            db.execute("INSERT INTO namedObjectTable VALUES(?,1,?)",
                       (int(item["id"]), _name_blob(item["name"], int(item["class"]))))
        db.commit()
        _quick_check(db, "NAME.NDS")

    poi_doc = _read_json(poi_dir / "poi-records.json")
    with sqlite3.connect(fts_db) as db:
        for table in ("nameFtsTable", "poiFtsTable", "namedObject2HouseNRTable",
                      "houseNR2NamedObjectTable", "ftsHouseNumberRangeTable",
                      "nameFtsSubstitutionTable", "poiFtsSubstitutionTable"):
            db.execute(f'DELETE FROM "{table}"')
        criteria = [names_profile[f"criterion_{letter}"] for letter in "abcd"]
        substitution_names = names_profile["admin_substitutions"]
        seen_substitutions: set[str] = set()
        for code, display in zip(criteria, substitution_names):
            db.execute("INSERT INTO nameFtsSubstitutionTable VALUES(?, ?, NULL, ?, NULL, 100)",
                       (code, display, region_id))
            seen_substitutions.add(str(code))
        dynamic_substitutions = records_doc.get("substitutions", [])
        if dynamic_substitutions:
            if not isinstance(dynamic_substitutions, list):
                raise ValueError("name-records.json substitutions must be a list")
            for substitution in dynamic_substitutions:
                if not isinstance(substitution, dict):
                    raise ValueError("Each name substitution must be an object")  # noqa: TRY004 -- name-record JSON validation uses ValueError
                code = str(substitution.get("code", ""))
                display = str(substitution.get("display", ""))
                if not code or not display or code in seen_substitutions:
                    continue
                db.execute(
                    "INSERT INTO nameFtsSubstitutionTable VALUES(?, ?, NULL, ?, NULL, ?)",
                    (code, display, region_id, int(substitution.get("frequency", 100))))
                seen_substitutions.add(code)
        for item in recs:
            cls = int(item["class"])
            item_criteria = [item.get(f"criterion_{letter}", criteria[index])
                             for index, letter in enumerate("abcd")]
            # nds_fts_findNextMatchResult selects (docId & 0xffffffff), not
            # namedObjectId. An implicit rowid points the SDK at unrelated or
            # absent NAME objects. rowid is the portable FTS4/FTS5 spelling.
            row: dict[str, Any] = {"rowid": int(item["id"]),
                   "namedObjectId": int(item["id"]), "namedObjectClass": cls,
                   "mortonCode": int(item["morton"]), "updateRegionId": region_id}
            if cls == 0:
                row["criterionA"] = item_criteria[0]
            elif cls == 2:
                row.update(criterionA=item_criteria[0], criterionB=item_criteria[1])
            elif cls == 9:
                row.update(criterionA=item_criteria[0], criterionB=item_criteria[1],
                           criterionC=item_criteria[2])
            elif cls == 10:
                row.update(criterionA=item_criteria[0], criterionB=item_criteria[1],
                           criterionC=item_criteria[2], criterionD=item_criteria[3])
            else:
                row.update(criterionA=item_criteria[0], criterionB=item_criteria[1],
                           criterionC=item_criteria[2],
                           criterionD=item.get("criterion_d", names_profile["criterion_d"]),
                           criterionI=str(item.get("aliases", item["name"])).replace("\x1c", " "),
                           criterionJ=item.get("criterion_j", names_profile["criterion_j"]),
                           searchTags=item.get("search_tags", ""))
            columns = list(row)
            db.execute(f"INSERT INTO nameFtsTable({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                       [row[column] for column in columns])
        for poi in poi_doc.get("records", []):
            category = str(poi["category"])
            aliases = poi["aliases"]
            if isinstance(aliases, list):
                aliases = "\x1c".join(str(alias) for alias in aliases)
            elif not isinstance(aliases, str):
                raise ValueError("POI aliases must be a string or list of strings")  # noqa: TRY004 -- ValueError is the established validation exception; changing its class changes caller-visible behavior
            # The legacy Curitiba profile stores a space before each U+001C
            # alternative separator in poiFtsTable.poiName.
            aliases = aliases.replace("\x1c", " \x1c")
            db.execute("""INSERT INTO poiFtsTable(
                rowid,poiId,mortonCode,poiName,poiCategoryId,regionId,updateRegionId,
                criterionA,criterionB,criterionC,criterionD,searchTags
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (int(poi["id"]), int(poi["id"]), int(poi["morton"]), aliases, category, 0, region_id,
                 *criteria[:3], names_profile["criterion_d"], poi["search_tags"]))
        db.execute("INSERT INTO nameFtsTable(nameFtsTable) VALUES('optimize')")
        db.execute("INSERT INTO poiFtsTable(poiFtsTable) VALUES('optimize')")
        db.commit()
        _quick_check(db, "FTS.NDS")
    shared_inputs = [_artifact_ref("names.compile", names_dir, "name-records.json")]
    names_origin = {"origin": "generated", "producer_step": "names.compile", "inputs": shared_inputs,
                    "provenance": {"source": "OSM street names and explicit profile hierarchy",
                                   "license": "ODbL-1.0; © OpenStreetMap contributors"}}
    fts_origin = {"origin": "generated", "producer_step": "names.compile", "inputs": [
        *shared_inputs, _artifact_ref("poi.compile", poi_dir, "poi-records.json")],
        "provenance": {"source": "OSM names plus explicitly selected POI records",
                       "license": "OSM-derived rows ODbL-1.0; curated rows retain their applicable rights",
                       "attribution": "© OpenStreetMap contributors",
                       "poi": _poi_provenance(poi_doc)}}
    return names_origin, fts_origin


def _write_address(fts_db: Path, address_dir: Path) -> dict[str, Any]:
    document = _read_json(address_dir / "address-doc.json")
    with sqlite3.connect(fts_db) as db:
        counts = write_address_tables(db, document)
        _quick_check(db, "FTS.NDS")
    return {**counts, "audit": document.get("audit", {})}


def _write_pois(db_path: Path, fts_db: Path, poi_dir: Path, catalog_dir: Path,
                profile: dict[str, Any]) -> dict:
    document = _read_json(poi_dir / "poi-records.json")
    category_ids = profile.get("poi_table_category_ids")
    if not isinstance(category_ids, dict):
        raise ValueError("POI profile requires explicit poi_table_category_ids")  # noqa: TRY004 -- ValueError is the established validation exception; changing its class changes caller-visible behavior
    tiles = _read_json(catalog_dir / "catalogs.json")["layer_coverage"]["poi"]["tile_ids"]
    with sqlite3.connect(db_path) as db:
        for table in ("poiTable", "poiVirtualTileTable", "poiToNameStringCollTable",
                      "poiNameStringCollTable", "poiLinkAccessTable", "poiRelationTable",
                      "poiNamedObjectRelationTable"):
            db.execute(f'DELETE FROM "{table}"')
        for row in document.get("records", []):
            category = str(row["category"])
            if category not in category_ids:
                raise ValueError(f"No POI table category ID for {category!r}")
            db.execute("INSERT INTO poiTable VALUES (?, ?, ?, 1, NULL, NULL, NULL, 16777216, NULL, NULL, NULL, NULL)",
                       (int(row["id"]), int(category_ids[category]), int(row["morton"])))
            db.execute("INSERT INTO poiToNameStringCollTable VALUES (?, ?)", (int(row["id"]), int(row["id"])))
            db.execute("INSERT INTO poiNameStringCollTable VALUES (?, 1, 6, 0, 1, ?, NULL)",
                       (int(row["id"]), row["name"]))
        for tile in tiles:
            db.execute("INSERT INTO poiVirtualTileTable VALUES (?, 1, ?, 1, 0)",
                       (int(tile), len(document.get("records", []))))
        db.commit()
        _quick_check(db, "POI.NDS")
    return {"origin": "generated", "producer_step": "poi.compile",
            "inputs": [_artifact_ref("poi.compile", poi_dir, "poi-records.json"),
                       _artifact_ref("catalogs.build", catalog_dir, "catalogs.json")],
            "provenance": _poi_provenance(document)}


def _write_route(route_db: Path, route_dir: Path, aux_dir: Path) -> dict:
    route = _read_json(route_dir / "route-manifest.json")
    aux = _read_json(aux_dir / "aux-manifest.json")
    aux_by_tile = {int(item["tile_id"]): item for item in aux["tiles"]}
    with sqlite3.connect(route_db) as db:
        db.execute("DELETE FROM routingTileTable")
        db.execute("DELETE FROM routingAuxTileTable")
        for tile in route["tiles"]:
            tile_id = int(tile["id"])
            route_path = route_dir / tile["route_blob"]
            aux_record = aux_by_tile.get(tile_id)
            if not aux_record:
                raise ValueError(f"Missing AUX tile row for ROUTE tile {tile_id}")
            aux_path = aux_dir / aux_record["path"]
            if _sha(route_path) != tile["route_sha256"] or _sha(aux_path) != aux_record["sha256"]:
                raise ValueError(f"ROUTE/AUX blob hash mismatch for tile {tile_id}")
            layer = aux_record.get("attribute_layer")
            layer_bytes = None
            if layer:
                layer_path = aux_dir / layer["path"]
                if not layer_path.is_file() or _sha(layer_path) != layer["sha256"]:
                    raise ValueError(f"ROUTE attribute layer hash mismatch for tile {tile_id}")
                layer_bytes = layer_path.read_bytes()
            db.execute("INSERT INTO routingTileTable VALUES(?,1,?)", (tile_id, route_path.read_bytes()))
            if layer_bytes is None:
                db.execute("INSERT INTO routingAuxTileTable VALUES(?,1,1,1,1,1,?,NULL,NULL,NULL,NULL)",
                           (tile_id, aux_path.read_bytes()))
            else:
                db.execute("INSERT INTO routingAuxTileTable VALUES(?,1,1,1,1,1,?,?,NULL,NULL,NULL)",
                           (tile_id, aux_path.read_bytes(), layer_bytes))
        db.commit()
        _quick_check(db, "ROUTE.NDS")
    return {"origin": "generated", "producer_step": "routing.main", "inputs": [
        _artifact_ref("routing.main", route_dir, "route-manifest.json"),
        _artifact_ref("routing.aux", aux_dir, "aux-manifest.json")],
        "provenance": {"source": "OSM route graph with profile-pinned sparse auxiliary policy",
                       "license": "ODbL-1.0; © OpenStreetMap contributors"}}


def _bmd_levels(bmd: dict) -> list[dict]:
    """Coverage levels: multi entry `levels[]` or legacy single level."""
    if "levels" in bmd:
        validate_bmd_levels(bmd["levels"])
        out = []
        for entry in bmd["levels"]:
            out.append({
                "level": int(entry["level"]),
                "shift": int(entry.get("shift", bmd.get("shift", 4))),
                "min_scale": entry.get("min_scale", 1),
                "max_scale": entry.get("max_scale", 50000000),
                "tile_ids": [int(t) for t in entry.get("tile_ids", [])],
            })
        return out
    return [{
        "level": int(bmd["level"]),
        "shift": int(bmd.get("shift", 4)),
        "min_scale": 1,
        "max_scale": 50000000,
        "tile_ids": [int(t) for t in bmd.get("tile_ids", [])],
    }]


def _write_shared(shared_db: Path, catalog: dict, profile: dict, *, catalog_dir: Path,
                  binding: RegionBinding | None = None) -> dict:
    catalog_input = _artifact_ref("catalogs.build", catalog_dir, "catalogs.json")
    bmd = catalog["layer_coverage"]["bmd"]
    route = catalog["layer_coverage"]["route"]
    iso = binding.iso_country_code if binding else "BRA"
    with sqlite3.connect(shared_db) as db:
        db.execute("DELETE FROM levelMetadataTable WHERE buildingBlockId IN (1,2)")
        for lv in _bmd_levels(bmd):
            db.execute("INSERT INTO levelMetadataTable VALUES(?,?,?,?,?,NULL)",
                       (lv["level"], 1, lv["shift"], lv["min_scale"], lv["max_scale"]))
        db.execute("INSERT INTO levelMetadataTable VALUES(?,?,?,1,50000000,NULL)",
                   (route["payload_level"], 2, route["payload_shift"]))
        db.execute("UPDATE regionMetadataTable SET isoCountryCode=?", (iso,))
        if binding is not None:
            _apply_timezone(db, binding)
        db.commit()
        _quick_check(db, "SHARED.NDS")
    return {"origin": "derived", "producer_step": "package.assemble", "inputs": [catalog_input],
        "provenance": {"source": "declared profile layer metadata",
                       "license": "schema seed rights remain unchanged"}}


def _write_product(product_db: Path, catalog: dict, profile: dict, *, catalog_dir: Path,
                   binding: RegionBinding | None = None) -> dict:
    catalog_input = _artifact_ref("catalogs.build", catalog_dir, "catalogs.json")
    region_id = int(binding.update_region_id if binding else profile["route"]["region_id"])
    bmd = catalog["layer_coverage"]["bmd"]
    route = catalog["layer_coverage"]["route"]
    poi = catalog["layer_coverage"]["poi"]
    with sqlite3.connect(product_db) as db:
        db.execute("DELETE FROM tileContentIndexTable WHERE buildingBlockId IN (1,2,4)")
        levels = _bmd_levels(bmd)
        for lv in levels:
            for sequence, tile in enumerate(lv["tile_ids"]):
                db.execute("INSERT INTO tileContentIndexTable VALUES(?,?,?,?,?,1,1,?)",
                           (region_id, 1, lv["level"], sequence, int(tile), b"\x80"))
        for sequence, tile in enumerate(sorted(poi["tile_ids"])):
            db.execute("INSERT INTO tileContentIndexTable VALUES(?,?,?,?,?,1,1,?)",
                       (region_id, 4, poi["level"], sequence, int(tile), b"\x80"))
        for sequence, tile in enumerate(route.get("index_tile_ids", [])):
            db.execute("INSERT INTO tileContentIndexTable VALUES(?,?,?,?,?,1,1,?)",
                       (region_id, 2, route["index_level"], sequence, int(tile), b"\x80"))
        label = binding.product_name if binding else profile.get("package_label", "Curitiba v9 OSM")
        if _table_exists(db, "updateRegionNameTable"):
            columns = _table_columns(db, "updateRegionNameTable")
            if "namestring" in columns:
                db.execute("UPDATE updateRegionNameTable SET nameString=? WHERE updateRegionId=?",
                           (label, region_id))
        if binding is not None:
            _apply_product_identity(db, binding)
        db.commit()
        _quick_check(db, "NAV/PRODUCT.NDS")
    return {"origin": "derived", "producer_step": "package.assemble", "inputs": [catalog_input],
        "provenance": {"source": "coverage catalog and declared product schema seed",
                       "license": "schema seed rights remain unchanged"}}


def _write_root(root_db: Path, profile: dict, *, seed_sha256: str,
                binding: RegionBinding | None = None) -> dict:
    label = binding.product_name if binding else profile.get("package_label", "Curitiba v9 OSM")
    with sqlite3.connect(root_db) as db:
        if _table_exists(db, "productDbTable"):
            columns = _table_columns(db, "productDbTable")
            if "internalname" in columns:
                db.execute("UPDATE productDbTable SET internalName=? WHERE uri='NAV/PRODUCT.NDS'",
                           (label,))
        if binding is not None:
            _apply_root_identity(db, binding)
        db.commit()
        _quick_check(db, "ROOT.NDS")
    return {"origin": "derived", "producer_step": "package.assemble", "inputs": [
        {"artifact": "bootstrap.verify", "path": "db/ROOT.NDS", "sha256": seed_sha256}],
        "provenance": {"source": "ROOT.NDS product binding", "license": "schema seed rights remain unchanged"}}


def _write_area(db_root: Path, binding: RegionBinding) -> None:
    path = db_root / "Area.txt"
    path.write_text(binding.area_code, encoding="utf-8")


def _refresh_metadata(db_root: Path, binding: RegionBinding | None = None) -> None:
    path = db_root / "metadata.json"
    document = _read_json(path)
    if binding is not None:
        _rewrite_metadata_identity(document, binding)
    listed: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("filePath"), str) and "fileHash" in value:
                candidate = (db_root / value["filePath"]).resolve()
                if db_root.resolve() not in candidate.parents or not candidate.is_file():
                    raise ValueError(f"metadata path is unsafe or missing: {value['filePath']}")
                value["fileHash"] = _sha(candidate)
                value["fileSize"] = candidate.stat().st_size
                listed.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(document)
    if "ndsDb" in document and listed:
        document["totalFileSize"] = sum(item["fileSize"] for item in listed)
        document["totalNumberOfFiles"] = len(listed)
    _write_json(path, document)


def _seed_record(records_by_name: dict[str, dict], filename: str) -> dict:
    matches = [item for logical, item in records_by_name.items()
               if Path(logical).name == filename and item.get("role") == "schema_seed"]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one schema seed declaration for {filename}")
    return matches[0]


def assemble_package(context: StepContext) -> dict[str, Any]:
    config = context.config.neusoft
    binding = getattr(context.config, "region_binding", None) or resolve_region_binding(config)
    # Reject contradictory administrative claims before copying bootstrap files
    # or resolving any external secret.
    validate_admin_scope(config["names"])
    bootstrap_profile = config.get("bootstrap_profile_id", binding.profile_id)
    if not isinstance(bootstrap_profile, str) or not bootstrap_profile:
        raise ValueError("neusoft.bootstrap_profile_id must be a non-empty string")
    profile = {**config, "profile_id": binding.profile_id}
    bootstrap = context.external_inputs["neusoft.bootstrap_dir"]
    bootstrap_report = _read_json(context.dependency_outputs["bootstrap.verify"] / "bootstrap-report.json")
    if bootstrap_report.get("profile_id") != bootstrap_profile:
        raise ValueError("Bootstrap report profile mismatch")
    verified = verify_bootstrap(bootstrap, bootstrap_profile)
    validate_native_category_sources(config["poi"],
                                     {record["logical_name"]: record for record in verified["files"]})
    records_by_name, origins = _copy_bootstrap_assets(
        bootstrap, verified["files"], context.output_dir / "db", binding)
    keys_ref = config.get("container_keys")
    if not isinstance(keys_ref, dict):
        raise ValueError("neusoft.container_keys must map each generated database path to a provider")  # noqa: TRY004 -- ValueError is the established validation exception; changing its class changes caller-visible behavior
    resolver = context.resolve_secret
    if resolver is None:
        raise RuntimeError("No runtime secret resolver available for schema seeds")
    generated = binding.generated_zv_paths()
    keys = {path: resolver(ref) for path, ref in keys_ref.items() if path in generated}
    if set(keys) != generated:
        raise ValueError("Assembly requires six explicit per-output keys")

    dirs = context.dependency_outputs
    bmd_dir, names_dir = dirs["geometry.bmd"], dirs["names.compile"]
    poi_dir, route_dir, aux_dir = dirs["poi.compile"], dirs["routing.main"], dirs["routing.aux"]
    catalog_dir = dirs["catalogs.build"]
    catalog = _read_json(catalog_dir / "catalogs.json")
    db_root = context.output_dir / "db"
    with tempfile.TemporaryDirectory(prefix="nds-forge-schema-") as scratch_name:
        scratch = Path(scratch_name)
        _materialize_seed(context, bootstrap, records_by_name, db_root, scratch, keys, binding)
        for seed in ("db/NAV/PRODUCT.NDS", "db/ROOT.NDS"):
            item = _seed_record(records_by_name, Path(seed).name)
            if item.get("format") != "sqlite3":
                raise ValueError(f"Expected plaintext SQLite schema seed {seed}")
            source = bootstrap / item["path"]
            dest = db_root / Path(seed).relative_to("db")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
        _write_bmd(db_root / Path(binding.nav_path("BMD.NDS")).relative_to("db"), bmd_dir)
        names_origin, fts_origin = _write_names(
            db_root / Path(binding.nav_path("NAME.NDS")).relative_to("db"),
            db_root / Path(binding.nav_path("FTS.NDS")).relative_to("db"),
            names_dir, poi_dir, config, binding.update_region_id)
        address_dir = dirs["address.compile"]
        address_counts = _write_address(
            db_root / Path(binding.nav_path("FTS.NDS")).relative_to("db"), address_dir)
        fts_origin["inputs"].append(
            _artifact_ref("address.compile", address_dir, "address-doc.json"))
        poi_origin = _write_pois(
            db_root / Path(binding.nav_path("POI.NDS")).relative_to("db"),
            db_root / Path(binding.nav_path("FTS.NDS")).relative_to("db"),
            poi_dir, catalog_dir, config["poi"])
        route_origin = _write_route(
            db_root / Path(binding.nav_path("ROUTE.NDS")).relative_to("db"), route_dir, aux_dir)
        shared_origin = _write_shared(
            db_root / Path(binding.nav_path("SHARED.NDS")).relative_to("db"), catalog, profile,
            catalog_dir=catalog_dir, binding=binding)
        product_origin = _write_product(db_root / "NAV/PRODUCT.NDS", catalog, profile,
                                        catalog_dir=catalog_dir, binding=binding)
        root_origin = _write_root(db_root / "ROOT.NDS", profile,
                                  seed_sha256=_seed_record(records_by_name, "ROOT.NDS")["sha256"],
                                  binding=binding)
    _write_area(db_root, binding)
    # Schema seeds are SQLite databases with preallocated/freelist pages. DELETE
    # clears their rows but does not shrink the file, so compact generated DBs
    # before hashing metadata and before ZV packing.
    for relative in sorted(generated):
        database = db_root / Path(relative).relative_to("db")
        with sqlite3.connect(database) as db:
            _quick_check(db, relative)
            db.execute("VACUUM")
            _quick_check(db, relative)
    origins.update({
        binding.nav_path("BMD.NDS"): _write_bmd_origin(bmd_dir),
        binding.nav_path("NAME.NDS"): names_origin,
        binding.nav_path("FTS.NDS"): fts_origin,
        binding.nav_path("POI.NDS"): poi_origin,
        binding.nav_path("ROUTE.NDS"): route_origin,
        binding.nav_path("SHARED.NDS"): shared_origin,
        "db/NAV/PRODUCT.NDS": product_origin,
        "db/ROOT.NDS": root_origin,
    })
    _refresh_metadata(db_root, binding)
    origins["db/metadata.json"] = {**origins["db/metadata.json"],
        "inputs": [{"artifact": "package.assemble", "path": "db/metadata.json",
                    "sha256": _sha(db_root / "metadata.json")} ]}
    origins["db/Area.txt"] = {"origin": "derived", "producer_step": "package.assemble",
        "inputs": [{"artifact": "package.assemble", "path": "db/Area.txt",
                    "sha256": _sha(db_root / "Area.txt")}],
        "provenance": {"source": "declared region binding area code",
                       "license": "no external asset"}}
    required_paths = binding.required_paths()
    required = sorted(required_paths)
    actual = {"db/" + path.relative_to(db_root).as_posix() for path in db_root.rglob("*") if path.is_file()}
    if actual != required_paths:
        raise ValueError(f"Assembled package path set mismatch: missing={sorted(required_paths-actual)}, extra={sorted(actual-required_paths)}")
    formats = binding.formats()
    files = []
    for path in required:
        origin = origins.get(path)
        if not origin:
            raise ValueError(f"No explicit origin/provenance record for {path}")
        origin = {**origin, "origin": _package_origin_role(path, origin["origin"], binding)}
        files.append({"path": path, **origin, "format": formats[path],
                      "validation_status": "unvalidated"})
    label = config.get("package_label", binding.product_name)
    layout = {"schema_version": 1, "profile_id": binding.profile_id,
              "region_binding": binding.to_dict(), "package_label": label,
              "required_paths": required, "pack_zv_sqlite_paths": sorted(generated), "files": files,
              "source_artifacts": {"bmd_manifest_sha256": _sha(bmd_dir / "bmd-manifest.json"),
                  "names_sha256": _sha(names_dir / "name-records.json"),
                  "address_sha256": _sha(address_dir / "address-doc.json"),
                  "poi_sha256": _sha(poi_dir / "poi-records.json"),
                  "route_manifest_sha256": _sha(route_dir / "route-manifest.json"),
                  "aux_manifest_sha256": _sha(aux_dir / "aux-manifest.json"),
                  "catalogs_sha256": _sha(catalog_dir / "catalogs.json")},
              "validation_status": "unvalidated",
              "limitations": [
                  "Schema seeds come from an explicitly declared bootstrap profile; their IDs are not inherited as output identity.",
                  "Output generation is not native/SDK/UI/device validation.",
                  "Inherited TMC/EXTTMC files are copied unchanged and do not establish Brazilian traffic coverage."]}
    _write_json(context.output_dir / "package-layout.json", layout)
    return {"file_count": len(files), "address_tables": address_counts, "origin_counts": {
                key: sum(1 for item in files if item["origin"] == key)
                for key in ("generated", "derived", "copied_external_asset")},
            "outputs": {path: {"bytes": (db_root / Path(path).relative_to("db")).stat().st_size,
                               "sha256": _sha(db_root / Path(path).relative_to("db"))} for path in required},
            "maturity": "experimental", "validation_status": "unvalidated",
            "capabilities_produced": ["declared_region_package_tree"],
            "capabilities_missing": ["container_encoding", "native_parser_validation",
                                     "SDK_calculation", "UI_validation", "vehicle_guidance"]}


def _write_bmd_origin(bmd_dir: Path) -> dict:
    return {"origin": "generated", "producer_step": "geometry.bmd",
            "inputs": [_artifact_ref("geometry.bmd", bmd_dir, "bmd-manifest.json")],
            "provenance": {"source": "OSM road geometry", "license": "ODbL-1.0; © OpenStreetMap contributors"}}
