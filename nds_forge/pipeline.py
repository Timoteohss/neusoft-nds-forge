from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import resource
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from .adapters.osm_extract import (
    OSM_CLIP_CAPABILITIES_MISSING,
    OSM_CLIP_CAPABILITIES_PRODUCED,
    clip_pbf,
)
from .bootstrap import BootstrapError, verify_bootstrap
from .config import ProjectConfig, canonical_hash
from .contracts import RegionSpec
from .contracts import StageContext as OSMStageContext
from .region import BoundaryError, resolve_region
from .registry import (
    BLOCKED,
    EXPERIMENTAL,
    IMPLEMENTED,
    PLANNED,
    Step,
    StepContext,
    all_steps,
    get_executor,
    get_step,
    register_step,
)

VALIDATION_STATES = {"unvalidated", "partial", "validated", "rejected"}
CODE_VERSION = "nds-forge-core-0.1.0"


class PipelineError(RuntimeError):
    pass


WORKSPACE_LOCK_TIMEOUT_SECONDS = 0.25

_fcntl: Any
try:  # POSIX (Linux and macOS)
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised on Windows
    _fcntl = None

try:  # Windows fallback
    _msvcrt: Any
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - exercised on POSIX
    _msvcrt = None


@contextmanager
def _workspace_lock(workspace: Path):
    """Hold an OS-level exclusive lock for one workspace run."""
    workspace.mkdir(parents=True, exist_ok=True)
    lock_path = workspace / "workspace.lock"
    handle = lock_path.open("a+", encoding="utf-8")
    acquired = False
    deadline = time.monotonic() + WORKSPACE_LOCK_TIMEOUT_SECONDS
    try:
        if _fcntl is None and _msvcrt is None:
            raise PipelineError("Workspace locking is unsupported on this platform")

        while True:
            try:
                if _fcntl is not None:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                else:  # pragma: no cover - exercised on Windows
                    handle.seek(0, os.SEEK_END)
                    if handle.tell() == 0:
                        handle.write("\0")
                        handle.flush()
                    handle.seek(0)
                    _msvcrt.locking(handle.fileno(), _msvcrt.LK_NBLCK, 1)
                acquired = True
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise PipelineError(
                        f"workspace is locked by another run: {workspace}"
                    ) from exc
                time.sleep(0.025)

        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\nhost={socket.gethostname()}\n")
        handle.flush()
        os.fsync(handle.fileno())
        yield
    finally:
        if acquired:
            if _fcntl is not None:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
            else:  # pragma: no cover - exercised on Windows
                handle.seek(0)
                _msvcrt.locking(handle.fileno(), _msvcrt.LK_UNLCK, 1)
        handle.close()


def _executor_error_summary(step_id: str, exc: Exception) -> str:
    """Return one concise, line-safe executor error for the CLI."""
    message = " ".join(str(exc).split())
    message = re.sub(
        r"(?i)\b(password|token|secret|api[_-]?key|key)\b\s*[:=]\s*\S+",
        r"\1=<redacted>", message,
    )
    message = re.sub(r"(?i)(https?://)[^/@\s]+:[^/@\s]+@", r"\1<redacted>@", message)
    if len(message) > 240:
        message = message[:237] + "..."
    details = f": {message}" if message else ""
    return f"etapa {step_id} ({type(exc).__name__}){details}"


def get_peak_rss_bytes() -> int:
    """Return peak resident set size in bytes across macOS and Linux."""
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if platform.system() == "Darwin":
        return int(usage)
    return int(usage) * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class RunHashCache:
    """Execution-local SHA-256 cache invalidated by file size or mtime changes."""

    def __init__(self) -> None:
        self._files: dict[Path, tuple[int, int, str]] = {}
        self._code_paths: tuple[Path, ...] | None = None

    def sha256(self, path: Path) -> str:
        resolved = Path(path).resolve()
        before = resolved.stat()
        metadata = (before.st_size, before.st_mtime_ns)
        cached = self._files.get(resolved)
        if cached is not None and cached[:2] == metadata:
            return cached[2]

        digest = sha256(resolved)
        after = resolved.stat()
        if (after.st_size, after.st_mtime_ns) != metadata:
            self._files.pop(resolved, None)
            raise PipelineError(f"File changed while hashing during this run: {resolved}")
        self._files[resolved] = (metadata[0], metadata[1], digest)
        return digest

    def code_hashes(self, code_root: Path) -> dict[str, str]:
        if self._code_paths is None:
            self._code_paths = tuple(sorted(code_root.rglob("*.py")))
        return {path.relative_to(code_root).as_posix(): self.sha256(path)
                for path in self._code_paths}


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _topological(target: str) -> list[Step]:
    if get_step(target) is None:
        raise PipelineError(f"Etapa desconhecida: {target}")
    result: list[Step] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(step_id: str) -> None:
        if step_id in visiting:
            raise PipelineError(f"Ciclo no DAG em {step_id}")
        if step_id in visited:
            return
        visiting.add(step_id)
        step = get_step(step_id)
        if step is None:
            raise PipelineError(f"Dependência não registrada: {step_id}")
        for dependency in step.dependencies:
            visit(dependency)
        visiting.remove(step_id)
        visited.add(step_id)
        result.append(step)

    visit(target)
    return result


