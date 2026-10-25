"""DDL-only SQLite contracts and fresh, empty databases for format research.

This is an offline research primitive, not an NDS package builder. Contracts
retain format/schema provenance but never copy table rows or database pages.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def export_contract(source: Path) -> dict[str, Any]:
    """Inspect an explicitly supplied plaintext SQLite DB without reading rows."""
    source = source.resolve(strict=True)
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as db:
        shadow = {row[1] for row in db.execute("PRAGMA table_list") if row[2] == "shadow"}
        objects = []
        for kind, name, table, sql in db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY rowid"
        ):
            if name.startswith("sqlite_") or name in shadow or table in shadow:
                continue
            if kind not in {"table", "index", "view"}:
                raise ValueError(f"Unsupported schema object {kind}: {name}")
            objects.append({"type": kind, "name": name, "table": table, "sql": sql})
        pragmas = {key: db.execute(f"PRAGMA {key}").fetchone()[0]
                   for key in ("page_size", "encoding", "user_version", "application_id")}
    return {
        "schema_version": 1, "kind": "sqlite-ddl-only",
        "source": {"name": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
        "pragmas": pragmas, "objects": objects,
        "limitations": ["Schema is observed, not independently specified or natively validated.",
                        "No source rows, pages, geographic records or metadata values are exported."],
    }


def _validate(contract: dict[str, Any]) -> None:
    if contract.get("schema_version") != 1 or contract.get("kind") != "sqlite-ddl-only":
        raise ValueError("Expected sqlite-ddl-only schema contract version 1")
    if not isinstance(contract.get("objects"), list):
        raise ValueError("Schema objects must be an array")  # noqa: TRY004 -- Invalid serialized contracts use a consistent ValueError interface.
    patterns = {
        "table": r"CREATE\s+(?:VIRTUAL\s+)?TABLE\s+",
        "index": r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+",
        "view": r"CREATE\s+VIEW\s+",
    }
    seen = set()
    for obj in contract["objects"]:
        if not isinstance(obj, dict) or obj.get("type") not in patterns:
            raise ValueError("Only table, index and view definitions are supported")
        name, sql = obj.get("name"), obj.get("sql")
        if not isinstance(name, str) or not name or name.startswith("sqlite_") or name in seen:
            raise ValueError("Schema object names must be distinct and non-internal")
        if not isinstance(sql, str) or not re.match(patterns[obj["type"]], sql, re.IGNORECASE):
            raise ValueError(f"Expected CREATE definition for {name}")
        seen.add(name)
    pragmas = contract.get("pragmas", {})
    page_size = pragmas.get("page_size", 4096)
    if type(page_size) is not int or not 512 <= page_size <= 65536 or page_size & (page_size - 1):
        raise ValueError("Invalid SQLite page size")
    if pragmas.get("encoding", "UTF-8") not in {"UTF-8", "UTF-16le", "UTF-16be"}:
        raise ValueError("Unsupported SQLite encoding")
    for key in ("user_version", "application_id"):
        value = pragmas.get(key, 0)
        if type(value) is not int or not -(1 << 31) <= value < (1 << 31):
            raise ValueError(f"Invalid SQLite {key}")


def create_empty_database(contract: dict[str, Any], output: Path) -> dict[str, Any]:
    """Create a fresh DB from DDL alone; missing modules fail without publication.

    No extension is installed or loaded automatically. The builder can run
    after the source database has been removed, since only the contract is used.
    """
    _validate(contract)
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".fresh-schema-", dir=output.parent)
    os.close(descriptor)
    stage = Path(temporary)
    try:
        with closing(sqlite3.connect(stage)) as db:
            pragmas = contract.get("pragmas", {})
            for key in ("page_size", "user_version", "application_id"):
                db.execute(f"PRAGMA {key}={pragmas.get(key, 4096 if key == 'page_size' else 0)}")
            db.execute(f"PRAGMA encoding='{pragmas.get('encoding', 'UTF-8')}'")
            # CREATE statements execute one at a time; triggers, DML and
            # multi-statement scripts are deliberately unsupported.
            for obj in contract["objects"]:
                db.execute(obj["sql"])
            expected = {(obj["type"], obj["name"]) for obj in contract["objects"]}
            actual = set(db.execute("SELECT type,name FROM sqlite_master"))
            if not expected <= actual:
                raise ValueError("Created schema objects disagree with contract")
            row_counts = {}
            for obj in contract["objects"]:
                if obj["type"] == "table":
                    count = db.execute(f"SELECT count(*) FROM {_quote(obj['name'])}").fetchone()[0]
                    if count:
                        raise ValueError(f"Fresh schema unexpectedly contains rows: {obj['name']}")
                    row_counts[obj["name"]] = count
            db.commit()
            if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("Fresh database failed SQLite integrity_check")
        # Exclusive atomic publication: an existing destination is never replaced.
        os.link(stage, output)
    finally:
        stage.unlink(missing_ok=True)
    return {"execution_status": "completed", "validation_status": "sqlite_schema_only",
            "bytes": output.stat().st_size, "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "table_rows": row_counts, "copied_source_rows": 0,
            "native_or_sdk_validation": "not_run", "installable_nds_package": False}
