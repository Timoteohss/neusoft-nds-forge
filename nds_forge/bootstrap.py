"""Validation helpers for explicitly supplied, non-generated profile inputs."""
from __future__ import annotations

import hashlib
import os
import json
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

class BootstrapError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_format(path: Path, format_name: str) -> None:
    formats = {"zv_zlib", "sqlite3", "text", "ndk_text", "json"}
    if format_name not in formats:
        raise BootstrapError(f"Formato bootstrap não suportado: {format_name}")
    with path.open("rb") as handle:
        prefix = handle.read(32)
    if format_name == "zv_zlib" and not prefix.startswith(b"ZV-zlib\0"):
        raise BootstrapError("Header ZV-zlib ausente no asset de bootstrap")
    if format_name == "sqlite3" and not prefix.startswith(b"SQLite format 3\0"):
        raise BootstrapError("Header SQLite ausente no asset de bootstrap")
    if format_name == "ndk_text" and not prefix.startswith(b"# Neusoft NDK 1."):
        raise BootstrapError("Header NDK esperado ausente no asset de bootstrap")
    if format_name in {"text", "ndk_text"}:
        try:
            path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise BootstrapError("Asset de texto não é UTF-8 válido") from exc
    if format_name == "json":
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BootstrapError("Asset JSON inválido") from exc


def _safe_archive_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise BootstrapError(f"Caminho inseguro no arquivo: {value}")
    return path


def _verify_bundle_manifest(root: Path) -> dict:
    """Verify archive members against its public manifest before publishing it."""
    path = root / "manifest.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BootstrapError(f"Manifest ausente/inválido no bundle: {exc}") from exc
    records = document.get("files") if isinstance(document, dict) else None
    if not isinstance(records, list) or not records:
        raise BootstrapError("Bundle manifest deve listar arquivos")
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            raise BootstrapError("Registro inválido no bundle manifest")
        relative = record.get("path")
        if not isinstance(relative, str) or not relative:
            raise BootstrapError("Registro do bundle sem path")
        safe = _safe_archive_path(relative)
        if safe in seen:
            raise BootstrapError(f"Path duplicado no bundle manifest: {relative}")
        seen.add(safe)
        file_path = root / safe
        if not file_path.is_file():
            raise BootstrapError(f"Arquivo declarado ausente: {relative}")
        expected_bytes = record.get("bytes")
        expected_sha = record.get("sha256")
        if not isinstance(expected_bytes, int) or file_path.stat().st_size != expected_bytes:
            raise BootstrapError(f"Tamanho diverge para {relative}")
        if not isinstance(expected_sha, str) or _sha256(file_path) != expected_sha.lower():
            raise BootstrapError(f"SHA-256 diverge para {relative}")
    return document


def fetch_bundles(config_path: Path, bundles: list[dict], allow_network: bool) -> dict:
    """Fetch SHA-pinned tar.gz bundles to fresh, config-relative directories.

    Nothing is downloaded or created unless ``allow_network`` is true. Archive
    extraction is staged and rejects links, path traversal, duplicate members,
    and destinations that already exist.
    """
    config_dir = config_path.resolve().parent
    if not allow_network:
        # Permit a fully offline run when every pinned bundle is already present
        # and verified. Validate all destinations before any mutation.
        for item in bundles:
            if not isinstance(item, dict):
                raise BootstrapError("Cada downloads.bundles precisa ser uma tabela")
            destination_value = item.get("destination")
            expected = item.get("sha256")
            if not isinstance(destination_value, str) or not isinstance(expected, str):
                raise BootstrapError("Bundle exige destination e sha256")
            relative = _safe_archive_path(destination_value)
            destination = (config_dir / relative).resolve()
            if config_dir not in destination.parents:
                raise BootstrapError("Destino do bundle deve ficar sob o diretório do perfil")
            marker = destination / ".nds-forge-archive-sha256"
            if not marker.is_file() or marker.read_text(encoding="ascii").strip() != expected.lower():
                raise BootstrapError("Bundle ausente ou diferente; use --allow-network para baixar inputs")
            _verify_bundle_manifest(destination)
    results = []
    downloaded = False
    for item in bundles:
        if not isinstance(item, dict):
            raise BootstrapError("Cada downloads.bundles precisa ser uma tabela")
        url = item.get("url")
        expected = item.get("sha256")
        destination_value = item.get("destination")
        bundle_id = item.get("id", "bundle")
        if not all(isinstance(x, str) and x for x in (url, expected, destination_value)):
            raise BootstrapError("Bundle exige id, url, sha256 e destination")
        if len(expected) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in expected):
            raise BootstrapError(f"SHA-256 inválido para bundle {bundle_id}")
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise BootstrapError("Bundles devem usar URL HTTPS")
        destination_rel = _safe_archive_path(destination_value)
        destination = (config_dir / destination_rel).resolve()
        if config_dir not in destination.parents:
            raise BootstrapError("Destino do bundle deve ficar sob o diretório do perfil")
        if destination.exists():
            marker = destination / ".nds-forge-archive-sha256"
            if marker.is_file() and marker.read_text(encoding="ascii").strip() == expected.lower():
                _verify_bundle_manifest(destination)
                results.append({"id": bundle_id, "destination": str(destination),
                                "sha256": expected.lower(), "reused": True})
                continue
            raise BootstrapError(f"Destino existe e não corresponde ao bundle pinado: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.stage-", dir=destination.parent))
        archive = stage.parent / f".{destination.name}.{os.getpid()}.download"
        try:
            digest = hashlib.sha256()
            try:
                with urllib.request.urlopen(url, timeout=120) as response, archive.open("wb") as output:
                    while chunk := response.read(1024 * 1024):
                        digest.update(chunk)
                        output.write(chunk)
            except OSError as exc:
                raise BootstrapError(f"Download falhou para bundle {bundle_id}") from exc
            actual = digest.hexdigest()
            if actual != expected.lower():
                raise BootstrapError(f"SHA-256 divergente para bundle {bundle_id}")
            seen_members = set()
            try:
                with tarfile.open(archive, "r:gz") as tar:
                    members = tar.getmembers()
                    for member in members:
                        rel = _safe_archive_path(member.name)
                        if rel in seen_members:
                            raise BootstrapError(f"Membro duplicado no arquivo: {member.name}")
                        seen_members.add(rel)
                        if not (member.isfile() or member.isdir()):
                            raise BootstrapError(f"Tipo de membro não permitido: {member.name}")
                        target = stage / rel
                        if member.isdir():
                            target.mkdir(parents=True, exist_ok=True)
                            continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        source = tar.extractfile(member)
                        if source is None:
                            raise BootstrapError(f"Membro ilegível: {member.name}")
                        with source, target.open("xb") as out:
                            shutil.copyfileobj(source, out, 1024 * 1024)
            except (tarfile.TarError, OSError) as exc:
                raise BootstrapError(f"Arquivo tar inválido: {bundle_id}") from exc
            _verify_bundle_manifest(stage)
            (stage / ".nds-forge-archive-sha256").write_text(actual + "\n", encoding="ascii")
            os.replace(stage, destination)
            downloaded = True
            results.append({"id": bundle_id, "destination": str(destination),
                            "sha256": actual, "reused": False})
        finally:
            archive.unlink(missing_ok=True)
            if stage.exists():
                shutil.rmtree(stage)
    return {"bundles": results, "network_used": downloaded,
            "warning": "inputs only; bundle verification does not validate a generated map"}