def _path_value(config: ProjectConfig, value: str) -> Path:
    path = Path(value)
    return (config.path.parent / path).resolve() if not path.is_absolute() else path.resolve()


def _resolve_secret_provider(config: ProjectConfig, reference: str) -> bytes:
    """Resolve a 16-byte package key without serializing or hashing its bytes."""
    if reference.startswith("file:"):
        path = _path_value(config, reference[5:])
        try:
            with path.open("rb") as handle:
                key = handle.read(17)
        except OSError as exc:
            raise PipelineError("Secret provider file is unavailable") from exc
    elif reference.startswith("env:"):
        name = reference[4:]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise PipelineError("Secret provider environment reference is invalid")
        value = os.environ.get(name)
        if value is None:
            raise PipelineError("Secret provider environment value is unavailable")
        try:
            key = bytes.fromhex(value)
        except ValueError as exc:
            raise PipelineError("Secret provider environment value must be 32 hexadecimal characters") from exc
    else:
        raise PipelineError("Unsupported secret provider; use file:<path> or env:<name>")
    if len(key) != 16:
        raise PipelineError("Package key provider must return exactly 16 bytes")
    return key


def _external_input_paths(config: ProjectConfig, step: Step) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for field in (*step.external_inputs, *step.optional_external_inputs):
        value: object = config.raw
        for component in field.split("."):
            value = value.get(component) if isinstance(value, dict) else None
        if not isinstance(value, str) or not value:
            if field in step.optional_external_inputs:
                continue
            raise PipelineError(f"External input config field {field} is empty or missing")
        path = _path_value(config, value)
        if not path.is_file() and not path.is_dir():
            raise PipelineError(f"External input {field} does not exist: {path}")
        paths[field] = path
    return paths


def _external_input_fingerprints(config: ProjectConfig, step: Step,
                                 hash_cache: RunHashCache | None = None) -> dict[str, dict]:
    hash_file = hash_cache.sha256 if hash_cache is not None else sha256
    fingerprints = {}
    for field, path in _external_input_paths(config, step).items():
        if path.is_file():
            fingerprints[field] = {"path": str(path), "kind": "file", "bytes": path.stat().st_size,
                                   "sha256": hash_file(path)}
        else:
            files = sorted(item for item in path.rglob("*") if item.is_file())
            fingerprints[field] = {"path": str(path), "kind": "directory", "files": len(files),
                                   "bytes": sum(item.stat().st_size for item in files),
                                   "sha256": canonical_hash([
                                       {"path": str(item.relative_to(path)), "bytes": item.stat().st_size,
                                        "sha256": hash_file(item)} for item in files])}
    return fingerprints


def _region(config: ProjectConfig, allow_network: bool = False) -> dict:
    try:
        return resolve_region(config.region, config.path.parent, allow_network=allow_network)
    except BoundaryError as exc:
        raise PipelineError(f"GeoJSON de limite inválido: {exc}") from exc


