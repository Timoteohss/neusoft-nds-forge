"""Native parser runner and external oracle runner (Issue #37 / [6.2]).

Executes external native probes and parsers (e.g., read_route_tile / libNdsLib wrappers)
against candidate tile blobs:
1. Pure subprocess execution via argv list (shell=False) with strict timeouts.
2. Binds runs to candidate artifact SHA-256 and native runtime executable SHA-256.
3. Detects and handles timeouts, missing runtimes, and non-zero exit codes.
4. Enforces fundamental boundary rule:
   PARSER_PASS does NOT imply ROUTE_PASS or SDK route calculation acceptance.
   No proprietary binaries checked into the repository.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
from pathlib import Path
import subprocess
from typing import Any


class NativeRunStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    TIMEOUT = "timeout"
    MISSING_RUNTIME = "missing_runtime"
    HASH_MISMATCH = "hash_mismatch"


class MissingNativeRuntimeError(FileNotFoundError):
    """Raised when the specified native parser executable does not exist."""


class NativeArtifactHashMismatchError(ValueError):
    """Raised when candidate artifact bytes do not match expected hash."""


class NativeRuntimeHashMismatchError(ValueError):
    """A runtime pin mismatch, carrying the digest observed before execution."""

    def __init__(self, expected_sha256: str, actual_sha256: str) -> None:
        self.actual_sha256 = actual_sha256
        super().__init__(f"Native runtime SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class NativeProbeResult:
    """Outcome of running an external native parser probe on a candidate tile."""
    tile_id: int
    status: NativeRunStatus
    runtime_path: str
    runtime_sha256: str
    artifact_path: str
    artifact_sha256: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    parsed_items_count: int = 0  # Unknown until a structured output parser exists.
    route_pass: bool = False
    boundary_note: str = (
        "PARSER_PASS is solely a binary format decoding check and does NOT "
        "equal SDK route calculation pass or vehicle navigation acceptance. "
        "parsed_items_count=0 means unknown; diagnostic stdout is not an item count."
    )


class NativeParserRunner:
    """Manages execution of external native parser binaries against candidate artifacts."""

    def __init__(
        self,
        runtime_path: Path | str,
        timeout_seconds: float = 10.0,
        expected_runtime_sha256: str | None = None,
    ) -> None:
        self.runtime_path = Path(runtime_path)
        self.timeout_seconds = timeout_seconds
        self.expected_runtime_sha256 = expected_runtime_sha256

    def verify_runtime(self) -> str:
        """Verify the native runtime binary exists and compute its hash."""
        if not self.runtime_path.is_file():
            raise MissingNativeRuntimeError(
                f"Native runtime executable not found at: {self.runtime_path}"
            )
        runtime_sha = _sha256_file(self.runtime_path)
        if self.expected_runtime_sha256 and runtime_sha != self.expected_runtime_sha256:
            raise NativeRuntimeHashMismatchError(self.expected_runtime_sha256, runtime_sha)
        return runtime_sha

    def probe_tile(
        self,
        tile_id: int,
        artifact_path: Path | str,
        expected_artifact_sha256: str | None = None,
        extra_args: list[str] | None = None,
    ) -> NativeProbeResult:
        """Run native parser on a candidate tile artifact with strict isolation."""
        art_path = Path(artifact_path)
        if not art_path.is_file():
            return NativeProbeResult(
                tile_id=tile_id,
                status=NativeRunStatus.FAIL,
                runtime_path=str(self.runtime_path),
                runtime_sha256="",
                artifact_path=str(art_path),
                artifact_sha256="",
                stderr=f"Candidate artifact file not found: {art_path}",
            )

        actual_art_sha = _sha256_file(art_path)
        if expected_artifact_sha256 and actual_art_sha != expected_artifact_sha256:
            return NativeProbeResult(
                tile_id=tile_id,
                status=NativeRunStatus.HASH_MISMATCH,
                runtime_path=str(self.runtime_path),
                runtime_sha256="",
                artifact_path=str(art_path),
                artifact_sha256=actual_art_sha,
                stderr=(
                    f"Candidate artifact hash mismatch: expected {expected_artifact_sha256}, got {actual_art_sha}"
                ),
            )

        try:
            runtime_sha = self.verify_runtime()
        except NativeRuntimeHashMismatchError as exc:
            return NativeProbeResult(
                tile_id=tile_id,
                status=NativeRunStatus.HASH_MISMATCH,
                runtime_path=str(self.runtime_path),
                runtime_sha256=exc.actual_sha256,
                artifact_path=str(art_path),
                artifact_sha256=actual_art_sha,
                stderr=str(exc),
                route_pass=False,
            )
        except MissingNativeRuntimeError as exc:
            return NativeProbeResult(
                tile_id=tile_id,
                status=NativeRunStatus.MISSING_RUNTIME,
                runtime_path=str(self.runtime_path),
                runtime_sha256="",
                artifact_path=str(art_path),
                artifact_sha256=actual_art_sha,
                stderr=str(exc),
            )

        # Build clean argv list without shell
        argv = [str(self.runtime_path), str(art_path)]
        if extra_args:
            argv.extend(extra_args)

        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                shell=False,
                check=False,
            )

            status = NativeRunStatus.PASS if proc.returncode == 0 else NativeRunStatus.FAIL
            return NativeProbeResult(
                tile_id=tile_id,
                status=status,
                runtime_path=str(self.runtime_path),
                runtime_sha256=runtime_sha,
                artifact_path=str(art_path),
                artifact_sha256=actual_art_sha,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                parsed_items_count=0,
                route_pass=False,  # PARSER PASS NEVER EQUALS ROUTE PASS
            )

        except subprocess.TimeoutExpired as exc:
            return NativeProbeResult(
                tile_id=tile_id,
                status=NativeRunStatus.TIMEOUT,
                runtime_path=str(self.runtime_path),
                runtime_sha256=runtime_sha,
                artifact_path=str(art_path),
                artifact_sha256=actual_art_sha,
                stderr=f"Native parser execution timed out after {self.timeout_seconds}s: {exc}",
                route_pass=False,
            )
        except Exception as exc:
            return NativeProbeResult(
                tile_id=tile_id,
                status=NativeRunStatus.FAIL,
                runtime_path=str(self.runtime_path),
                runtime_sha256=runtime_sha,
                artifact_path=str(art_path),
                artifact_sha256=actual_art_sha,
                stderr=f"Subprocess execution error: {exc}",
                route_pass=False,
            )
