"""Assemble the explicit 21-path Curitiba v9 package from layer artifacts.

The writer reuses only schema-bearing bootstrap databases and allowlisted
static assets. It never reads a previous Curitiba package as an input.
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
from ..registry import StepContext
from .container import unpack_sqlite
from .names import validate_admin_scope
from .poi_categories import validate_native_category_sources

PROFILE = "neusoft-curitiba-v9"
REGION_ID = 439
GENERATED_ZV = {
    "db/NAV/UR439/BMD.NDS", "db/NAV/UR439/NAME.NDS", "db/NAV/UR439/FTS.NDS",
    "db/NAV/UR439/POI.NDS", "db/NAV/UR439/ROUTE.NDS", "db/NAV/UR439/SHARED.NDS",
}
REQUIRED_PATHS = {
    "db/Area.txt", "db/DECO/PRODUCT.NDS", "db/DECO/UR0/BMD.NDS",
    "db/DECO/UR0/NAME.NDS", "db/DECO/UR0/SHARED.NDS", "db/NAV/PRODUCT.NDS",
    "db/NAV/UR439/3D.NDS", "db/NAV/UR439/BMD.NDS", "db/NAV/UR439/EXTBMD.NDS",
    "db/NAV/UR439/EXTTMC.NDS", "db/NAV/UR439/FTS.NDS", "db/NAV/UR439/JV.NDS",
    "db/NAV/UR439/NAME.NDS", "db/NAV/UR439/POI.NDS", "db/NAV/UR439/ROUTE.NDS",
    "db/NAV/UR439/SHARED.NDS", "db/NAV/UR439/SPEECH.NDS", "db/NAV/UR439/TMC.NDS",
    "db/ROOT.NDK", "db/ROOT.NDS", "db/metadata.json",
}
FORMATS = {
    **{path: "zv_zlib" for path in GENERATED_ZV},
    **{path: "zv_zlib" for path in {
        "db/DECO/UR0/BMD.NDS", "db/DECO/UR0/NAME.NDS", "db/DECO/UR0/SHARED.NDS",
        "db/NAV/UR439/3D.NDS", "db/NAV/UR439/EXTBMD.NDS", "db/NAV/UR439/EXTTMC.NDS",
        "db/NAV/UR439/JV.NDS", "db/NAV/UR439/SPEECH.NDS", "db/NAV/UR439/TMC.NDS",
    }},
    "db/DECO/PRODUCT.NDS": "sqlite3", "db/NAV/PRODUCT.NDS": "sqlite3",
    "db/ROOT.NDS": "sqlite3", "db/Area.txt": "text", "db/ROOT.NDK": "ndk_text",
    "db/metadata.json": "json",
}
SEED_TO_OUTPUT = {
    "db/NAV/UR439/BMD.NDS": "db/NAV/UR439/BMD.NDS",
    "db/NAV/UR439/NAME.NDS": "db/NAV/UR439/NAME.NDS",
    "db/NAV/UR439/FTS.NDS": "db/NAV/UR439/FTS.NDS",
    "db/NAV/UR439/POI.NDS": "db/NAV/UR439/POI.NDS",
    "db/NAV/UR439/ROUTE.NDS": "db/NAV/UR439/ROUTE.NDS",
    "db/NAV/UR439/SHARED.NDS": "db/NAV/UR439/SHARED.NDS",
}


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


def _copy_bootstrap_assets(bootstrap: Path, records: list[dict], db_root: Path) -> tuple[dict[str, dict], list[dict]]:
    by_name = {item["logical_name"]: item for item in records}
    origins: dict[str, dict] = {}
    for item in records:
        if item["role"] == "static_asset":
            relative = item["output_path"]
            source = (bootstrap / item["path"]).resolve()
            dest = (db_root / Path(relative).relative_to("db"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
            if _sha(dest) != item["sha256"]:
                raise ValueError(f"Copied bootstrap asset changed: {relative}")
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
                      output_db: Path, scratch: Path, keys: dict[str, bytes]) -> None:
    for seed_path, output_path in SEED_TO_OUTPUT.items():
        item = records_by_name.get(seed_path)
        if not item or item.get("role") != "schema_seed":
            raise ValueError(f"Missing schema seed declaration for {seed_path}")
        source = bootstrap / item["path"]
        destination = output_db / Path(output_path).relative_to("db")
        destination.parent.mkdir(parents=True, exist_ok=True)
        plain = scratch / f"seed-{Path(seed_path).stem}.sqlite"
        if item["format"] == "zv_zlib":
            key = keys.get(output_path)
            if key is None:
                raise ValueError(f"Missing runtime key for schema seed {output_path}")
            unpack_sqlite(source, plain, key)
        elif item["format"] == "sqlite3":
            shutil.copyfile(source, plain)
        else:
            raise ValueError(f"Unsupported schema seed format for {seed_path}")
        shutil.copyfile(plain, destination)


def _name_blob(name: str) -> bytes:
    raw = name.encode("utf-8")
    if not raw or len(raw) > 255:
        raise ValueError(f"NDS name length outside supported 1..255 byte range: {name!r}")
    return b"\x00\x10\x00\x00\x60\x80\x01" + bytes((len(raw),)) + raw + b"\x00"


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
                 config: dict[str, Any]) -> tuple[dict, dict]:
    records_doc = _read_json(names_dir / "name-records.json")
    recs = records_doc.get("records")
    if not isinstance(recs, list):
        raise ValueError("name-records.json must contain records[]")
    names_profile = config["names"]
    region_id = int(names_profile["region_id"])
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
                       (int(item["id"]), _name_blob(item["name"])))
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
        for code, display in zip(criteria, substitution_names):
            db.execute("INSERT INTO nameFtsSubstitutionTable VALUES(?, ?, NULL, ?, NULL, 100)",
                       (code, display, region_id))
        for item in recs:
            cls = int(item["class"])
            row = {"namedObjectId": int(item["id"]), "namedObjectClass": cls,
                   "mortonCode": int(item["morton"]), "updateRegionId": region_id}
            if cls == 0:
                row["criterionA"] = criteria[0]
            elif cls == 2:
                row.update(criterionA=criteria[0], criterionB=criteria[1])
            elif cls == 9:
                row.update(criterionA=criteria[0], criterionB=criteria[1], criterionC=criteria[2])
            elif cls == 10:
                row.update(criterionA=criteria[0], criterionB=criteria[1], criterionC=criteria[2],
                           criterionD=criteria[3])
            else:
                row.update(criterionA=criteria[0], criterionB=criteria[1], criterionC=criteria[2],
                           criterionD=names_profile["criterion_d"],
                           criterionI=str(item.get("aliases", item["name"])).replace("\x1c", " "),
                           criterionJ=names_profile["criterion_j"], searchTags=item.get("search_tags", ""))
            columns = list(row)
            db.execute(f"INSERT INTO nameFtsTable({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                       [row[column] for column in columns])
        for poi in poi_doc.get("records", []):
            category = str(poi["category"])
            aliases = poi["aliases"]
            if isinstance(aliases, list):
                aliases = "\x1c".join(str(alias) for alias in aliases)
            elif not isinstance(aliases, str):
                raise ValueError("POI aliases must be a string or list of strings")
            # The legacy Curitiba profile stores a space before each U+001C
            # alternative separator in poiFtsTable.poiName.
            aliases = aliases.replace("\x1c", " \x1c")
            db.execute("""INSERT INTO poiFtsTable(
                poiId,mortonCode,poiName,poiCategoryId,regionId,updateRegionId,
                criterionA,criterionB,criterionC,criterionD,searchTags
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (int(poi["id"]), int(poi["morton"]), aliases, category, 0, region_id,
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
                       "poi": _poi_provenance(poi_doc)}}
    return names_origin, fts_origin