def _acquire_source(config: ProjectConfig, allow_network: bool,
                    hash_file=None) -> dict:
    hash_file = hash_file or sha256
    source = config.source
    local_value = source.get("path", "")
    url = source.get("url", "")
    expected = source.get("sha256", "").lower()
    if local_value:
        path = _path_value(config, local_value)
        if not path.is_file():
            raise PipelineError(f"PBF local não existe: {path}")
        actual = hash_file(path)
        if expected and actual != expected:
            raise PipelineError(f"SHA-256 da fonte local diverge: esperado {expected}, obtido {actual}")
        return {"kind": "pbf", "origin": "local", "path": str(path), "size": path.stat().st_size,
                "sha256": actual, "expected_sha256": expected or None,
                "license": source.get("license", "unknown"),
                "attribution": source.get("attribution", "")}
    if not url:
        raise PipelineError("Sem source.path nem source.url")
    if not allow_network:
        raise PipelineError("Fonte remota bloqueada: repita com --allow-network")
    if not expected:
        raise PipelineError("Download remoto exige source.sha256 para integridade; nenhum arquivo foi baixado")
    destination_dir = config.workspace / "inputs"
    destination_dir.mkdir(parents=True, exist_ok=True)
    filename = Path(urlparse(url).path).name or "source.osm.pbf"
    destination = destination_dir / filename
    if destination.exists():
        actual = hash_file(destination)
        if actual != expected:
            raise PipelineError(f"Destino de download existente tem hash divergente: {destination}")
    else:
        temp = destination_dir / f".{filename}.{os.getpid()}.part"
        try:
            with urllib.request.urlopen(url, timeout=60) as response, temp.open("wb") as out:
                shutil.copyfileobj(response, out, length=1024 * 1024)
            actual = hash_file(temp)
            if actual != expected:
                raise PipelineError(f"Download SHA-256 diverge: esperado {expected}, obtido {actual}")
            os.replace(temp, destination)
        finally:
            temp.unlink(missing_ok=True)
    return {"kind": "pbf", "origin": "url", "url": url, "path": str(destination),
            "size": destination.stat().st_size, "sha256": expected,
            "license": source.get("license", "unknown"),
            "attribution": source.get("attribution", "")}


def _preflight_inputs(config: ProjectConfig, steps: list[Step], allow_network: bool,
                      hash_cache: RunHashCache | None = None) -> None:
    hash_file = hash_cache.sha256 if hash_cache is not None else sha256
    required = config.pipeline.get("required_capabilities", [])
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise PipelineError("pipeline.required_capabilities must be an array of capability names")
    available = {capability for step in steps for capability in step.capabilities}
    missing = set(required) - available
    if missing:
        raise PipelineError("Requested capabilities are not implemented in this build path: " +
                            ", ".join(sorted(missing)))
    for step in steps:
        _external_input_paths(config, step)
    step_ids = {step.id for step in steps}
    if "poi.compile" in step_ids:
        from .layers.steps import validate_poi_profile
        try:
            validate_poi_profile(config.neusoft.get("poi", {}))
        except ValueError as exc:
            raise PipelineError(str(exc)) from exc
    if "region.resolve" in step_ids:
        _region(config, allow_network=allow_network)
    if "source.acquire" not in step_ids:
        return
    source = config.source
    if source.get("path"):
        path = _path_value(config, source["path"])
        if not path.is_file():
            raise PipelineError(f"PBF local não existe: {path}")
        expected = source.get("sha256", "").lower()
        actual = hash_file(path)
        if expected and expected != actual:
            raise PipelineError(f"SHA-256 da fonte local diverge: esperado {expected}, obtido {actual}")
    else:
        if not allow_network:
            raise PipelineError("Fonte remota bloqueada: repita com --allow-network; nenhuma saída foi criada")
        if not source.get("sha256"):
            raise PipelineError("Download remoto exige source.sha256; nenhuma saída foi criada")


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def _execute_region(context: StepContext) -> dict:
    payload = _region(context.config, allow_network=context.allow_network)
    _write_json(context.output_dir / "region.json", payload)
    return {"region": payload}


def _execute_source(context: StepContext) -> dict:
    payload = _acquire_source(context.config, context.allow_network, context.hash_file)
    _write_json(context.output_dir / "source.json", payload)
    return {"source": payload}


def _execute_source_verify(context: StepContext) -> dict:
    source_path = context.dependency_outputs["source.acquire"] / "source.json"
    region_path = context.dependency_outputs["region.resolve"] / "region.json"
    source_payload = json.loads(source_path.read_text(encoding="utf-8"))
    region_payload = json.loads(region_path.read_text(encoding="utf-8"))
    result = {"source_sha256": source_payload["sha256"],
              "region_id": region_payload["id"],
              "spatial_filter": "not_applied",
              "warning": "source and region are identified; clipping is a separate step"}
    _write_json(context.output_dir / "source-report.json", result)
    return result


def _execute_osm_clip(context: StepContext) -> dict:
    source_metadata = json.loads((context.dependency_outputs["source.acquire"] / "source.json").read_text(encoding="utf-8"))
    region_metadata = json.loads((context.dependency_outputs["region.resolve"] / "region.json").read_text(encoding="utf-8"))
    bbox_value = region_metadata.get("bbox")
    geometry = region_metadata.get("geometry_geojson")
    region = RegionSpec(
        id=region_metadata["id"], name=region_metadata.get("name", ""),
        bbox=tuple(bbox_value) if bbox_value is not None else None,
        geometry_geojson=geometry,
        resolved=region_metadata.get("boundary_source") != "osm_relation_reference_only",
    )
    result = clip_pbf(OSMStageContext(
        source_path=Path(source_metadata["path"]),
        region=region,
        output_dir=context.output_dir,
        license=source_metadata.get("license", "unknown"),
        attribution=source_metadata.get("attribution", ""),
    ), policy=context.config.raw.get("osm", {}).get("highway_values"))
    return {"counts": result["counts"], "outputs": result["outputs"],
            "capabilities_produced": result["capabilities_produced"],
            "capabilities_missing": result["capabilities_missing"],
            "maturity": result["maturity"]}


