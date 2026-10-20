"""Target Profile schema, validation, and evidence binding.

Defines opaque country/region/product/UR/building-block/grid/index/key-provider
fields, enforces authorized evidence binding, rejects guessed IDs, and ensures
synthetic profiles are accepted for tests but strictly rejected for release.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
from pathlib import Path
from typing import Any


class ProfileError(ValueError):
    """Raised when profile validation or evidence binding fails."""


class EvidenceSourceType(str, Enum):
    """Authorized sources of evidence for opaque profile parameters."""
    SYNTHETIC = "synthetic"
    DEVICE_BINARY_INSPECTION = "device_binary_inspection"
    BOOTSTRAP_SEED_CATALOG = "bootstrap_seed_catalog"
    VENDOR_SPEC_AUTHORIZED = "vendor_spec_authorized"


@dataclass(frozen=True)
class EvidenceRecord:
    """Explicit authorized evidence binding for an opaque profile field."""
    field: str
    value: Any
    source_type: EvidenceSourceType
    reference: str
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "field": self.field,
            "value": self.value,
            "source_type": self.source_type.value,
            "reference": self.reference,
        }
        if self.notes is not None:
            result["notes"] = self.notes
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceRecord:
        return cls(
            field=str(data["field"]),
            value=data["value"],
            source_type=EvidenceSourceType(data["source_type"]),
            reference=str(data["reference"]),
            notes=data.get("notes"),
        )


@dataclass(frozen=True)
class BuildingBlockSpec:
    """Specification of an NDS building block layer."""
    building_block_id: int
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"building_block_id": self.building_block_id}
        if self.name is not None:
            result["name"] = self.name
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BuildingBlockSpec:
        return cls(
            building_block_id=int(data["building_block_id"]),
            name=data.get("name"),
        )


@dataclass(frozen=True)
class GridSpec:
    """Tiling and shift resolution configuration for route and BMD layers."""
    route_level: int
    route_shift: int
    bmd_level: int
    bmd_shift: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_level": self.route_level,
            "route_shift": self.route_shift,
            "bmd": {
                "level": self.bmd_level,
                "shift": self.bmd_shift,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GridSpec:
        bmd = data.get("bmd", {})
        return cls(
            route_level=int(data["route_level"]),
            route_shift=int(data["route_shift"]),
            bmd_level=int(bmd.get("level", 11)),
            bmd_shift=int(bmd.get("shift", 4)),
        )


@dataclass(frozen=True)
class CuratedPOISpec:
    """Specification for curated baseline POI input."""
    source: str
    is_curated: bool = True
    max_records: int = 10
    coverage_scope: str = "curated_baseline"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "is_curated": self.is_curated,
            "max_records": self.max_records,
            "coverage_scope": self.coverage_scope,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CuratedPOISpec:
        return cls(
            source=str(data["source"]),
            is_curated=bool(data.get("is_curated", True)),
            max_records=int(data.get("max_records", 10)),
            coverage_scope=str(data.get("coverage_scope", "curated_baseline")),
        )


@dataclass
class TargetProfile:
    """Comprehensive NDS target profile with opaque fields and evidence bindings."""
    profile_id: str
    maturity: str  # "synthetic", "development", "production"
    country_id: int
    region_id: int
    product_id: str
    ur_code: str
    building_blocks: dict[str, BuildingBlockSpec]
    grid: GridSpec
    index_levels: dict[str, int]
    key_providers: dict[str, str]
    curated_poi_spec: CuratedPOISpec
    evidence_records: list[EvidenceRecord] = field(default_factory=list)
    claim_brazil_authority: bool = False
    allow_guessed_ids: bool = False
    schema_version: int = 1

    def validate(self, for_release: bool = False) -> None:
        """Validate profile constraints, evidence bindings, and release eligibility.

        Raises:
            ProfileError: When constraints, evidence requirements, or release gates fail.
        """
        # 1. Guessed IDs policy: strictly rejected
        if self.allow_guessed_ids:
            raise ProfileError("Guessed IDs are strictly rejected; authorized evidence is required")

        for ev in self.evidence_records:
            if "guess" in ev.reference.lower() or (ev.notes and "guess" in ev.notes.lower()):
                raise ProfileError(f"Guessed evidence reference rejected for field '{ev.field}'")

        # 2. Check for missing opaque identifiers
        if self.country_id is None:
            raise ProfileError("Missing required opaque identifier: country_id")
        if self.region_id is None:
            raise ProfileError("Missing required opaque identifier: region_id")
        if not self.product_id:
            raise ProfileError("Missing required opaque identifier: product_id")
        if not self.ur_code:
            raise ProfileError("Missing required opaque identifier: ur_code")
        if not self.building_blocks:
            raise ProfileError("Profile must declare at least one building block")

        # 3. Check release gate
        is_release = for_release or self.maturity == "production"
        if is_release:
            if self.maturity == "synthetic":
                raise ProfileError("Synthetic profile is accepted for tests but rejected as release")
            synthetic_ev = [
                ev.field for ev in self.evidence_records
                if ev.source_type == EvidenceSourceType.SYNTHETIC
            ]
            if synthetic_ev:
                raise ProfileError(
                    f"Release profile cannot bind synthetic evidence for: {synthetic_ev}"
                )

        # 4. Enforce evidence binding on real values
        ev_fields = {ev.field for ev in self.evidence_records}
        required_evidence_fields = {"country_id", "region_id", "product_id", "ur_code"}
        missing_evidence = required_evidence_fields - ev_fields
        if missing_evidence:
            raise ProfileError(
                f"Missing authorized evidence binding for opaque fields: {sorted(missing_evidence)}"
            )

        # 5. Guard against false Brazil national profile claims (e.g. ISR/UR439)
        if self.region_id == 439 or self.ur_code == "UR439":
            if self.claim_brazil_authority:
                raise ProfileError(
                    "Do not infer Brazil IDs; ISR/UR439 is an Israel-derived bootstrap "
                    "compatibility profile and cannot be claimed as an official Brazil profile"
                )

        # 6. Curated POI input constraints
        if not self.curated_poi_spec.is_curated:
            raise ProfileError("curated_poi_spec must have is_curated=True")
        if self.curated_poi_spec.coverage_scope in {"complete", "complete_osm_poi_coverage"}:
            raise ProfileError(
                "Profile-curated POIs must be labeled curated and never represented "
                "as complete OSM POI coverage"
            )

        # 7. Key provider declaration check: never raw key bytes
        for path, provider in self.key_providers.items():
            if not provider.startswith("env:"):
                raise ProfileError(
                    f"Key provider for '{path}' must be an indirect 'env:' secret reference, "
                    f"got: '{provider}'"
                )

    def validate_against_schema(self, schema_path: Path | None = None) -> None:
        """Validate serialized dictionary against profile.schema.json if available."""
        if schema_path is None:
            default_path = Path(__file__).resolve().parent.parent / "schemas" / "profile.schema.json"
            if default_path.is_file():
                schema_path = default_path
        if schema_path is not None and schema_path.is_file():
            try:
                import jsonschema  # type: ignore
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                jsonschema.validate(instance=self.to_dict(), schema=schema)
            except ImportError:
                # Built-in validation rules already enforced in validate()
                pass
            except Exception as exc:
                raise ProfileError(f"Profile schema validation failed: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "maturity": self.maturity,
            "country_id": self.country_id,
            "region_id": self.region_id,
            "product_id": self.product_id,
            "ur_code": self.ur_code,
            "building_blocks": {
                name: bb.to_dict()
                for name, bb in sorted(self.building_blocks.items())
            },
            "grid": self.grid.to_dict(),
            "index_levels": dict(sorted(self.index_levels.items())),
            "key_providers": dict(sorted(self.key_providers.items())),
            "curated_poi_spec": self.curated_poi_spec.to_dict(),
            "evidence_binding": {
                "claim_brazil_authority": self.claim_brazil_authority,
                "evidence_records": [ev.to_dict() for ev in self.evidence_records],
            },
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True, ensure_ascii=False) + "\n"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TargetProfile:
        bb_data = data.get("building_blocks", {})
        building_blocks = {
            k: BuildingBlockSpec.from_dict(v) for k, v in bb_data.items()
        }
        grid = GridSpec.from_dict(data["grid"])
        curated_poi_spec = CuratedPOISpec.from_dict(data["curated_poi_spec"])
        ev_data = data.get("evidence_binding", {})
        evidence_records = [
            EvidenceRecord.from_dict(item)
            for item in ev_data.get("evidence_records", [])
        ]
        return cls(
            schema_version=data.get("schema_version", 1),
            profile_id=str(data["profile_id"]),
            maturity=str(data["maturity"]),
            country_id=int(data["country_id"]),
            region_id=int(data["region_id"]),
            product_id=str(data["product_id"]),
            ur_code=str(data["ur_code"]),
            building_blocks=building_blocks,
            grid=grid,
            index_levels={str(k): int(v) for k, v in data.get("index_levels", {}).items()},
            key_providers={str(k): str(v) for k, v in data.get("key_providers", {}).items()},
            curated_poi_spec=curated_poi_spec,
            evidence_records=evidence_records,
            claim_brazil_authority=bool(ev_data.get("claim_brazil_authority", False)),
        )

    @classmethod
    def from_json(cls, json_str: str) -> TargetProfile:
        return cls.from_dict(json.loads(json_str))


def validate_curated_poi_document(doc: dict[str, Any], max_records: int = 10) -> None:
    """Validate that a curated POI document is explicitly labeled as curated and bounded.

    Raises:
        ProfileError: If the document claims complete coverage or exceeds the curated limit.
    """
    scope = doc.get("scope", "")
    if "complete" in scope.lower() or "exhaustive" in scope.lower():
        raise ProfileError(
            "Curated POI document must never be represented as complete OSM POI coverage"
        )
    records = doc.get("records")
    if not isinstance(records, list):
        raise ProfileError("Curated POI document missing 'records' list")
    if len(records) > max_records:
        raise ProfileError(
            f"Curated POI count ({len(records)}) exceeds maximum allowed ({max_records})"
        )
