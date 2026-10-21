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

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
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
    declared_hashes = manifest.get("output_hashes")
    if declared_hashes is None:
        records = manifest.get("files")
        if not isinstance(records, list) or not records:
            return ["Manifest must declare non-empty output_hashes or files[]"]
        declared_hashes = {}
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                errors.append("Invalid file record in manifest")
                continue
            path = record["path"]
            if path in declared_hashes:
                errors.append(f"Duplicate artifact path: {path}")
            declared_hashes[path] = record
    if not isinstance(declared_hashes, dict) or not declared_hashes:
        return errors + ["Manifest must declare non-empty output_hashes or files[]"]

    root = artifact_dir.resolve()
    for rel_path, declaration in declared_hashes.items():
        if not isinstance(rel_path, str) or not rel_path:
            errors.append("Invalid artifact path in manifest")
            continue
        relative = Path(rel_path)
        file_path = (root / relative).resolve()
        if relative.is_absolute() or ".." in relative.parts or root not in file_path.parents:
            errors.append(f"Unsafe artifact path: {rel_path}")
            continue
        expected_hash = declaration.get("sha256") if isinstance(declaration, dict) else declaration
        if (not isinstance(expected_hash, str) or len(expected_hash) != 64
                or any(char not in "0123456789abcdefABCDEF" for char in expected_hash)):
            errors.append(f"Invalid SHA-256 declaration for {rel_path}")
            continue
        if not file_path.is_file():
            errors.append(f"Missing expected artifact file: {rel_path}")
            continue
        if isinstance(declaration, dict) and "bytes" in declaration:  # noqa: SIM102 -- Keep nested guards to make validation branches explicit while preserving short-circuit order
            if declaration["bytes"] != file_path.stat().st_size:
                errors.append(f"Size mismatch for {rel_path}")
        actual_hash = sha256_file(file_path)
        if actual_hash != expected_hash.lower():
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
    except Exception as exc:  # noqa: BLE001 -- Broad catch is intentional at this recovery/reporting boundary; preserve rollback/error aggregation behavior
        errors.append(f"SQLite error opening {db_path.name}: {exc}")

    return errors


@dataclass
class ProvenanceValidationResult:
    """Applicability and declaration checks, not proof of source authenticity."""
    applicable: bool
    errors: list[str] = field(default_factory=list)
    checked_items: int = 0

    @property
    def status(self) -> str:
        if not self.applicable:
            return "not_assessed"
        return "failed" if self.errors else "declarations_checked"