def _execute_bootstrap_verify(context: StepContext) -> dict:
    profile_id = context.config.neusoft.get("profile_id")
    if not isinstance(profile_id, str) or not profile_id:
        raise PipelineError("neusoft.profile_id é obrigatório para validar bootstrap")
    directory = context.external_inputs["neusoft.bootstrap_dir"]
    try:
        result = verify_bootstrap(directory, profile_id)
    except BootstrapError as exc:
        raise PipelineError(str(exc)) from exc
    _write_json(context.output_dir / "bootstrap-report.json", result)
    return {"profile_id": profile_id, "manifest_sha256": result["manifest_sha256"],
            "count": result["count"], "roles": sorted({item["role"] for item in result["files"]})}


def plan(config: ProjectConfig, target: str) -> dict:
    steps = _topological(target)
    return {"project_id": config.project_id, "target": target,
            "workspace": str(config.workspace), "steps": [
                {"id": step.id, "status": step.status, "dependencies": list(step.dependencies),
                 "inputs": list(step.inputs), "outputs": list(step.outputs),
                 "capabilities": list(step.capabilities),
                 "capabilities_missing": list(step.capabilities_missing),
                 "description": step.description,
                 "cacheable": step.cacheable,
                 "action": ("run" if step.status == IMPLEMENTED else
                            "experimental_opt_in" if step.status == EXPERIMENTAL else "blocked")}
                for step in steps],
            "side_effects": "none", "network": "not accessed", "validation": "not performed"}


def _manifest_path(config: ProjectConfig, step_id: str) -> Path:
    return config.workspace / "state" / f"{step_id.replace('.', '__')}.json"


def _failure_path(config: ProjectConfig, step_id: str) -> Path:
    return config.workspace / "state" / f"{step_id.replace('.', '__')}.failure.json"


