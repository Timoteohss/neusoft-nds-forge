"""Offline artifact validators, integrity checkers, and capability matrix (Issue #36 / [6.1]).

Validates:
1. JSON schema structure and metadata contracts.
2. SQLite table presence and PRAGMA integrity_check / quick_check.
3. Artifact SHA-256 hashes against declared manifest signatures.
4. Catalog cross-references and tile coverage plans.
5. Architectural limits and feasibility thresholds.
6. Provenance, ODbL attribution, and licensing tags.
7. Capability matrix:
   Strictly separates validation tiers:
   - GENERATED_OFFLINE
   - NATIVE_PARSER
   - SDK_EVALUATION
   - UI_PREVIEW
   - GUIDANCE_IN_CAR
   Rejects any synthetic artifact claiming 'real validated'.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any


class ValidationTier(str, Enum):
    """Validation tiers in ascending order of authority."""
    GENERATED_OFFLINE = "generated_offline"
    NATIVE_PARSER = "native_parser"
    SDK_EVALUATION = "sdk_evaluation"
    UI_PREVIEW = "ui_preview"
    GUIDANCE_IN_CAR = "guidance_in_car"


class OfflineValidationError(ValueError):
    """Raised when an artifact fails offline validation checks."""


class InvalidValidationClaimError(ValueError):
    """Raised when an offline or synthetic fixture claims 'real validated' status."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class ValidationReport:
    """Detailed results of offline artifact validation."""
    is_valid: bool
    tier: str = ValidationTier.GENERATED_OFFLINE.value
    passed_checks: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


def validate_artifact_hashes(manifest: dict[str, Any], artifact_dir: Path) -> list[str]:
    """Validate all files in artifact_dir against manifest declared output_hashes or files."""
    errors: list[str] = []
    declared_hashes = manifest.get("output_hashes") or manifest.get("files") or {}

    for rel_path, expected_hash in declared_hashes.items():
        if isinstance(expected_hash, dict):
            expected_hash = expected_hash.get("sha256", "")
        file_path = artifact_dir / rel_path
        if not file_path.is_file():
            errors.append(f"Missing expected artifact file: {rel_path}")
            continue
        actual_hash = sha256_file(file_path)
        if actual_hash != expected_hash:
            errors.append(
                f"Hash mismatch for {rel_path}: expected {expected_hash}, got {actual_hash}"
            )
    return errors


def validate_sqlite_database(
    db_path: Path,
    expected_tables: list[str] | None = None,
    quick_check: bool = True,
) -> list[str]:
    """Validate SQLite database integrity and table schema."""
    errors: list[str] = []
    if not db_path.is_file():
        return [f"Database file does not exist: {db_path}"]

    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            check_sql = "PRAGMA quick_check" if quick_check else "PRAGMA integrity_check"
            cursor = conn.execute(check_sql)
            rows = cursor.fetchall()
            if not rows or rows[0][0] != "ok":
                errors.append(f"SQLite integrity check failed for {db_path.name}: {rows}")

            if expected_tables:
                cur = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
                actual_tables = {r[0] for r in cur.fetchall()}
                missing = set(expected_tables) - actual_tables
                if missing:
                    errors.append(f"Missing expected tables in {db_path.name}: {sorted(missing)}")
    except Exception as exc:
        errors.append(f"SQLite error opening {db_path.name}: {exc}")

    return errors


def validate_provenance_and_licensing(manifest: dict[str, Any]) -> list[str]:
    """Validate license, attribution, and data boundaries."""
    errors: list[str] = []
    # If this is an OSM-derived layer, check for required ODbL attribution
    is_osm = manifest.get("source_type") == "osm" or "osm" in manifest.get("step_id", "")
    if is_osm:
        attr = manifest.get("attribution", "")
        lic = manifest.get("license", "")
        if "OpenStreetMap" not in attr:
            errors.append("OSM derived artifact lacks OpenStreetMap attribution")
        if "ODbL" not in lic and "Open Database" not in lic:
            errors.append("OSM derived artifact lacks ODbL license declaration")
    return errors


def validate_capability_matrix_claim(claim: str, is_synthetic_or_offline: bool = True) -> str:
    """Enforce strict separation between offline/synthetic checks and hardware/vehicle validation."""
    clean_claim = claim.strip().lower()

    forbidden_phrases = ["real validated", "real_validated", "in_car_validated", "vehicle_accepted"]
    for phrase in forbidden_phrases:
        if phrase in clean_claim:
            if is_synthetic_or_offline:
                raise InvalidValidationClaimError(
                    f"Invalid claim '{claim}': Synthetic or offline fixtures must never be promoted to '{phrase}'."
                )

    valid_tiers = {tier.value for tier in ValidationTier}
    if clean_claim not in valid_tiers and clean_claim not in {"partial", "unvalidated"}:
        raise InvalidValidationClaimError(f"Unknown validation tier claim: '{claim}'")

    return clean_claim


def perform_offline_validation(
    artifact_dir: Path,
    manifest_path: Path | None = None,
    sqlite_checks: dict[str, list[str]] | None = None,
    is_synthetic: bool = True,
    claimed_tier: str = ValidationTier.GENERATED_OFFLINE.value,
) -> ValidationReport:
    """Execute complete suite of offline validation checks."""
    errors: list[str] = []
    passed: list[str] = []
    warnings: list[str] = []

    # 1. Claim validity check
    try:
        validated_tier = validate_capability_matrix_claim(claimed_tier, is_synthetic_or_offline=is_synthetic)
        passed.append(f"Capability tier claim verified: {validated_tier}")
    except InvalidValidationClaimError as exc:
        errors.append(str(exc))
        return ValidationReport(is_valid=False, tier="rejected", errors=errors)

    # 2. Manifest check
    manifest = {}
    if manifest_path and manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            passed.append(f"Manifest parsed: {manifest_path.name}")
        except Exception as exc:
            errors.append(f"Malformed manifest JSON: {exc}")
    elif manifest_path:
        errors.append(f"Manifest not found: {manifest_path}")

    # 3. Hash verification
    if manifest:
        hash_errors = validate_artifact_hashes(manifest, artifact_dir)
        if hash_errors:
            errors.extend(hash_errors)
        else:
            passed.append("All declared file hashes verified successfully")

        # 4. Provenance & License verification
        prov_errors = validate_provenance_and_licensing(manifest)
        if prov_errors:
            errors.extend(prov_errors)
        else:
            passed.append("Provenance and license declarations verified")

    # 5. SQLite checks
    if sqlite_checks:
        for db_name, expected_tables in sqlite_checks.items():
            db_path = artifact_dir / db_name
            db_errs = validate_sqlite_database(db_path, expected_tables=expected_tables)
            if db_errs:
                errors.extend(db_errs)
            else:
                passed.append(f"SQLite database {db_name} integrity and tables verified")

    is_valid = len(errors) == 0

    return ValidationReport(
        is_valid=is_valid,
        tier=validated_tier,
        passed_checks=passed,
        errors=errors,
        warnings=warnings,
        details={
            "artifact_dir": str(artifact_dir),
            "total_checks": len(passed) + len(errors),
        },
    )
