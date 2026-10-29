"""Explicit product/update-region identity for Neusoft package outputs.

The pipeline previously inferred ``NAV/UR439`` and Israel product IDs from the
Curitiba v9 bootstrap.  This module makes every opaque identity a validated,
declared value and derives package paths from it.  The Brazil/Paraná preset is
a local experimental namespace; it is not an official licensed Neusoft ID.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .schema_validation import validate_json_schema

_UR_PATTERN = re.compile(r"^UR([0-9]+)$")
_NAV_PATH = re.compile(r"^(?P<prefix>.*?)(?P<nav>NAV/)?UR[0-9]+/(?P<rest>.*)$")
_VERSION_PATTERN = re.compile(r"^N\.[A-Z0-9]{2}\.[A-Z0-9]{2}\.[0-9]{2}\.[0-9]{2}\.[0-9]{2}\.[0-9]{2}$")
_NAV_DB_NAMES = ("BMD.NDS", "NAME.NDS", "FTS.NDS", "POI.NDS", "ROUTE.NDS", "SHARED.NDS")
_STATIC_NAV_NAMES = ("3D.NDS", "EXTBMD.NDS", "EXTTMC.NDS", "JV.NDS", "SPEECH.NDS", "TMC.NDS")


class RegionBindingError(ValueError):
    """Raised when a product/update-region declaration is incomplete or unsafe."""


@dataclass(frozen=True)
class TimeZoneSpec:
    """Region time zone encoded in SHARED.NDS."""

    id: int
    olson: str
    utc_offset_quarters: int
    name: str
    abbrev: str

    def validate(self) -> None:
        if not 0 <= self.id <= 65535:
            raise RegionBindingError("timezone_id must fit an unsigned 16-bit value")
        if not -56 <= self.utc_offset_quarters <= 56:
            raise RegionBindingError("timezone_utc_offset_quarters must be within -56..56")
        if not self.olson or not re.fullmatch(r"[A-Za-z_]+/[A-Za-z0-9_+\-/]+", self.olson):
            raise RegionBindingError("timezone_olson must be an IANA Area/Location identifier")
        if self.olson == "America/Sao_Paulo" and self.utc_offset_quarters != -12:
            raise RegionBindingError(
                "America/Sao_Paulo uses UTC-3, encoded as -12 quarter-hours")
        if not self.name or not self.abbrev:
            raise RegionBindingError("time zone name and abbreviation are required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "olson": self.olson,
            "utc_offset_quarters": self.utc_offset_quarters,
            "name": self.name,
            "abbrev": self.abbrev,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TimeZoneSpec:
        try:
            return cls(
                id=int(data["id"]),
                olson=str(data["olson"]),
                utc_offset_quarters=int(data["utc_offset_quarters"]),
                name=str(data["name"]),
                abbrev=str(data["abbrev"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RegionBindingError(f"Invalid time zone declaration: {exc}") from exc


@dataclass(frozen=True)
class RegionBinding:
    """Opaque catalogue identity plus the package paths derived from it."""

    profile_id: str
    product_group_id: int
    nav_product_id: int
    deco_product_id: int
    update_region_set_id: int
    update_region_id: int
    ur_code: str
    area_code: str
    version_name: str
    product_name: str
    set_name: str
    region_name: str
    iso_country_code: str
    timezone: TimeZoneSpec | None = None

    def validate(self) -> None:
        if not self.profile_id or not re.fullmatch(r"[A-Za-z0-9_-]+", self.profile_id):
            raise RegionBindingError("profile_id accepts only letters, digits, hyphen and underscore")
        ids = {
            "product_group_id": self.product_group_id,
            "nav_product_id": self.nav_product_id,
            "deco_product_id": self.deco_product_id,
            "update_region_set_id": self.update_region_set_id,
            "update_region_id": self.update_region_id,
        }
        for name, value in ids.items():
            if not isinstance(value, int) or isinstance(value, bool) or not 0 < value < 1 << 31:
                raise RegionBindingError(f"{name} must be a positive 31-bit integer")
        if len(set(ids.values())) != len(ids):
            raise RegionBindingError("product, set and region IDs must be distinct")
        match = _UR_PATTERN.fullmatch(self.ur_code)
        if match is None or int(match.group(1)) != self.update_region_id:
            raise RegionBindingError("ur_code must equal UR<update_region_id>")
        if not re.fullmatch(r"[A-Z0-9]{2}", self.area_code):
            raise RegionBindingError("area_code must be two uppercase letters/digits")
        if _VERSION_PATTERN.fullmatch(self.version_name) is None:
            raise RegionBindingError("version_name must match N.<xx>.<area>.<yy>.<zz>.<zz>.<zz>")
        if self.version_name.split(".")[2] != self.area_code:
            raise RegionBindingError("version_name area segment must equal area_code")
        if not re.fullmatch(r"[A-Z]{3}", self.iso_country_code):
            raise RegionBindingError("iso_country_code must be a three-letter ISO 3166-1 code")
        for name in ("product_name", "set_name", "region_name"):
            if not getattr(self, name):
                raise RegionBindingError(f"{name} is required")
        if self.timezone is not None:
            self.timezone.validate()

    @property
    def nav_dir(self) -> str:
        return f"db/NAV/{self.ur_code}"

    def nav_path(self, filename: str) -> str:
        if "/" in filename or "\\" in filename:
            raise RegionBindingError("nav_path accepts a database filename only")
        return f"{self.nav_dir}/{filename}"

    def generated_zv_paths(self) -> frozenset[str]:
        return frozenset(self.nav_path(name) for name in _NAV_DB_NAMES)

    def static_zv_paths(self) -> frozenset[str]:
        return frozenset(
            {f"db/DECO/UR0/{name}" for name in ("BMD.NDS", "NAME.NDS", "SHARED.NDS")}
            | {self.nav_path(name) for name in _STATIC_NAV_NAMES}
        )

    def required_paths(self) -> frozenset[str]:
        return frozenset({
            "db/Area.txt", "db/DECO/PRODUCT.NDS", "db/NAV/PRODUCT.NDS",
            "db/ROOT.NDK", "db/ROOT.NDS", "db/metadata.json",
        }) | self.generated_zv_paths() | self.static_zv_paths()

    def derived_paths(self) -> frozenset[str]:
        return frozenset({
            "db/Area.txt", "db/metadata.json", "db/ROOT.NDS", "db/NAV/PRODUCT.NDS",
            self.nav_path("SHARED.NDS"),
        })

    def formats(self) -> dict[str, str]:
        return {
            **{path: "zv_zlib" for path in self.generated_zv_paths() | self.static_zv_paths()},
            "db/DECO/PRODUCT.NDS": "sqlite3",
            "db/NAV/PRODUCT.NDS": "sqlite3",
            "db/ROOT.NDS": "sqlite3",
            "db/Area.txt": "text",
            "db/ROOT.NDK": "ndk_text",
            "db/metadata.json": "json",
        }

    def seed_to_output(self) -> dict[str, str]:
        """Map bootstrap logical seed names to this binding's output paths."""
        return {
            f"db/NAV/UR{self.update_region_id}/{name}": self.nav_path(name)
            for name in _NAV_DB_NAMES
        }

    def remap_path(self, value: str, source_ur_code: str | None = None) -> str:
        """Rewrite a NAV/UR path or URI to this binding without touching geometry."""
        # DECO/UR0 is the shared world-map product, not an update-region path.
        if value.startswith(("db/DECO/UR0/", "DECO/UR0/")):
            return value
        if source_ur_code:
            source = re.compile(rf"(?P<prefix>.*?)(?P<nav>NAV/)?{re.escape(source_ur_code)}/(?P<rest>.*)")
            match = source.fullmatch(value)
            if match:
                return (f"{match['prefix']}{match['nav'] or ''}{self.ur_code}/"
                        f"{match['rest']}")
        match = _NAV_PATH.fullmatch(value)
        if match is None:
            return value
        return f"{match['prefix']}{match['nav'] or ''}{self.ur_code}/{match['rest']}"

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "profile_id": self.profile_id,
            "product_group_id": self.product_group_id,
            "nav_product_id": self.nav_product_id,
            "deco_product_id": self.deco_product_id,
            "update_region_set_id": self.update_region_set_id,
            "update_region_id": self.update_region_id,
            "ur_code": self.ur_code,
            "area_code": self.area_code,
            "version_name": self.version_name,
            "product_name": self.product_name,
            "set_name": self.set_name,
            "region_name": self.region_name,
            "iso_country_code": self.iso_country_code,
        }
        if self.timezone is not None:
            result["timezone"] = self.timezone.to_dict()
        return result

    def to_json(self, indent: int = 2) -> str:
        import json
        return json.dumps(self.to_dict(), sort_keys=True, indent=indent, ensure_ascii=False) + "\n"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RegionBinding:
        try:
            timezone = data.get("timezone")
            binding = cls(
                profile_id=str(data["profile_id"]),
                product_group_id=int(data["product_group_id"]),
                nav_product_id=int(data["nav_product_id"]),
                deco_product_id=int(data["deco_product_id"]),
                update_region_set_id=int(data["update_region_set_id"]),
                update_region_id=int(data["update_region_id"]),
                ur_code=str(data["ur_code"]),
                area_code=str(data["area_code"]),
                version_name=str(data["version_name"]),
                product_name=str(data["product_name"]),
                set_name=str(data["set_name"]),
                region_name=str(data["region_name"]),
                iso_country_code=str(data["iso_country_code"]),
                timezone=TimeZoneSpec.from_dict(timezone) if isinstance(timezone, Mapping) else None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RegionBindingError(f"Invalid region binding: {exc}") from exc
        binding.validate()
        return binding

    def validate_against_schema(self, schema_path: Path | None = None) -> bool:
        if schema_path is None:
            schema_path = Path(__file__).resolve().parent.parent / "schemas" / "region_binding.schema.json"
        return validate_json_schema(self.to_dict(), schema_path)


# Legacy compatibility preset for the pinned Curitiba v9 bootstrap.  It is an
# explicit fallback, never an inferred national identity.
LEGACY_ISRAEL_V9 = RegionBinding(
    profile_id="neusoft-curitiba-v9",
    product_group_id=9000,
    nav_product_id=90000001,
    deco_product_id=90000002,
    update_region_set_id=9100,
    update_region_id=439,
    ur_code="UR439",
    area_code="IL",
    version_name="N.J6.IL.26.00.01.00",
    product_name="Curitiba v9 OSM",
    set_name="Brasil",
    region_name="Curitiba",
    iso_country_code="ISR",
)

BRAZIL_PARANA = RegionBinding(
    profile_id="neusoft-brazil-parana",
    product_group_id=9000,
    nav_product_id=90000011,
    deco_product_id=90000012,
    update_region_set_id=9100,
    update_region_id=9102,
    ur_code="UR9102",
    area_code="AC",
    version_name="N.J6.AC.26.00.02.00",
    product_name="Brazil",
    set_name="Brasil",
    region_name="Paraná",
    iso_country_code="BRA",
    timezone=TimeZoneSpec(
        id=6,
        olson="America/Sao_Paulo",
        utc_offset_quarters=-12,
        name="Brasilia Time",
        abbrev="BRT",
    ),
)

PRESETS = {
    LEGACY_ISRAEL_V9.profile_id: LEGACY_ISRAEL_V9,
    BRAZIL_PARANA.profile_id: BRAZIL_PARANA,
}
for _preset in PRESETS.values():
    _preset.validate()


def resolve_region_binding(neusoft: Mapping[str, Any]) -> RegionBinding:
    """Return the declared binding or the explicit Curitiba v9 compatibility preset."""
    raw = neusoft.get("region_binding")
    if raw is not None:
        if not isinstance(raw, Mapping):
            raise RegionBindingError("neusoft.region_binding must be a table")
        binding = RegionBinding.from_dict(raw)
    else:
        profile_id = neusoft.get("profile_id")
        preset = PRESETS.get(profile_id) if isinstance(profile_id, str) else None
        if preset is None:
            raise RegionBindingError(
                "neusoft.region_binding is required unless profile_id names a known preset")
        binding = preset
    declared_profile = neusoft.get("profile_id")
    if declared_profile is not None and declared_profile != binding.profile_id:
        raise RegionBindingError(
            f"neusoft.profile_id ({declared_profile!r}) does not match "
            f"region_binding.profile_id ({binding.profile_id!r})")
    validate_region_binding_config(neusoft, binding)
    return binding


def validate_region_binding_config(neusoft: Mapping[str, Any], binding: RegionBinding) -> None:
    """Cross-check region IDs and generated key paths against the binding."""
    for block in ("names", "poi", "route"):
        value = neusoft.get(block)
        if (isinstance(value, Mapping) and value.get("region_id") is not None
                and int(value["region_id"]) != binding.update_region_id):
            raise RegionBindingError(
                f"neusoft.{block}.region_id must equal update_region_id "
                f"{binding.update_region_id}, got {value['region_id']}")
    keys = neusoft.get("container_keys")
    if isinstance(keys, Mapping) and keys:
        declared = set(keys)
        expected = binding.generated_zv_paths()
        if declared != expected:
            raise RegionBindingError(
                "neusoft.container_keys must name exactly the generated databases: "
                f"missing={sorted(expected - declared)}, extra={sorted(declared - expected)}")


def rebind(binding: RegionBinding, **changes: Any) -> RegionBinding:
    """Return a validated copy; useful for tests and explicit experiments."""
    updated = replace(binding, **changes)
    updated.validate()
    return updated