def _read_manifest(config: ProjectConfig, step_id: str) -> dict | None:
    path = _manifest_path(config, step_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _output_hashes(directory: Path) -> dict[str, str]:
    return {str(path.relative_to(directory)): sha256(path)
            for path in sorted(directory.rglob("*")) if path.is_file()}


def _current_fingerprint(config: ProjectConfig, step: Step, dependency_manifests: dict,
                         hash_cache: RunHashCache | None = None) -> str:
    config_data = config.fingerprint_data()
    # Executors are thin wrappers around codecs/adapters in subpackages. Hash
    # all shipped Python sources so a change in an imported implementation
    # cannot silently reuse artifacts built with older code. Relative paths
    # avoid collisions between e.g. several __init__.py files.
    code_root = Path(__file__).parent
    hash_file = hash_cache.sha256 if hash_cache is not None else sha256
    code_hashes = (hash_cache.code_hashes(code_root) if hash_cache is not None else
                   {path.relative_to(code_root).as_posix(): hash_file(path)
                    for path in sorted(code_root.rglob("*.py"))})
    executor = get_executor(step.id)
    if executor is not None:
        module = __import__(executor.__module__, fromlist=["__file__"])
        module_file = getattr(module, "__file__", None)
        if module_file and Path(module_file).is_file():
            code_hashes[f"executor:{step.id}"] = hash_file(Path(module_file))
        elif getattr(executor, "__code__", None) is not None:
            code_hashes[f"executor:{step.id}"] = canonical_hash({
                "bytecode": executor.__code__.co_code.hex(),
                "constants": repr(executor.__code__.co_consts),
                "names": executor.__code__.co_names,
            })
    external_inputs = _external_input_fingerprints(config, step, hash_cache)
    if step.id == "source.acquire" and config.source.get("path"):
        path = _path_value(config, config.source["path"])
        external_inputs["source.path"] = {"path": str(path), "kind": "file",
                                           "bytes": path.stat().st_size if path.is_file() else None,
                                           "sha256": hash_file(path) if path.is_file() else "missing"}
    elif step.id == "source.acquire" and config.source.get("url"):
        prior_source = _read_manifest(config, "source.acquire")
        prior_path = None
        if prior_source:
            prior_path = prior_source.get("result", {}).get("source", {}).get("path")
        if prior_path:
            path = Path(prior_path)
            external_inputs["downloaded_source"] = {"path": str(path), "kind": "file",
                                                     "bytes": path.stat().st_size if path.is_file() else None,
                                                     "sha256": hash_file(path) if path.is_file() else "missing"}
    if step.id == "region.resolve" and config.region.get("source") == "boundary_geojson":
        path = _path_value(config, config.region["boundary_geojson"])
        external_inputs["region.boundary_geojson"] = {"path": str(path), "kind": "file",
                                                       "bytes": path.stat().st_size if path.is_file() else None,
                                                       "sha256": hash_file(path) if path.is_file() else "missing"}
    return canonical_hash({"code": CODE_VERSION, "step": step.id,
                          "code_hashes": code_hashes, "config": config_data,
                          "step_definition": {"status": step.status,
                                              "dependencies": step.dependencies,
                                              "output": step.output,
                                              "inputs": step.inputs,
                                              "outputs": step.outputs,
                                              "capabilities": step.capabilities,
                                              "capabilities_missing": step.capabilities_missing,
                                              "external_inputs": step.external_inputs,
                                              "optional_external_inputs": step.optional_external_inputs,
                                              "cacheable": step.cacheable},
                          "external_inputs": external_inputs, "dependencies": dependency_manifests})


def _execution_digest(manifest: dict) -> str:
    return canonical_hash({"step_id": manifest.get("step_id"),
                           "fingerprint": manifest.get("fingerprint"),
                           "output_hashes": manifest.get("output_hashes")})


def _cache_valid(config: ProjectConfig, step: Step, fingerprint: str) -> bool:
    try:
        manifest = _read_manifest(config, step.id)
        output = config.workspace / "artifacts" / step.output
        if not manifest or manifest.get("fingerprint") != fingerprint or not output.is_dir():
            return False
        return manifest.get("output_hashes") == _output_hashes(output)
    except (OSError, PipelineError):
        return False


def _commit_directory(staging: Path, destination: Path, publish_manifest=None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = destination.with_name(f".{destination.name}.previous-{os.getpid()}")
    if backup.exists():
        shutil.rmtree(backup)
    had_old = destination.exists()
    if had_old:
        os.replace(destination, backup)
    try:
        os.replace(staging, destination)
        if publish_manifest is not None:
            publish_manifest()
    except Exception:
        if destination.exists():
            shutil.rmtree(destination)
        if had_old and backup.exists():
            os.replace(backup, destination)
        raise
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)


def _progress(enabled: bool, message: str) -> None:
    if enabled:
        print(f"[nds-forge] {message}", file=sys.stderr, flush=True)


@contextmanager
def _heartbeat(enabled: bool, label: str, interval: float = 15.0):
    """Show that a long-running step is still alive."""
    if not enabled:
        yield
        return
    stop = threading.Event()
    started = time.monotonic()

    def beat() -> None:
        while not stop.wait(interval):
            _progress(True, f"{label}: still running ({time.monotonic() - started:.0f}s)")

    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1.0)


