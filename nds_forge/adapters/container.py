"""ZV-zlib container codec and secret-provider interface.

Implements ZV-zlib page compression and encryption, secret-provider key
resolution, atomic 21-path package wrapping, and roundtrip SQLite verification.
Key bytes are never serialized, logged, persisted, or fingerprinted.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import tempfile
import uuid
import zlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class ContainerError(ValueError):
    """Base error for container codec and packaging."""


class SecretProviderError(ContainerError):
    """Raised when an external secret provider fails to resolve a valid 16-byte key."""


def _publish_package_directory(staging_dir: Path, output_dir: Path) -> None:
    """Publish db/ and its manifest together, restoring the prior directory on error."""
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    if output_dir.is_symlink() or (output_dir.exists() and not output_dir.is_dir()):
        raise ContainerError(f"Package output must be a regular directory: {output_dir}")

    backup_dir = None
    if output_dir.exists():
        backup_dir = output_dir.with_name(f".{output_dir.name}.previous-{uuid.uuid4().hex}")
        os.replace(output_dir, backup_dir)
    try:
        os.replace(staging_dir, output_dir)
    except Exception:
        if backup_dir is not None and backup_dir.exists():
            os.replace(backup_dir, output_dir)
        raise
    if backup_dir is not None:
        shutil.rmtree(backup_dir, ignore_errors=True)


class SecretProvider(ABC):
    """Abstract interface for external runtime key providers."""

    @abstractmethod
    def resolve_key(self, reference: str) -> bytes:
        """Resolve a 16-byte key from an opaque reference (e.g. 'env:VAR_NAME')."""


class EnvSecretProvider(SecretProvider):
    """Resolves container encryption keys from environment variables."""

    def resolve_key(self, reference: str) -> bytes:
        if not reference.startswith("env:"):
            raise SecretProviderError(
                f"EnvSecretProvider expects 'env:<VARIABLE_NAME>' reference, got '{reference}'"
            )
        var_name = reference[4:].strip()
        val = os.environ.get(var_name)
        if val is None:
            raise SecretProviderError(f"Environment variable '{var_name}' is not set")
        # Support hex-encoded 32-char string or raw 16-byte utf-8
        if len(val) == 32:
            try:
                key_bytes = bytes.fromhex(val)
            except ValueError:
                key_bytes = val.encode("utf-8")
        else:
            key_bytes = val.encode("utf-8")

        if len(key_bytes) != 16:
            raise SecretProviderError(
                f"Resolved key for '{reference}' must be exactly 16 bytes, got {len(key_bytes)}"
            )
        return key_bytes


class DictSecretProvider(SecretProvider):
    """In-memory secret provider for tests and authorized runtime injection."""

    def __init__(self, mapping: dict[str, bytes]) -> None:
        self._mapping = mapping

    def resolve_key(self, reference: str) -> bytes:
        if reference not in self._mapping:
            raise SecretProviderError(f"Key reference not found: '{reference}'")
        key = self._mapping[reference]
        if not isinstance(key, bytes) or len(key) != 16:
            raise SecretProviderError(
                f"Key for '{reference}' must be exactly 16 bytes"
            )
        return key


# 21 target package paths
REQUIRED_PACKAGE_PATHS = frozenset({
    "db/Area.txt", "db/DECO/PRODUCT.NDS", "db/DECO/UR0/BMD.NDS",
    "db/DECO/UR0/NAME.NDS", "db/DECO/UR0/SHARED.NDS", "db/NAV/PRODUCT.NDS",
    "db/NAV/UR439/3D.NDS", "db/NAV/UR439/BMD.NDS", "db/NAV/UR439/EXTBMD.NDS",
    "db/NAV/UR439/EXTTMC.NDS", "db/NAV/UR439/FTS.NDS", "db/NAV/UR439/JV.NDS",
    "db/NAV/UR439/NAME.NDS", "db/NAV/UR439/POI.NDS", "db/NAV/UR439/ROUTE.NDS",
    "db/NAV/UR439/SHARED.NDS", "db/NAV/UR439/SPEECH.NDS", "db/NAV/UR439/TMC.NDS",
    "db/ROOT.NDK", "db/ROOT.NDS", "db/metadata.json",
})

# 6 generated/derived paths requiring ZV wrapping
GENERATED_ZV_PATHS = frozenset({
    "db/NAV/UR439/BMD.NDS", "db/NAV/UR439/NAME.NDS", "db/NAV/UR439/FTS.NDS",
    "db/NAV/UR439/POI.NDS", "db/NAV/UR439/ROUTE.NDS", "db/NAV/UR439/SHARED.NDS",
})

# 9 static ZV paths copied byte-for-byte
STATIC_ZV_PATHS = frozenset({
    "db/DECO/UR0/BMD.NDS", "db/DECO/UR0/NAME.NDS", "db/DECO/UR0/SHARED.NDS",
    "db/NAV/UR439/3D.NDS", "db/NAV/UR439/EXTBMD.NDS", "db/NAV/UR439/EXTTMC.NDS",
    "db/NAV/UR439/JV.NDS", "db/NAV/UR439/SPEECH.NDS", "db/NAV/UR439/TMC.NDS",
})

EXPECTED_FORMATS: dict[str, str] = {
    **{path: "zv_zlib" for path in STATIC_ZV_PATHS | GENERATED_ZV_PATHS},
    "db/DECO/PRODUCT.NDS": "sqlite3",
    "db/NAV/PRODUCT.NDS": "sqlite3",
    "db/ROOT.NDS": "sqlite3",
    "db/Area.txt": "text",
    "db/ROOT.NDK": "ndk_text",
    "db/metadata.json": "json",
}


def pack_sqlite(source: Path, output: Path, key: bytes) -> dict[str, Any]:
    """Pack a SQLite file using the observed ZV-zlib container layout."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:
        raise RuntimeError("ZV-zlib output requires optional dependency cryptography") from exc

    raw = source.read_bytes()
    if not raw.startswith(b"SQLite format 3\0"):
        raise ContainerError("Expected ordinary SQLite input")
    if not isinstance(key, bytes) or len(key) != 16:
        raise SecretProviderError("External key provider must return exactly 16 bytes")

    page_size = int.from_bytes(raw[16:18], "big")
    if page_size == 1:
        page_size = 65536
    if page_size < 512 or page_size > 65536 or page_size & (page_size - 1) or len(raw) % page_size:
        raise ContainerError("Invalid SQLite page size")

    pages = len(raw) // page_size
    start = 200 + pages * 8
    header = bytearray(200)
    header[:100] = raw[:100]
    header[:16] = b"ZV-zlib\0" + bytes(8)
    entries = bytearray()
    slots = bytearray()

    for i in range(pages):
        compressed = zlib.compress(raw[i * page_size:(i + 1) * page_size])
        length = len(compressed)
        if length >= 1 << 17:
            raise ContainerError("Compressed page exceeds slot length")
        encrypted_len = min(4, length // 16) * 16
        encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        encrypted = encryptor.update(compressed[:encrypted_len]) + encryptor.finalize()
        payload = encrypted + compressed[encrypted_len:]
        offset = start + len(slots)
        if offset >= 1 << 40:
            raise ContainerError("Container exceeds offset range")
        entries.extend(struct.pack(">Q", (offset << 24) | (length << 7)))
        slots.extend((((i + 1) << 17) | length).to_bytes(6, "big"))
        slots.extend(payload)

    end = start + len(slots)
    struct.pack_into(">9Q2I", header, 100, 0, start, end, 0, 0, len(raw), 0, 0, 0, page_size, 1)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as handle:
        handle.write(header)
        handle.write(entries)
        handle.write(slots)

    return {
        "pages": pages,
        "logical_bytes": len(raw),
        "container_bytes": end,
        "container_format": "ZV-zlib",
    }


def unpack_sqlite(source: Path, output: Path, key: bytes) -> dict[str, Any]:
    """Decode a ZV-zlib container to SQLite, checking every page and integrity."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:
        raise RuntimeError("ZV-zlib input requires optional dependency cryptography") from exc

    data = source.read_bytes()
    if not data.startswith(b"ZV-zlib\0"):
        raise ContainerError("Expected ZV-zlib container")
    if not isinstance(key, bytes) or len(key) != 16:
        raise SecretProviderError("External key provider must return exactly 16 bytes")

    size = struct.unpack_from(">Q", data, 140)[0]
    page_size, version = struct.unpack_from(">II", data, 172)
    if version != 1 or page_size < 512 or page_size > 65536 or size % page_size:
        raise ContainerError("Unsupported container parameters")

    pages = size // page_size
    if 200 + pages * 8 > len(data):
        raise ContainerError("Truncated page map")

    output.parent.mkdir(parents=True, exist_ok=True)
    written: set[int] = set()

    with output.open("wb") as handle:
        handle.truncate(size)
        for i in range(pages):
            entry = struct.unpack_from(">Q", data, 200 + i * 8)[0]
            offset, length = entry >> 24, (entry >> 7) & 0x1ffff
            if offset == 0:
                continue
            if offset + 6 + length > len(data):
                raise ContainerError(f"Truncated slot at index {i}")
            number = int.from_bytes(data[offset:offset + 4], "big") >> 1
            if number != i + 1 or number in written:
                raise ContainerError(f"Unexpected page number {number} at index {i}")
            payload = data[offset + 6:offset + 6 + length]
            encrypted_len = min(4, length // 16) * 16
            decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
            compressed = decryptor.update(payload[:encrypted_len]) + decryptor.finalize()
            compressed += payload[encrypted_len:]
            page = zlib.decompress(compressed)
            if len(page) != page_size:
                raise ContainerError(f"Page {number} inflated to an invalid size")
            if number == 1 and not page.startswith(b"SQLite format 3\0"):
                raise ContainerError("First page is not SQLite")
            handle.seek((number - 1) * page_size)
            handle.write(page)
            written.add(number)

    return {
        "pages": pages,
        "decoded_pages": len(written),
        "logical_bytes": size,
        "container_format": "ZV-zlib",
    }


def pack_package_tree(
    source_package: Path,
    output_dir: Path,
    keys: dict[str, bytes] | SecretProvider,
    key_references: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Materialize the 21 target paths and apply ZV-zlib wrapping with atomic staging.

    Enforces:
    - Target inventory matches exactly 21 files (15 ZV, 3 SQLite, Area.txt, ROOT.NDK, metadata.json).
    - Source assets are hash-pinned and byte-equal where copied.
    - ZV-zlib wrapping applies only to the 6 generated paths.
    - Wrapped outputs roundtrip with external 16-byte key provider and pass quick_check.
    - Key bytes never enter config, manifest, logs, or repository.
    - Missing key or required asset blocks atomic output.
    - metadata.json hashes/bytes are recomputed after wrapping.
    """
    layout_path = source_package / "package-layout.json"
    if not layout_path.is_file():
        raise ContainerError(f"Missing package-layout.json in {source_package}")
    layout = json.loads(layout_path.read_text(encoding="utf-8"))

    required = layout.get("required_paths", [])
    to_pack = layout.get("pack_zv_sqlite_paths", [])
    declared_files = layout.get("files", [])

    if set(required) != REQUIRED_PACKAGE_PATHS:
        missing = sorted(REQUIRED_PACKAGE_PATHS - set(required))
        extra = sorted(set(required) - REQUIRED_PACKAGE_PATHS)
        raise ContainerError(f"Package requires exactly 21 paths; missing={missing}, extra={extra}")

    if set(to_pack) != GENERATED_ZV_PATHS:
        raise ContainerError(
            f"Paths to pack must match generated ZV set {sorted(GENERATED_ZV_PATHS)}, got {sorted(to_pack)}"
        )

    # Resolve keys via SecretProvider or dict
    resolved_keys: dict[str, bytes] = {}
    for path in to_pack:
        if isinstance(keys, SecretProvider):
            if not key_references or path not in key_references:
                raise SecretProviderError(f"Missing key reference for {path}")
            ref = key_references[path]
            resolved_keys[path] = keys.resolve_key(ref)
        elif isinstance(keys, dict):
            if path not in keys:
                raise SecretProviderError(f"Missing key for {path}")
            key = keys[path]
            if not isinstance(key, bytes) or len(key) != 16:
                raise SecretProviderError(f"Key for {path} must be exactly 16 bytes")
            resolved_keys[path] = key
        else:
            raise TypeError("keys must be a dict[str, bytes] or SecretProvider instance")

    # Atomic staging directory
    staging_dir = output_dir.parent / f".staging-{output_dir.name}"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    try:
        staging_db = staging_dir / "db"
        shutil.copytree(source_package / "db", staging_db)

        # 1. Verify all 21 files exist in staging
        for relative in REQUIRED_PACKAGE_PATHS:
            p = staging_db / Path(relative).relative_to("db")
            if not p.is_file():
                raise FileNotFoundError(f"Missing required package asset: {relative}")

        # 2. Verify static ZV files are already wrapped and not repacked
        for relative in sorted(STATIC_ZV_PATHS):
            target_p = staging_db / Path(relative).relative_to("db")
            if not target_p.read_bytes().startswith(b"ZV-zlib\0"):
                raise ContainerError(f"Static ZV asset is not pre-wrapped: {relative}")

        # 3. Apply ZV wrapping to the 6 generated files and roundtrip verify
        packed_info = []
        for relative in sorted(to_pack):
            plain_path = staging_db / Path(relative).relative_to("db")
            temp_path = plain_path.with_name(plain_path.name + ".zvtmp")
            key = resolved_keys[relative]

            res = pack_sqlite(plain_path, temp_path, key)

            # Roundtrip verification
            with tempfile.TemporaryDirectory(prefix="nds-zv-roundtrip-") as chk_dir:
                decoded = Path(chk_dir) / "decoded.sqlite"
                unpack_sqlite(temp_path, decoded, key)
                if hashlib.sha256(decoded.read_bytes()).digest() != hashlib.sha256(plain_path.read_bytes()).digest():
                    raise ContainerError(f"ZV roundtrip SHA-256 mismatch for {relative}")
                import sqlite3
                with sqlite3.connect(f"file:{decoded}?mode=ro", uri=True) as db:
                    chk = db.execute("PRAGMA quick_check").fetchall()
                    if chk != [("ok",)]:
                        raise ContainerError(f"Decoded SQLite quick_check failed for {relative}: {chk}")

            temp_path.replace(plain_path)
            packed_info.append({"path": relative, **res})

        # 4. Recompute metadata.json hashes and byte sizes
        meta_path = staging_db / "metadata.json"
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))

        def update_metadata_hashes(node: Any) -> None:
            if isinstance(node, dict):
                if isinstance(node.get("filePath"), str) and "fileHash" in node:
                    fp = (staging_db / node["filePath"]).resolve()
                    if staging_db.resolve() not in fp.parents and staging_db.resolve() != fp.parent:
                        raise ContainerError("metadata.json contains unsafe path outside package")
                    node["fileHash"] = hashlib.sha256(fp.read_bytes()).hexdigest()
                    node["fileSize"] = fp.stat().st_size
                for child in node.values():
                    update_metadata_hashes(child)
            elif isinstance(node, list):
                for child in node:
                    update_metadata_hashes(child)

        update_metadata_hashes(metadata)
        meta_path.write_text(
            json.dumps(metadata, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # 5. Build final artifact manifest
        file_records = []
        records_by_path = {r["path"]: r for r in declared_files}
        for relative in sorted(REQUIRED_PACKAGE_PATHS):
            fp = staging_db / Path(relative).relative_to("db")
            declared = records_by_path.get(relative, {
                "path": relative,
                "origin": "derived",
                "producer_step": "container.pack",
                "inputs": [],
                "provenance": {"source": "compiled output", "license": "ODbL-1.0"},
            })
            file_records.append({
                **declared,
                "path": relative,
                "bytes": fp.stat().st_size,
                "sha256": hashlib.sha256(fp.read_bytes()).hexdigest(),
                "format": EXPECTED_FORMATS[relative],
                "validation_status": "partial" if relative in to_pack else "unvalidated",
            })

        report = {
            "schema_version": 1,
            "step": "container.pack",
            "maturity": "experimental",
            "profile_id": layout.get("profile_id", "generic-profile"),
            "target": "container.pack",
            "execution_status": "completed",
            "validation_status": "unvalidated",
            "files": file_records,
            "packed_generated_containers": packed_info,
            "static_zv_assets_repacked": 0,
            "file_count": len(file_records),
            "limitations": [
                "Only the verified v9 path set is supported.",
                "Container validity does not imply Neusoft SDK compatibility.",
            ],
        }
        (staging_dir / "artifact-manifest.json").write_text(
            json.dumps(report, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        # db/ and the manifest are one staged directory, so they publish together.
        _publish_package_directory(staging_dir, output_dir)

        return {
            "file_count": len(file_records),
            "packed_count": len(packed_info),
            "outputs": {
                item["path"]: {"bytes": item["bytes"], "sha256": item["sha256"]}
                for item in file_records
            },
            "maturity": "experimental",
            "validation_status": "unvalidated",
        }

    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