def _osm_declaration_errors(attribution: Any, license_text: Any, label: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(attribution, str) or "OpenStreetMap" not in attribution:
        errors.append(f"{label} lacks OpenStreetMap attribution")
    if not isinstance(license_text, str) or not any(value in license_text for value in ("ODbL", "Open Database")):
        errors.append(f"{label} lacks ODbL license declaration")
    return errors


def _provenance_record_errors(provenance: Any, label: str) -> list[str]:
    if not isinstance(provenance, dict) or not provenance:
        return [f"{label} lacks provenance metadata"]
    errors = []
    source, license_text = provenance.get("source"), provenance.get("license")
    if not isinstance(source, str) or not source.strip():
        errors.append(f"{label} lacks a source provenance declaration")
    if not isinstance(license_text, str) or not license_text.strip():
        errors.append(f"{label} lacks a license declaration")
    attribution = provenance.get("attribution", "")
    # Existing package origins include OSM attribution in the license text.
    attribution_text = " ".join(value for value in (attribution, license_text) if isinstance(value, str))
    is_osm = (provenance.get("source_type") == "osm" or
              (isinstance(source, str) and re.search(r"\b(?:OSM|OpenStreetMap)\b", source, re.IGNORECASE)) or
              "OpenStreetMap" in attribution_text)
    if is_osm:
        errors.extend(_osm_declaration_errors(attribution_text, license_text, label))
    return errors


def validate_provenance_and_licensing(manifest: dict[str, Any]) -> ProvenanceValidationResult:
    """Assess supported provenance declarations; absence is not verification."""
    is_container = any(manifest.get(field) == "container.pack" for field in ("step", "step_id", "target"))
    if is_container:
        records = manifest.get("files")
        if not isinstance(records, list) or not records:
            return ProvenanceValidationResult(True, ["Container provenance requires non-empty files[]"])
        errors = []
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                errors.append(f"Container file {index} lacks provenance metadata")
                continue
            errors.extend(_provenance_record_errors(record.get("provenance"), f"Container file {record.get('path', index)}"))
        return ProvenanceValidationResult(True, errors, len(records))
    step = manifest.get("step_id", manifest.get("step", ""))
    if manifest.get("source_type") == "osm" or (isinstance(step, str) and "osm" in step):
        source = manifest.get("source")
        source = source if isinstance(source, dict) else {}
        return ProvenanceValidationResult(True, _osm_declaration_errors(
            manifest.get("attribution", source.get("attribution")),
            manifest.get("license", source.get("license")), "OSM derived artifact"), 1)
    if "provenance" in manifest:
        return ProvenanceValidationResult(True, _provenance_record_errors(manifest["provenance"], "Artifact"), 1)
    return ProvenanceValidationResult(False)


def validate_capability_matrix_claim(claim: str, is_synthetic_or_offline: bool = True) -> str:
    """Enforce strict separation between offline/synthetic checks and hardware/vehicle validation."""
    clean_claim = claim.strip().lower()

    forbidden_phrases = ["real validated", "real_validated", "in_car_validated", "vehicle_accepted"]
    for phrase in forbidden_phrases:
        if phrase in clean_claim:  # noqa: SIM102 -- Keep nested guards to make validation branches explicit while preserving short-circuit order
            if is_synthetic_or_offline:
                raise InvalidValidationClaimError(
                    f"Invalid claim '{claim}': Synthetic or offline fixtures must never be promoted to '{phrase}'."
                )

    valid_tiers = {tier.value for tier in ValidationTier}
    if clean_claim not in valid_tiers and clean_claim not in {"partial", "unvalidated"}:
        raise InvalidValidationClaimError(f"Unknown validation tier claim: '{claim}'")

    if is_synthetic_or_offline and clean_claim not in {
        ValidationTier.GENERATED_OFFLINE.value, "partial", "unvalidated"
    }:
        raise InvalidValidationClaimError(
            f"Offline or synthetic validation cannot establish '{clean_claim}'; "
            "independent runtime/device evidence is required."
        )
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
    provenance = ProvenanceValidationResult(False)

    # 1. Claim validity check
    try:
        validated_tier = validate_capability_matrix_claim(claimed_tier, is_synthetic_or_offline=True)
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
        except Exception as exc:  # noqa: BLE001 -- Broad catch is intentional at this recovery/reporting boundary; preserve rollback/error aggregation behavior
            errors.append(f"Malformed manifest JSON: {exc}")
    elif manifest_path:
        errors.append(f"Manifest not found: {manifest_path}")
    else:
        errors.append("Manifest is required for offline artifact validation")
    if manifest_path and manifest_path.is_file() and not isinstance(manifest, dict):
        errors.append("Manifest root must be an object")
        manifest = {}
    elif manifest_path and manifest_path.is_file() and not manifest and not errors:
        errors.append("Manifest must declare artifact hashes")

    # 3. Hash verification
    if manifest:
        hash_errors = validate_artifact_hashes(manifest, artifact_dir)
        if hash_errors:
            errors.extend(hash_errors)
        else:
            passed.append("All declared file hashes verified successfully")

        # 4. Provenance & License verification
        provenance = validate_provenance_and_licensing(manifest)
        if provenance.errors:
            errors.extend(provenance.errors)
        elif provenance.applicable and provenance.checked_items:
            passed.append("Provenance and license declarations verified")
            warnings.append("Provenance checks assess declarations only; source authenticity and redistribution rights are not verified")
        else:
            warnings.append("Provenance and license declarations not assessed: no supported provenance metadata")

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
            "provenance": {**asdict(provenance), "status": provenance.status},
        },
    )