def _run_locked(config: ProjectConfig, target: str, steps: list[Step], hash_cache: RunHashCache,
                allow_network: bool = False, allow_experimental: bool = False,
                resume: bool = False, progress: bool = False) -> dict:
    results: list[dict[str, Any]] = []
    completed_manifests: dict[str, str] = {}
    total = len(steps)
    for index, step in enumerate(steps, 1):
        dep_manifests = {dep: completed_manifests[dep] for dep in step.dependencies}
        fingerprint = _current_fingerprint(config, step, dep_manifests, hash_cache)
        output = config.workspace / "artifacts" / step.output
        if resume and step.cacheable and _cache_valid(config, step, fingerprint):
            prior = cast(dict, _read_manifest(config, step.id))
            completed_manifests[step.id] = _execution_digest(prior)
            _failure_path(config, step.id).unlink(missing_ok=True)
            resources = prior.get("resource_metrics", {})
            _progress(progress, f"[{index}/{total}] {step.id}: cached"
                                f" ({resources.get('duration_seconds', 0)}s)")
            results.append({"id": step.id, "status": "cached", "fingerprint": fingerprint})
            continue
        executor = get_executor(step.id)
        if executor is None:
            raise PipelineError(f"Etapa {step.id} não tem executor registrado")
        dependency_outputs = {
            dep: config.workspace / "artifacts" / cast(Step, get_step(dep)).output
            for dep in step.dependencies
        }
        config.workspace.mkdir(parents=True, exist_ok=True)
        staging_root = config.workspace / ".staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f"{step.id.replace('.', '-')}-", dir=staging_root))
        _progress(progress, f"[{index}/{total}] {step.id}: running ({step.status})")
        try:
            start_time = time.perf_counter()
            context = StepContext(config=config, output_dir=staging,
                                  dependency_outputs=dependency_outputs,
                                  external_inputs=_external_input_paths(config, step),
                                  allow_network=allow_network,
                                  allow_experimental=allow_experimental,
                                  resolve_secret=lambda reference: _resolve_secret_provider(config, reference),
                                  hash_file=hash_cache.sha256)
            with _heartbeat(progress, f"[{index}/{total}] {step.id}"):
                try:
                    result_data = executor(context)
                except Exception as exc:
                    raise PipelineError(_executor_error_summary(step.id, exc)) from exc
            duration_s = time.perf_counter() - start_time
            if not isinstance(result_data, dict):
                raise PipelineError(f"Executor {step.id} deve retornar um objeto JSON")
            hashes = _output_hashes(staging)

            in_bytes = 0
            for path in list(context.external_inputs.values()) + list(context.dependency_outputs.values()):
                if path.is_file():
                    in_bytes += path.stat().st_size
                elif path.is_dir():
                    in_bytes += sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            out_bytes = sum(f.stat().st_size for f in staging.rglob("*") if f.is_file())

            resource_metrics = {
                "duration_seconds": round(duration_s, 4),
                "peak_rss_bytes": get_peak_rss_bytes(),
                "input_bytes": in_bytes,
                "output_bytes": out_bytes,
            }

            manifest = {"schema_version": 1, "step_id": step.id, "execution_status": "completed",
                        "validation_status": "unvalidated", "fingerprint": fingerprint,
                        "dependencies": dep_manifests, "output_hashes": hashes,
                        "result": result_data, "code_version": CODE_VERSION,
                        "completed_at_utc": datetime.now(UTC).isoformat(),
                        "resource_metrics": resource_metrics}
            manifest_path = _manifest_path(config, step.id)
            staged_manifest = manifest_path.with_name(
                f".{manifest_path.name}.pending-{os.getpid()}-{time.time_ns()}")
            _atomic_json(staged_manifest, manifest)
            try:
                _commit_directory(
                    staging,
                    output,
                    publish_manifest=lambda: os.replace(staged_manifest, manifest_path),  # noqa: B023 -- Callback is invoked synchronously by _commit_directory before this loop advances, so capture is stable
                )
            finally:
                staged_manifest.unlink(missing_ok=True)
            try:
                _failure_path(config, step.id).unlink(missing_ok=True)
            except OSError:
                # The completed manifest's timestamp makes any stale failure
                # record non-authoritative; cleanup must not undo a publish.
                pass
            completed_manifests[step.id] = _execution_digest(manifest)
            _progress(progress, f"[{index}/{total}] {step.id}: completed in "
                                f"{duration_s:.2f}s ({out_bytes / 1_000_000:.1f} MB)")
            results.append({"id": step.id, "status": "completed", "fingerprint": fingerprint,
                            "output_hashes": hashes, "resource_metrics": resource_metrics})
        except Exception as exc:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            root_cause = exc.__cause__ if isinstance(exc, PipelineError) and exc.__cause__ else exc
            _atomic_json(_failure_path(config, step.id), {
                "schema_version": 1,
                "step_id": step.id,
                "execution_status": "failed",
                "validation_status": "unvalidated",
                "fingerprint": fingerprint,
                "error_type": type(root_cause).__name__,
                "failed_at_utc": datetime.now(UTC).isoformat(),
            })
            _progress(progress, f"[{index}/{total}] {step.id}: failed ({type(root_cause).__name__})")
            raise
    return {"project_id": config.project_id, "target": target, "results": results,
            "overall_validation": "unvalidated"}


def run(config: ProjectConfig, target: str, allow_network: bool = False,
        allow_experimental: bool = False, resume: bool = False,
        progress: bool = False) -> dict:
    # Keep read-only preflight side-effect free, then lock before state, staging,
    # output, manifest, or failure-record mutations begin.
    steps = _topological(target)
    for step in steps:
        if step.status in {PLANNED, BLOCKED}:
            raise PipelineError(f"Etapa {step.id} é {step.status}; execução bloqueada antes de iniciar o DAG")
    for step in steps:
        if step.status == EXPERIMENTAL and not allow_experimental:
            raise PipelineError(f"Etapa {step.id} é experimental; exige --allow-experimental")
    hash_cache = RunHashCache()
    _progress(progress, f"preflight: {len(steps)} etapas")
    _preflight_inputs(config, steps, allow_network, hash_cache)
    with _workspace_lock(config.workspace):
        return _run_locked(config, target, steps, hash_cache, allow_network,
                           allow_experimental, resume, progress)


