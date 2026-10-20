"""Structural checker for a Neusoft package sidecar.

This checker verifies inventory, origin declarations and byte hashes only. It
does not decode NDS data or establish parser, SDK, UI, guidance, or vehicle
compatibility.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "tests/fixtures/neusoft_curitiba_v9_package.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class InventoryError(ValueError):
    pass


def load_contract(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    files = contract.get("files")
    if contract.get("schema_version") != 1 or not isinstance(files, dict):
        raise InventoryError("invalid package inventory contract")
    return contract


def _safe_file(package_dir: Path, relative: str) -> Path:
    candidate = PurePosixPath(relative)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise InventoryError(f"unsafe package path: {relative!r}")
    path = package_dir.joinpath(*candidate.parts)
    if path.is_symlink() or not path.is_file():
        raise InventoryError(f"package output is missing or not a regular file: {relative}")
    if not path.resolve().is_relative_to(package_dir.resolve()):
        raise InventoryError(f"package output escapes package directory: {relative}")
    return path


def _detect_format(path: Path) -> str:
    with path.open("rb") as handle:
        header = handle.read(32)
    if header.startswith(b"ZV-zlib\0"):
        return "zv_zlib"
    if header.startswith(b"SQLite format 3\0"):
        return "sqlite3"
    if header.startswith(b"# Neusoft NDK"):
        return "ndk_text"
    if path.name == "metadata.json":
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InventoryError("metadata.json is not valid UTF-8 JSON") from exc
        return "json"
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise InventoryError(f"unrecognized binary format: {path.name}") from exc
    if "\0" in text:
        raise InventoryError(f"text output contains NUL bytes: {path.name}")
    return "text"


def validate_package(package_dir: Path, manifest_path: Path) -> dict[str, Any]:
    """Verify the v9 package inventory and sidecar hashes without promotion."""
    contract = load_contract()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise InventoryError("unsupported artifact manifest schema")
    if manifest.get("profile_id") != contract["profile_id"]:
        raise InventoryError("artifact manifest profile does not match contract")
    if manifest.get("target") != "container.pack":
        raise InventoryError("artifact manifest target must be container.pack")
    if manifest.get("execution_status") != "completed":
        raise InventoryError("package execution is not completed")
    if manifest.get("validation_status") != "unvalidated":
        raise InventoryError("package manifest must not claim external validation")

    records = manifest.get("files")
    if not isinstance(records, list):
        raise InventoryError("artifact manifest files must be a list")
    by_path: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise InventoryError("invalid file record")
        rel = record["path"]
        if rel in by_path:
            raise InventoryError(f"duplicate package path: {rel}")
        by_path[rel] = record

    expected = contract["files"]
    if set(by_path) != set(expected):
        missing = sorted(set(expected) - set(by_path))
        extra = sorted(set(by_path) - set(expected))
        raise InventoryError(f"package inventory mismatch; missing={missing}; extra={extra}")

    verified = []
    for rel, expected_origin in expected.items():
        record = by_path[rel]
        if record.get("origin") != expected_origin:
            raise InventoryError(f"wrong origin role for {rel}")
        if not isinstance(record.get("producer_step"), str) or not record["producer_step"]:
            raise InventoryError(f"missing producer_step for {rel}")
        inputs = record.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            raise InventoryError(f"missing declared input lineage for {rel}")
        for item in inputs:
            if not isinstance(item, dict) or not SHA256_RE.fullmatch(str(item.get("sha256", ""))):
                raise InventoryError(f"invalid input digest for {rel}")
        provenance = record.get("provenance")
        if not isinstance(provenance, dict) or not provenance:
            raise InventoryError(f"missing provenance for {rel}")
        if expected_origin == "copied_external_asset":
            if not provenance.get("source") or not provenance.get("license"):
                raise InventoryError(f"copied asset lacks source/license provenance: {rel}")
        expected_hash = record.get("sha256")
        if not isinstance(expected_hash, str) or not SHA256_RE.fullmatch(expected_hash):
            raise InventoryError(f"invalid output digest for {rel}")
        path = _safe_file(package_dir, rel)
        expected_format = contract["formats"][rel]
        actual_format = _detect_format(path)
        if actual_format != expected_format:
            raise InventoryError(f"wrong storage format for {rel}: expected {expected_format}, got {actual_format}")
        if record.get("format") != expected_format:
            raise InventoryError(f"wrong manifest format for {rel}")
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        actual_bytes = path.stat().st_size
        if record.get("bytes") != actual_bytes or expected_hash != actual_hash:
            raise InventoryError(f"output bytes/hash do not match manifest: {rel}")
        verified.append({"path": rel, "bytes": actual_bytes, "sha256": actual_hash,
                         "origin": expected_origin, "format": actual_format})

    return {
        "result": "inventory_and_hashes_pass",
        "profile_id": contract["profile_id"],
        "files_verified": len(verified),
        "by_origin": {origin: sum(r["origin"] == origin for r in verified)
                      for origin in ("generated", "derived", "copied_external_asset")},
        "by_format": {format_name: sum(r["format"] == format_name for r in verified)
                      for format_name in ("zv_zlib", "sqlite3", "ndk_text", "text", "json")},
        "compatibility": "not_assessed",
        "validation_status": "unvalidated",
        "files": verified,
    }