def _write_pois(db_path: Path, fts_db: Path, poi_dir: Path, bmd_dir: Path,
                profile: dict[str, Any]) -> dict:
    document = _read_json(poi_dir / "poi-records.json")
    category_ids = profile.get("poi_table_category_ids")
    if not isinstance(category_ids, dict):
        raise ValueError("POI profile requires explicit poi_table_category_ids")
    tiles = _read_json(bmd_dir / "bmd-manifest.json")["tiles"]
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
                       (int(tile["tile_id"]), len(document.get("records", []))))
        db.commit()
        _quick_check(db, "POI.NDS")
    return {"origin": "generated", "producer_step": "poi.compile",
            "inputs": [_artifact_ref("poi.compile", poi_dir, "poi-records.json")],
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
            db.execute("INSERT INTO routingTileTable VALUES(?,1,?)", (tile_id, route_path.read_bytes()))
            db.execute("INSERT INTO routingAuxTileTable VALUES(?,1,1,1,1,1,?,NULL,NULL,NULL,NULL)",
                       (tile_id, aux_path.read_bytes()))
        db.commit()
        _quick_check(db, "ROUTE.NDS")
    return {"origin": "generated", "producer_step": "routing.main", "inputs": [
        _artifact_ref("routing.main", route_dir, "route-manifest.json"),
        _artifact_ref("routing.aux", aux_dir, "aux-manifest.json")],
        "provenance": {"source": "OSM route graph with profile-pinned sparse auxiliary policy",
                       "license": "ODbL-1.0; © OpenStreetMap contributors"}}


def _write_shared(shared_db: Path, catalog: dict, profile: dict, *, catalog_dir: Path) -> dict:
    catalog_input = _artifact_ref("catalogs.build", catalog_dir, "catalogs.json")
    bmd = catalog["layer_coverage"]["bmd"]
    route = catalog["layer_coverage"]["route"]
    with sqlite3.connect(shared_db) as db:
        db.execute("DELETE FROM levelMetadataTable WHERE buildingBlockId IN (1,2)")
        db.execute("INSERT INTO levelMetadataTable VALUES(?,?,?,1,50000000,NULL)",
                   (bmd["level"], 1, bmd["shift"]))
        db.execute("INSERT INTO levelMetadataTable VALUES(?,?,?,1,50000000,NULL)",
                   (route["payload_level"], 2, route["payload_shift"]))
        db.execute("UPDATE regionMetadataTable SET isoCountryCode='BRA'")
        db.commit()
        _quick_check(db, "SHARED.NDS")
    return {"origin": "derived", "producer_step": "package.assemble", "inputs": [catalog_input],
        "provenance": {"source": "Curitiba v9 profile layer metadata", "license": "schema seed rights remain unchanged"}}


def _write_product(product_db: Path, catalog: dict, profile: dict, *, catalog_dir: Path) -> dict:
    catalog_input = _artifact_ref("catalogs.build", catalog_dir, "catalogs.json")
    region_id = int(profile["route"]["region_id"])
    bmd = catalog["layer_coverage"]["bmd"]
    route = catalog["layer_coverage"]["route"]
    with sqlite3.connect(product_db) as db:
        db.execute("DELETE FROM tileContentIndexTable WHERE buildingBlockId IN (1,2,4)")
        for building_block_id, level, tiles in (
                (1, bmd["level"], bmd["tile_ids"]),
                (4, bmd["level"], bmd["tile_ids"]),
                (2, route["index_level"], route["index_tile_ids"])):
            for sequence, tile in enumerate(tiles):
                db.execute("INSERT INTO tileContentIndexTable VALUES(?,?,?,?,?,1,1,?)",
                           (region_id, building_block_id, level, sequence, int(tile), b"\x80"))
        label = profile.get("package_label", "Curitiba v9 OSM")
        db.execute("UPDATE updateRegionNameTable SET nameString=? WHERE updateRegionId=?",
                   (label, region_id))
        db.commit()
        _quick_check(db, "NAV/PRODUCT.NDS")
    return {"origin": "derived", "producer_step": "package.assemble", "inputs": [catalog_input],
        "provenance": {"source": "Curitiba v9 coverage catalog and explicit product schema seed",
                       "license": "schema seed rights remain unchanged"}}


def _write_root(root_db: Path, profile: dict, *, seed_sha256: str) -> dict:
    label = profile.get("package_label", "Curitiba v9 OSM")
    with sqlite3.connect(root_db) as db:
        db.execute("UPDATE productDbTable SET internalName=? WHERE uri='NAV/PRODUCT.NDS'", (label,))
        db.commit()
        _quick_check(db, "ROOT.NDS")
    return {"origin": "derived", "producer_step": "package.assemble", "inputs": [
        {"artifact": "bootstrap.verify", "path": "db/ROOT.NDS", "sha256": seed_sha256}],
        "provenance": {"source": "ROOT.NDS profile binding", "license": "schema seed rights remain unchanged"}}


def _refresh_metadata(db_root: Path) -> None:
    path = db_root / "metadata.json"
    document = _read_json(path)
    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("filePath"), str) and "fileHash" in value:
                candidate = (db_root / value["filePath"]).resolve()
                if db_root.resolve() not in candidate.parents or not candidate.is_file():
                    raise ValueError(f"metadata path is unsafe or missing: {value['filePath']}")
                value["fileHash"] = _sha(candidate)
                value["fileSize"] = candidate.stat().st_size
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(document)
    _write_json(path, document)