def status(config: ProjectConfig) -> dict:
    steps = []
    for step in all_steps():
        manifest = _read_manifest(config, step.id)
        failure_path = _failure_path(config, step.id)
        failure = None
        if failure_path.exists():
            try:
                failure = json.loads(failure_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                failure = {"execution_status": "failed"}
        manifest_completed_at = manifest.get("completed_at_utc", "") if manifest else ""
        failed = bool(failure and (not manifest or
                       cast(dict, failure).get("failed_at_utc", "") >= manifest_completed_at))
        failure_doc = cast(dict | None, failure)
        steps.append({"id": step.id, "implementation": step.status,
                      "execution": "failed" if failed else (manifest.get("execution_status", "not_run") if manifest else "not_run"),
                      "validation": "unvalidated" if failed else (manifest.get("validation_status", "unvalidated") if manifest else "unvalidated"),
                      "fingerprint": failure_doc.get("fingerprint") if failed and failure_doc else (manifest.get("fingerprint") if manifest else None),
                      "outputs_intact": False if failed else (_cache_valid(config, step, manifest.get("fingerprint", "")) if manifest else False)})
    return {"project_id": config.project_id, "steps": steps}


def validate_step(config: ProjectConfig, step_id: str) -> dict:
    step = get_step(step_id)
    if step is None:
        raise PipelineError(f"Etapa desconhecida: {step_id}")
    manifest = _read_manifest(config, step_id)
    if not manifest or manifest.get("execution_status") != "completed":
        raise PipelineError(f"Etapa {step_id} não foi executada")
    failure_path = _failure_path(config, step_id)
    if failure_path.exists():
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
        if failure.get("failed_at_utc", "") >= manifest.get("completed_at_utc", ""):
            raise PipelineError(f"A execução mais recente de {step_id} falhou; valide novamente somente após um run completo")
    output = config.workspace / "artifacts" / step.output
    actual_hashes = _output_hashes(output) if output.is_dir() else {}
    if actual_hashes != manifest.get("output_hashes"):
        state, details = "rejected", "output hashes do not match execution manifest"
    elif step_id == "region.resolve":
        region = json.loads((output / "region.json").read_text())
        state = "validated" if "bbox" in region and region.get("boundary_source") != "osm_relation_reference_only" else "partial"
        details = "region geometry/extent resolved; this does not validate map-data coverage"
    elif step_id == "source.acquire":
        source = json.loads((output / "source.json").read_text())
        current = Path(source["path"])
        state = "validated" if current.is_file() and sha256(current) == source["sha256"] else "rejected"
        details = "source file hash rechecked; no OSM parse or map coverage validated"
    elif step_id == "source.verify":
        state, details = "partial", (
            "source integrity and region reference checked; osm.clip is a separate "
            "experimental step; no OSM parse or map coverage validated"
        )
    else:
        state, details = "partial", "generic output integrity only"
    manifest["validation_status"] = state
    manifest["validation"] = {"details": details, "checked_at_utc": datetime.now(UTC).isoformat()}
    _atomic_json(_manifest_path(config, step_id), manifest)
    return {"step_id": step_id, "validation_status": state, "details": details}


def _register_builtin_steps() -> None:
    declarations = (
        (Step("region.resolve", IMPLEMENTED, (), "region",
              "Validar e normalizar bbox ou polígono fornecido",
              inputs=("config.region",), outputs=("region.json",), capabilities=("region_extent",)),
         _execute_region),
        (Step("source.acquire", IMPLEMENTED, (), "source",
              "Verificar fonte PBF local ou baixar URL com opt-in e SHA esperado",
              inputs=("config.source",), outputs=("source.json",), capabilities=("pbf_file", "sha256")),
         _execute_source),
        (Step("source.verify", IMPLEMENTED, ("source.acquire", "region.resolve"), "source-report",
              "Registrar integridade, licença e relação espacial de entrada",
              inputs=("source.acquire/source.json", "region.resolve/region.json"),
              outputs=("source-report.json",), capabilities=("provenance",)),
         _execute_source_verify),
        (Step("osm.clip", EXPERIMENTAL, ("source.verify", "source.acquire", "region.resolve"), "clipped-osm",
              "Recortar entidades OSM pelo limite da região",
              inputs=("source.acquire/source.json", "region.resolve/region.json"),
              outputs=("roads.geojson", "clip-report.json", "osm-dataset.json"),
              capabilities=OSM_CLIP_CAPABILITIES_PRODUCED,
              capabilities_missing=OSM_CLIP_CAPABILITIES_MISSING),
         _execute_osm_clip),
        (Step("bootstrap.verify", IMPLEMENTED, (), "bootstrap",
              "Validar inventário local de bootstrap e hashes de assets explicitamente declarados",
              inputs=("neusoft.profile_id", "neusoft.bootstrap_dir"),
              outputs=("bootstrap-report.json",), capabilities=("bootstrap_inventory_verified",),
              external_inputs=("neusoft.bootstrap_dir",)),
         _execute_bootstrap_verify),
        (Step("osm.normalize", PLANNED, ("osm.clip",), "normalized-osm",
              "Normalizar OSM para modelo canônico tipado",
              inputs=("osm.clip/roads.geojson",), outputs=("normalized-osm.jsonl", "normalization-report.json"),
              capabilities=("normalized_roads", "nodes", "relations", "addresses", "poi", "restrictions")), None),
        (Step("geometry.bmd", PLANNED, ("osm.normalize",), "geometry",
              "Compilar geometria viária BMD/3D", inputs=("osm.normalize/normalized-osm.jsonl",),
              outputs=("BMD.NDS",), capabilities=("bmd_geometry",)), None),
        (Step("names.compile", PLANNED, ("osm.normalize",), "names",
              "Compilar nomes, índices e FTS", inputs=("osm.normalize/normalized-osm.jsonl",),
              outputs=("NAME.NDS", "FTS.NDS"), capabilities=("street_names", "search")), None),
        (Step("poi.compile", PLANNED, ("osm.normalize",), "poi",
              "Compilar POI e categorias", inputs=("osm.normalize/normalized-osm.jsonl",),
              outputs=("POI.NDS",), capabilities=("poi_catalog",)), None),
        (Step("address.compile", PLANNED, ("osm.normalize",), "address",
              "Compilar endereços e interpolação", inputs=("osm.normalize/normalized-osm.jsonl",),
              outputs=("address tables",), capabilities=("address_search",)), None),
        (Step("routing.main", PLANNED, ("source.acquire", "region.resolve"), "routing-main",
              "Compilar grafo ROUTE principal diretamente da fonte OSM e limite declarado",
              inputs=("source.acquire/source.json", "region.resolve/region.json"),
              outputs=("ROUTE.NDS main tiles",), capabilities=("route_graph",)), None),
        (Step("routing.aux", PLANNED, ("routing.main",), "routing-aux",
              "Compilar geometrias e referências ROUTE-AUX", inputs=("routing.main",),
              outputs=("ROUTE.NDS aux tiles",), capabilities=("route_geometry", "turn_rules")), None),
        (Step("catalogs.build", PLANNED,
              ("geometry.bmd", "names.compile", "poi.compile", "routing.main", "routing.aux"),
              "catalogs", "Construir catálogos de cobertura e índices",
              inputs=("compiled layers",), outputs=("catalogs/regions/coverage",), capabilities=("catalogs",)), None),
        (Step("package.assemble", PLANNED, ("catalogs.build", "bootstrap.verify"), "package",
              "Montar e empacotar saída para perfil Neusoft declarado",
              inputs=("catalogs.build",), outputs=("package manifest", "package files"), capabilities=("nds_package",)), None),
        (Step("container.pack", PLANNED, ("package.assemble",), "package-final",
              "Aplicar os containers exigidos pelo formato alvo e publicar os 21 caminhos app-readable",
              inputs=("package.assemble", "neusoft.container_keys"),
              outputs=("21 app-readable target paths", "artifact-manifest.json"),
              capabilities=("app_readable_package",), cacheable=False), None),
        (Step("validation.offline", PLANNED, ("container.pack",), "validation-offline",
              "Validar estrutura, hashes e consistência offline",
              inputs=("container.pack",), outputs=("offline-validation.json",), capabilities=("offline_checked",)), None),
        (Step("validation.native", PLANNED, ("validation.offline",), "validation-native",
              "Validar leitura com parser nativo específico", inputs=("validation.offline",),
              outputs=("native-validation.json",), capabilities=("native_parser_accepted",)), None),
        (Step("validation.device", PLANNED, ("validation.native",), "validation-device",
              "Validar SDK/dispositivo com opt-in explícito", inputs=("validation.native",),
              outputs=("device-validation.json",), capabilities=("sdk_behavior_observed",)), None),
    )
    for step, executor in declarations:
        register_step(step, executor)


_register_builtin_steps()
from .layers.steps import register_layer_steps

register_layer_steps()
from .layers.catalogs import register_catalog_step
from .layers.package_steps import register_package_step

register_catalog_step()
register_package_step()
STEPS = all_steps()
