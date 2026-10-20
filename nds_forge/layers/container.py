"""ZV-zlib container codec for authorized Neusoft output databases.

Only storage is implemented here. The caller must provide a key from an
external secret provider at runtime; this module never resolves, persists,
prints, or fingerprints key material.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
import shutil
import re
import tempfile
import struct
import zlib


def pack_sqlite(source: Path, output: Path, key: bytes) -> dict:
    """Pack a SQLite file using the observed ZV-zlib container layout."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:
        raise RuntimeError("ZV-zlib output requires optional dependency cryptography") from exc
    raw = source.read_bytes()
    if not raw.startswith(b"SQLite format 3\0"):
        raise ValueError("Expected ordinary SQLite input")
    if not isinstance(key, bytes) or len(key) != 16:
        raise ValueError("External key provider must return exactly 16 bytes")
    page_size = int.from_bytes(raw[16:18], "big")
    if page_size == 1:
        page_size = 65536
    if page_size < 512 or page_size > 65536 or page_size & (page_size - 1) or len(raw) % page_size:
        raise ValueError("Invalid SQLite page size")
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
            raise ValueError("Compressed page exceeds slot length")
        encrypted_len = min(4, length // 16) * 16
        encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        encrypted = encryptor.update(compressed[:encrypted_len]) + encryptor.finalize()
        payload = encrypted + compressed[encrypted_len:]
        offset = start + len(slots)
        if offset >= 1 << 40:
            raise ValueError("Container exceeds offset range")
        entries.extend(struct.pack(">Q", (offset << 24) | (length << 7)))
        slots.extend((((i + 1) << 17) | length).to_bytes(6, "big"))
        slots.extend(payload)
    end = start + len(slots)
    struct.pack_into(">9Q2I", header, 100,
                     0, start, end, 0, 0, len(raw), 0, 0, 0, page_size, 1)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as handle:
        handle.write(header)
        handle.write(entries)
        handle.write(slots)
    return {"pages": pages, "logical_bytes": len(raw), "container_bytes": end,
            "container_format": "ZV-zlib"}


def unpack_sqlite(source: Path, output: Path, key: bytes) -> dict:
    """Decode a ZV-zlib container to SQLite, checking every page and integrity."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:
        raise RuntimeError("ZV-zlib input requires optional dependency cryptography") from exc
    data = source.read_bytes()
    if not data.startswith(b"ZV-zlib\0"):
        raise ValueError("Expected ZV-zlib container")
    if not isinstance(key, bytes) or len(key) != 16:
        raise ValueError("External key provider must return exactly 16 bytes")
    size = struct.unpack_from(">Q", data, 140)[0]
    page_size, version = struct.unpack_from(">II", data, 172)
    if version != 1 or page_size < 512 or page_size > 65536 or size % page_size:
        raise ValueError("Unsupported container parameters")
    pages = size // page_size
    if 200 + pages * 8 > len(data):
        raise ValueError("Truncated page map")
    output.parent.mkdir(parents=True, exist_ok=True)
    written: set[int] = set()
    with output.open("xb") as handle:
        handle.truncate(size)
        for i in range(pages):
            entry = struct.unpack_from(">Q", data, 200 + i * 8)[0]
            offset, length = entry >> 24, (entry >> 7) & 0x1ffff
            if offset == 0:
                continue
            if offset + 6 + length > len(data):
                raise ValueError(f"Truncated slot at index {i}")
            number = int.from_bytes(data[offset:offset + 4], "big") >> 1
            if number != i + 1 or number in written:
                raise ValueError(f"Unexpected page number {number} at index {i}")
            payload = data[offset + 6:offset + 6 + length]
            encrypted_len = min(4, length // 16) * 16
            decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
            compressed = decryptor.update(payload[:encrypted_len]) + decryptor.finalize()
            compressed += payload[encrypted_len:]
            page = zlib.decompress(compressed)
            if len(page) != page_size:
                raise ValueError(f"Page {number} inflated to an invalid size")
            if number == 1 and not page.startswith(b"SQLite format 3\0"):
                raise ValueError("First page is not SQLite")
            handle.seek((number - 1) * page_size)
            handle.write(page)
            written.add(number)
    return {"pages": pages, "decoded_pages": len(written), "logical_bytes": size,
            "container_format": "ZV-zlib"}


def pack_package_tree(source_package: Path, output_dir: Path, keys: dict[str, bytes]) -> dict:
    """Pack only the explicit generated-container paths in package layout.

    `source_package/package-layout.json` must list `pack_zv_sqlite_paths`,
    `required_paths`, and a file-level provenance record for every output.
    Static assets already wrapped as ZV-zlib are copied byte
    for byte by package assembly and are never repacked here. The source tree
    is read-only; output is built under this stage's private directory.
    """
    layout_path = source_package / "package-layout.json"
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    required = layout.get("required_paths")
    to_pack = layout.get("pack_zv_sqlite_paths")
    declared_files = layout.get("files")
    if not isinstance(required, list) or not isinstance(to_pack, list) or not isinstance(declared_files, list):
        raise ValueError("package-layout.json requires required_paths, pack_zv_sqlite_paths and files")
    if len(required) != len(set(required)) or len(to_pack) != len(set(to_pack)):
        raise ValueError("package layout contains duplicate paths")
    if not set(to_pack) <= set(required):
        raise ValueError("All paths to pack must be required package outputs")
    # This release profile currently has a proven six-file generated ZV set.
    # Keep it explicit until another target has independent container evidence.
    expected_pack = {
        "db/NAV/UR439/BMD.NDS", "db/NAV/UR439/NAME.NDS", "db/NAV/UR439/FTS.NDS",
        "db/NAV/UR439/POI.NDS", "db/NAV/UR439/ROUTE.NDS", "db/NAV/UR439/SHARED.NDS",
    }
    if set(to_pack) != expected_pack:
        raise ValueError("Unknown target container path set; this adapter only supports the verified v9 profile")
    if not isinstance(keys, dict) or set(keys) != expected_pack:
        raise ValueError("A separate named runtime key is required for each generated ZV output path")
    for path, key in keys.items():
        if not isinstance(key, bytes) or len(key) != 16:
            raise ValueError(f"Runtime key for {path} must contain exactly 16 bytes")
    expected_paths = {
        "db/Area.txt", "db/DECO/PRODUCT.NDS", "db/DECO/UR0/BMD.NDS",
        "db/DECO/UR0/NAME.NDS", "db/DECO/UR0/SHARED.NDS", "db/NAV/PRODUCT.NDS",
        "db/NAV/UR439/3D.NDS", "db/NAV/UR439/BMD.NDS", "db/NAV/UR439/EXTBMD.NDS",
        "db/NAV/UR439/EXTTMC.NDS", "db/NAV/UR439/FTS.NDS", "db/NAV/UR439/JV.NDS",
        "db/NAV/UR439/NAME.NDS", "db/NAV/UR439/POI.NDS", "db/NAV/UR439/ROUTE.NDS",
        "db/NAV/UR439/SHARED.NDS", "db/NAV/UR439/SPEECH.NDS", "db/NAV/UR439/TMC.NDS",
        "db/ROOT.NDK", "db/ROOT.NDS", "db/metadata.json",
    }
    if set(required) != expected_paths:
        missing, extra = sorted(expected_paths - set(required)), sorted(set(required) - expected_paths)
        raise ValueError(f"Verified v9 package requires exactly 21 paths; missing={missing}, extra={extra}")
    records_by_path = {}
    for record in declared_files:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise ValueError("package-layout files entries require a path")
        if record["path"] in records_by_path:
            raise ValueError(f"Duplicate package provenance record: {record['path']}")
        records_by_path[record["path"]] = record
    if set(records_by_path) != expected_paths:
        raise ValueError("package-layout files metadata must cover exactly the 21 required paths")
    sha_re = re.compile(r"^[0-9a-f]{64}$")
    static_zv = {
        "db/DECO/UR0/BMD.NDS", "db/DECO/UR0/NAME.NDS", "db/DECO/UR0/SHARED.NDS",
        "db/NAV/UR439/3D.NDS", "db/NAV/UR439/EXTBMD.NDS", "db/NAV/UR439/EXTTMC.NDS",
        "db/NAV/UR439/JV.NDS", "db/NAV/UR439/SPEECH.NDS", "db/NAV/UR439/TMC.NDS",
    }
    allowed_origins = {"generated", "derived", "copied_external_asset"}
    expected_formats = {
        **{path: "zv_zlib" for path in static_zv | expected_pack},
        "db/DECO/PRODUCT.NDS": "sqlite3", "db/NAV/PRODUCT.NDS": "sqlite3",
        "db/ROOT.NDS": "sqlite3", "db/Area.txt": "text", "db/ROOT.NDK": "ndk_text",
        "db/metadata.json": "json",
    }
    for relative, record in records_by_path.items():
        if record.get("origin") not in allowed_origins:
            raise ValueError(f"Invalid output origin for {relative}")
        if not isinstance(record.get("producer_step"), str) or not record["producer_step"]:
            raise ValueError(f"Missing producer_step for {relative}")
        if record.get("format") != expected_formats[relative]:
            raise ValueError(f"Unexpected final format for {relative}")
        inputs = record.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            raise ValueError(f"Missing input lineage for {relative}")
        for item in inputs:
            if (not isinstance(item, dict) or not isinstance(item.get("artifact"), str)
                    or not isinstance(item.get("path"), str)
                    or not sha_re.fullmatch(str(item.get("sha256", "")))):
                raise ValueError(f"Invalid input lineage for {relative}")
        provenance = record.get("provenance")
        if not isinstance(provenance, dict) or not provenance:
            raise ValueError(f"Missing provenance for {relative}")
        if record["origin"] == "copied_external_asset" and not (
                provenance.get("source") and provenance.get("license")):
            raise ValueError(f"Copied asset lacks source/license: {relative}")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_db = output_dir / "db"
    shutil.copytree(source_package / "db", out_db)
    for relative in required:
        rel = Path(relative)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"Unsafe package path: {relative}")
        if not (out_db / rel.relative_to("db")).is_file():
            raise FileNotFoundError(f"Package output is missing required path: {relative}")
    actual_paths = {"db/" + path.relative_to(out_db).as_posix()
                    for path in out_db.rglob("*") if path.is_file()}
    if actual_paths != expected_paths:
        raise ValueError(f"Package database tree differs from the 21-path profile: "
                         f"missing={sorted(expected_paths - actual_paths)}, "
                         f"extra={sorted(actual_paths - expected_paths)}")
    for relative in sorted(static_zv):
        if not (out_db / Path(relative).relative_to("db")).read_bytes().startswith(b"ZV-zlib\0"):
            raise ValueError(f"Static ZV asset is not already wrapped; refusing to repack: {relative}")
    packed = []
    for relative in sorted(to_pack):
        rel = Path(relative)
        plain_path = out_db / rel.relative_to("db")
        temp_path = plain_path.with_name(plain_path.name + ".zvtmp")
        key = keys[relative]
        result = pack_sqlite(plain_path, temp_path, key)
        # Verify that the encoded container round-trips to the exact SQLite
        # input before replacing it. The temporary decoded file is always
        # removed and the key is never serialized.
        with tempfile.TemporaryDirectory(prefix="nds-forge-zv-check-") as check_dir:
            decoded = Path(check_dir) / "decoded.sqlite"
            unpack_sqlite(temp_path, decoded, key)
            if hashlib.sha256(decoded.read_bytes()).digest() != hashlib.sha256(plain_path.read_bytes()).digest():
                temp_path.unlink(missing_ok=True)
                raise ValueError(f"ZV round-trip differs from SQLite input: {relative}")
            import sqlite3
            with sqlite3.connect(f"file:{decoded}?mode=ro", uri=True) as db:
                if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    temp_path.unlink(missing_ok=True)
                    raise ValueError(f"ZV round-trip SQLite quick_check failed: {relative}")
        temp_path.replace(plain_path)
        packed.append({"path": relative, **result})

    metadata_path = out_db / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    def update(value):
        if isinstance(value, dict):
            if isinstance(value.get("filePath"), str) and "fileHash" in value:
                file_path = (out_db / value["filePath"]).resolve()
                if out_db.resolve() not in file_path.parents:
                    raise ValueError("metadata contains path outside package")
                value["fileHash"] = hashlib.sha256(file_path.read_bytes()).hexdigest()
                value["fileSize"] = file_path.stat().st_size
            for child in value.values():
                update(child)
        elif isinstance(value, list):
            for child in value:
                update(child)
    update(metadata)
    metadata_path.write_text(json.dumps(metadata, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")

    file_records = []
    for relative in sorted(required):
        rel = Path(relative)
        file_path = out_db / rel.relative_to("db")
        declared = records_by_path[relative]
        file_records.append({**declared, "path": relative, "bytes": file_path.stat().st_size,
                             "sha256": hashlib.sha256(file_path.read_bytes()).hexdigest(),
                             "validation_status": "partial" if relative in to_pack else "unvalidated"})
    report = {"schema_version": 1, "step": "container.pack", "maturity": "experimental",
              "profile_id": layout.get("profile_id"), "target": "container.pack",
              "execution_status": "completed", "validation_status": "unvalidated",
              "files": file_records, "packed_generated_containers": packed,
              "static_zv_assets_repacked": 0,
              "file_count": len(file_records),
              "limitations": ["Only the verified v9 path set is supported.",
                              "Container validity does not imply Neusoft SDK compatibility."]}
    (output_dir / "artifact-manifest.json").write_text(
        json.dumps(report, sort_keys=True, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"file_count": len(file_records), "packed_count": len(packed),
            "outputs": {item["path"]: {"bytes": item["bytes"], "sha256": item["sha256"]}
                        for item in file_records},
            "maturity": "experimental", "validation_status": "unvalidated"}