def assemble_v9(context: StepContext) -> dict[str, Any]:
    config = context.config.neusoft
    if config.get("profile_id") != PROFILE:
        raise ValueError("Package assembly is currently limited to the pinned Curitiba v9 profile")
    # Reject contradictory administrative claims before copying bootstrap files
    # or resolving any external secret.
    validate_admin_scope(config["names"])
    profile = {**config, "profile_id": PROFILE}
    bootstrap = context.external_inputs["neusoft.bootstrap_dir"]
    bootstrap_report = _read_json(context.dependency_outputs["bootstrap.verify"] / "bootstrap-report.json")
    if bootstrap_report.get("profile_id") != PROFILE:
        raise ValueError("Bootstrap report profile mismatch")
    verified = verify_bootstrap(bootstrap, PROFILE)
    validate_native_category_sources(config["poi"],
                                     {record["logical_name"]: record for record in verified["files"]})
    records_by_name, origins = _copy_bootstrap_assets(bootstrap, verified["files"], context.output_dir / "db")
    keys_ref = config.get("container_keys")
    if not isinstance(keys_ref, dict):
        raise ValueError("neusoft.container_keys must map each generated database path to a provider")
    resolver = context.resolve_secret
    if resolver is None:
        raise RuntimeError("No runtime secret resolver available for schema seeds")
    keys = {path: resolver(ref) for path, ref in keys_ref.items() if path in GENERATED_ZV}
    if set(keys) != GENERATED_ZV:
        raise ValueError("Curitiba v9 assembly requires six explicit per-output keys")

    dirs = context.dependency_outputs
    bmd_dir, names_dir = dirs["geometry.bmd"], dirs["names.compile"]
    poi_dir, route_dir, aux_dir = dirs["poi.compile"], dirs["routing.main"], dirs["routing.aux"]
    catalog_dir = dirs["catalogs.build"]
    catalog = _read_json(catalog_dir / "catalogs.json")
    db_root = context.output_dir / "db"
    with tempfile.TemporaryDirectory(prefix="nds-forge-v9-schema-") as scratch_name:
        scratch = Path(scratch_name)
        _materialize_seed(context, bootstrap, records_by_name, db_root, scratch, keys)
        for seed in ("db/NAV/PRODUCT.NDS", "db/ROOT.NDS"):
            item = records_by_name.get(seed)
            if not item or item.get("role") != "schema_seed" or item.get("format") != "sqlite3":
                raise ValueError(f"Expected plaintext SQLite schema seed {seed}")
            source = bootstrap / item["path"]
            dest = db_root / Path(seed).relative_to("db")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
        _write_bmd(db_root / "NAV/UR439/BMD.NDS", bmd_dir)
        names_origin, fts_origin = _write_names(db_root / "NAV/UR439/NAME.NDS",
                                                db_root / "NAV/UR439/FTS.NDS",
                                                names_dir, poi_dir, config)
        poi_origin = _write_pois(db_root / "NAV/UR439/POI.NDS", db_root / "NAV/UR439/FTS.NDS",
                                 poi_dir, bmd_dir, config["poi"])
        route_origin = _write_route(db_root / "NAV/UR439/ROUTE.NDS", route_dir, aux_dir)
        shared_origin = _write_shared(db_root / "NAV/UR439/SHARED.NDS", catalog, profile,
                                      catalog_dir=catalog_dir)
        product_origin = _write_product(db_root / "NAV/PRODUCT.NDS", catalog, profile,
                                        catalog_dir=catalog_dir)
        root_origin = _write_root(db_root / "ROOT.NDS", profile)
    # Schema seeds are SQLite databases with preallocated/freelist pages. DELETE
    # clears their rows but does not shrink the file, so compact generated DBs
    # before hashing metadata and before ZV packing.
    for relative in sorted(GENERATED_ZV):
        database = db_root / Path(relative).relative_to("db")
        with sqlite3.connect(database) as db:
            _quick_check(db, relative)
            db.execute("VACUUM")
            _quick_check(db, relative)
    origins.update({
        "db/NAV/UR439/BMD.NDS": _write_bmd_origin(bmd_dir),
        "db/NAV/UR439/NAME.NDS": names_origin,
        "db/NAV/UR439/FTS.NDS": fts_origin,
        "db/NAV/UR439/POI.NDS": poi_origin,
        "db/NAV/UR439/ROUTE.NDS": route_origin,
        "db/NAV/UR439/SHARED.NDS": shared_origin,
        "db/NAV/PRODUCT.NDS": product_origin,
        "db/ROOT.NDS": root_origin,
    })
    _refresh_metadata(db_root)
    origins["db/metadata.json"] = {**origins["db/metadata.json"],
        "inputs": [{"artifact": "package.assemble", "path": "db/metadata.json",
                    "sha256": _sha(db_root / "metadata.json")} ]}
    required = sorted(REQUIRED_PATHS)
    actual = {"db/" + path.relative_to(db_root).as_posix() for path in db_root.rglob("*") if path.is_file()}
    if actual != REQUIRED_PATHS:
        raise ValueError(f"Assembled package path set mismatch: missing={sorted(REQUIRED_PATHS-actual)}, extra={sorted(actual-REQUIRED_PATHS)}")
    files = []
    for path in required:
        origin = origins.get(path)
        if not origin:
            raise ValueError(f"No explicit origin/provenance record for {path}")
        if path in GENERATED_ZV:
            origin = {**origin, "origin": "generated"}
        elif path in {"db/NAV/PRODUCT.NDS", "db/NAV/UR439/SHARED.NDS", "db/ROOT.NDS", "db/metadata.json"}:
            origin = {**origin, "origin": "derived"}
        files.append({"path": path, **origin, "format": FORMATS[path],
                      "validation_status": "unvalidated"})
    layout = {"schema_version": 1, "profile_id": PROFILE, "package_label": profile.get("package_label", "Curitiba v9 OSM"),
              "required_paths": required, "pack_zv_sqlite_paths": sorted(GENERATED_ZV), "files": files,
              "source_artifacts": {"bmd_manifest_sha256": _sha(bmd_dir / "bmd-manifest.json"),
                  "names_sha256": _sha(names_dir / "name-records.json"),
                  "poi_sha256": _sha(poi_dir / "poi-records.json"),
                  "route_manifest_sha256": _sha(route_dir / "route-manifest.json"),
                  "aux_manifest_sha256": _sha(aux_dir / "aux-manifest.json"),
                  "catalogs_sha256": _sha(catalog_dir / "catalogs.json")},
              "validation_status": "unvalidated",
              "limitations": ["This writer is pinned to the Curitiba v9 profile and Israel schema bootstrap.",
                              "Output generation is not native/SDK/UI/device validation.",
                              "Inherited TMC/EXTTMC files are copied unchanged and do not establish Brazilian traffic coverage."]}
    _write_json(context.output_dir / "package-layout.json", layout)
    return {"file_count": len(files), "origin_counts": {
                key: sum(1 for item in files if item["origin"] == key)
                for key in ("generated", "derived", "copied_external_asset")},
            "outputs": {path: {"bytes": (db_root / Path(path).relative_to("db")).stat().st_size,
                               "sha256": _sha(db_root / Path(path).relative_to("db"))} for path in required},
            "maturity": "experimental", "validation_status": "unvalidated",
            "capabilities_produced": ["curitiba_v9_21_path_package_tree"],
            "capabilities_missing": ["container_encoding", "native_parser_validation",
                                     "SDK_calculation", "UI_validation", "vehicle_guidance"]}


def _write_bmd_origin(bmd_dir: Path) -> dict:
    return {"origin": "generated", "producer_step": "geometry.bmd",
            "inputs": [_artifact_ref("geometry.bmd", bmd_dir, "bmd-manifest.json")],
            "provenance": {"source": "OSM road geometry", "license": "ODbL-1.0; © OpenStreetMap contributors"}}