def verify_bootstrap(directory: Path, profile_id: str) -> dict:
    """Verify a local bootstrap manifest and all files it declares.

    The manifest is an inventory, not a package seed. Callers must use
    ``static_asset`` files only as explicit copied assets and may use
    ``schema_seed`` files only as a source of documented schema definitions.
    """
    root = directory.resolve()
    manifest_path = root / "manifest.json"
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BootstrapError(f"Bootstrap manifest inválido: {manifest_path}: {exc}") from exc
    if not isinstance(document, dict):
        raise BootstrapError("Bootstrap manifest root deve ser objeto")
    if document.get("schema_version") != 1 or document.get("profile_id") != profile_id:
        raise BootstrapError("Bootstrap schema_version/profile_id não corresponde à configuração")
    files = document.get("files")
    if not isinstance(files, list):
        raise BootstrapError("Bootstrap files deve ser uma lista")
    seen_names: set[str] = set()
    seen_outputs: set[str] = set()
    verified = []
    for record in files:
        if not isinstance(record, dict):
            raise BootstrapError("Registro de bootstrap deve ser objeto")
        logical_name = record.get("logical_name")
        output_path = record.get("output_path")
        relative_path = record.get("path")
        role = record.get("role")
        format_name = record.get("format")
        if not all(isinstance(value, str) and value for value in
                   (logical_name, relative_path, role, format_name)):
            raise BootstrapError("Bootstrap exige logical_name, path, format e role")
        if output_path is not None and (not isinstance(output_path, str) or not output_path):
            raise BootstrapError("Bootstrap output_path deve ser string relativa ou null")
        if logical_name in seen_names or (output_path and output_path in seen_outputs):
            raise BootstrapError("Bootstrap contém logical_name ou output_path duplicado")
        seen_names.add(logical_name)
        if output_path:
            seen_outputs.add(output_path)
            out = Path(output_path)
            if out.is_absolute() or ".." in out.parts or not out.parts:
                raise BootstrapError(f"output_path inseguro no bootstrap: {output_path}")
        src = (root / relative_path).resolve()
        if role not in {"static_asset", "schema_seed", "reference_only"}:
            raise BootstrapError(f"role de bootstrap não suportado: {role}")
        if role == "static_asset" and not output_path:
            raise BootstrapError("static_asset precisa de output_path explícito")
        if role != "static_asset" and output_path:
            raise BootstrapError("schema/reference inputs não podem ser copiados como package outputs")
        if root not in src.parents or not src.is_file():
            raise BootstrapError(f"Arquivo de bootstrap ausente ou fora do diretório: {relative_path}")
        expected_size = record.get("bytes")
        expected_hash = record.get("sha256")
        if not isinstance(expected_size, int) or expected_size != src.stat().st_size:
            raise BootstrapError(f"Tamanho de bootstrap diverge: {logical_name}")
        if not isinstance(expected_hash, str) or _sha256(src) != expected_hash.lower():
            raise BootstrapError(f"SHA-256 de bootstrap diverge: {logical_name}")
        _verify_format(src, format_name)
        provenance = record.get("provenance")
        if (not isinstance(provenance, dict) or
                not isinstance(provenance.get("source"), str) or not provenance.get("source") or
                not isinstance(provenance.get("license"), str) or not provenance.get("license")):
            raise BootstrapError(f"Proveniência/licença ausente: {logical_name}")
        verified.append({"logical_name": logical_name, "output_path": output_path,
                         "bytes": expected_size, "sha256": expected_hash.lower(),
                         "role": role, "format": format_name, "provenance": provenance})
    return {"profile_id": profile_id, "manifest_sha256": _sha256(manifest_path),
            "files": verified, "count": len(verified),
            "usage_warning": "verified inventory only; no package asset has been copied"}
