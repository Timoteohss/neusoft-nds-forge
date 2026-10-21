"""Canonical road attribute normalization for NDS Forge.

Normalizes raw OSM highway tags into standard domain structures (highway class,
oneway direction, access levels, speed limits, barriers, and conditional rules)
while maintaining loss-awareness and raw tag preservation.

NDS binary flag mapping remains strictly separate in compiler layers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RoadNormalizationError(Exception):
    """Raised when road attribute normalization fails or encounters disallowed unknowns."""


class HighwayClass(str, Enum):
    """Standardized highway classification."""
    MOTORWAY = "motorway"
    TRUNK = "trunk"
    PRIMARY = "primary"
    SECONDARY = "secondary"
    TERTIARY = "tertiary"
    UNCLASSIFIED = "unclassified"
    RESIDENTIAL = "residential"
    LIVING_STREET = "living_street"
    SERVICE = "service"
    PEDESTRIAN = "pedestrian"
    TRACK = "track"
    BUSWAY = "busway"
    ROAD = "road"
    PATH = "path"
    CYCLEWAY = "cycleway"
    FOOTWAY = "footway"
    BRIDLEWAY = "bridleway"
    STEPS = "steps"
    OTHER = "other"


class OnewayDirection(int, Enum):
    """Oneway traffic direction relative to way node ordering."""
    BOTH = 0
    FORWARD = 1
    REVERSE = -1


class AccessLevel(str, Enum):
    """Normalized access permissions for road participants."""
    PUBLIC = "public"
    PRIVATE = "private"
    DESTINATION = "destination"
    PERMISSIVE = "permissive"
    CUSTOMERS = "customers"
    DELIVERY = "delivery"
    AGRICULTURAL = "agricultural"
    FORESTRY = "forestry"
    NO = "no"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SpeedLimit:
    """Normalized speed limit information with source attribution."""
    speed_kmh: float | None
    unit: str
    raw_value: str
    source: str | None = None
    is_variable: bool = False
    is_unlimited: bool = False

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "speed_kmh": self.speed_kmh,
            "unit": self.unit,
            "raw_value": self.raw_value,
            "is_variable": self.is_variable,
            "is_unlimited": self.is_unlimited,
        }
        if self.source is not None:
            result["source"] = self.source
        return result


@dataclass(frozen=True)
class Barrier:
    """Normalized barrier obstruction on road or boundary."""
    barrier_type: str
    is_barrier: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "barrier_type": self.barrier_type,
            "is_barrier": self.is_barrier,
        }


@dataclass(frozen=True)
class ConditionalRule:
    """A conditional restriction rule parsed from OSM *:conditional tag."""
    attribute: str
    value: str
    condition: str
    raw_tag: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "attribute": self.attribute,
            "value": self.value,
            "condition": self.condition,
            "raw_tag": self.raw_tag,
        }


@dataclass
class NormalizedRoad:
    """Normalized road representation retaining unknowns and preserving raw tags."""
    way_id: int
    highway_class: HighwayClass
    is_link: bool
    oneway: OnewayDirection
    is_roundabout: bool
    general_access: AccessLevel
    motorcar_access: AccessLevel
    motorcycle_access: AccessLevel
    hgv_access: AccessLevel
    bicycle_access: AccessLevel
    foot_access: AccessLevel
    speed_limit: SpeedLimit | None
    barrier: Barrier | None
    conditionals: list[ConditionalRule] = field(default_factory=list)
    unknowns: dict[str, str] = field(default_factory=dict)
    raw_tags: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "way_id": self.way_id,
            "highway_class": self.highway_class.value,
            "is_link": self.is_link,
            "oneway": self.oneway.value,
            "is_roundabout": self.is_roundabout,
            "access": {
                "general": self.general_access.value,
                "motorcar": self.motorcar_access.value,
                "motorcycle": self.motorcycle_access.value,
                "hgv": self.hgv_access.value,
                "bicycle": self.bicycle_access.value,
                "foot": self.foot_access.value,
            },
            "speed_limit": self.speed_limit.to_dict() if self.speed_limit else None,
            "barrier": self.barrier.to_dict() if self.barrier else None,
            "conditionals": [c.to_dict() for c in self.conditionals],
            "unknowns": dict(sorted(self.unknowns.items())),
            "raw_tags": dict(sorted(self.raw_tags.items())),
        }
        return result


# --- Normalization Mappings & Constants ---

_RECOGNIZED_HIGHWAY_CLASSES = {
    "motorway": HighwayClass.MOTORWAY,
    "trunk": HighwayClass.TRUNK,
    "primary": HighwayClass.PRIMARY,
    "secondary": HighwayClass.SECONDARY,
    "tertiary": HighwayClass.TERTIARY,
    "unclassified": HighwayClass.UNCLASSIFIED,
    "residential": HighwayClass.RESIDENTIAL,
    "living_street": HighwayClass.LIVING_STREET,
    "service": HighwayClass.SERVICE,
    "pedestrian": HighwayClass.PEDESTRIAN,
    "track": HighwayClass.TRACK,
    "busway": HighwayClass.BUSWAY,
    "road": HighwayClass.ROAD,
    "path": HighwayClass.PATH,
    "cycleway": HighwayClass.CYCLEWAY,
    "footway": HighwayClass.FOOTWAY,
    "bridleway": HighwayClass.BRIDLEWAY,
    "steps": HighwayClass.STEPS,
}

_RECOGNIZED_ONEWAY_VALUES = {
    "yes": OnewayDirection.FORWARD,
    "1": OnewayDirection.FORWARD,
    "true": OnewayDirection.FORWARD,
    "-1": OnewayDirection.REVERSE,
    "reverse": OnewayDirection.REVERSE,
    "no": OnewayDirection.BOTH,
    "0": OnewayDirection.BOTH,
    "false": OnewayDirection.BOTH,
}

_RECOGNIZED_ACCESS_VALUES = {
    "yes": AccessLevel.PUBLIC,
    "true": AccessLevel.PUBLIC,
    "1": AccessLevel.PUBLIC,
    "designated": AccessLevel.PUBLIC,
    "permissive": AccessLevel.PERMISSIVE,
    "destination": AccessLevel.DESTINATION,
    "customers": AccessLevel.CUSTOMERS,
    "delivery": AccessLevel.DELIVERY,
    "agricultural": AccessLevel.AGRICULTURAL,
    "forestry": AccessLevel.FORESTRY,
    "private": AccessLevel.PRIVATE,
    "no": AccessLevel.NO,
    "false": AccessLevel.NO,
    "0": AccessLevel.NO,
    "discouraged": AccessLevel.DESTINATION,
}

_RECOGNIZED_BARRIER_TYPES = frozenset({
    "gate", "bollard", "lift_gate", "swing_gate", "cattle_grid",
    "toll_booth", "block", "cycle_barrier", "jersey_barrier",
    "fence", "wall", "border_control", "turnstile", "yes",
})

_MPH_TO_KMH = 1.609344


def normalize_road_tags(
    tags: dict[str, str],
    way_id: int = 0,
    allow_unknown: bool = False,
) -> NormalizedRoad:
    """Normalize raw OSM road tags into a structured NormalizedRoad entity.

    Args:
        tags: Raw OSM tags dictionary.
        way_id: Integer OSM way ID for context.
        allow_unknown: If False (default), raises RoadNormalizationError on unrecognized
            attributes. If True, captures unknowns in the `unknowns` dictionary and
            maintains raw_tags without raising.

    Returns:
        NormalizedRoad instance containing structured, validated attributes.

    Raises:
        RoadNormalizationError: When an attribute is unrecognized or invalid and
            `allow_unknown=False`.
    """
    raw_tags = dict(tags)
    unknowns: dict[str, str] = {}
    conditionals: list[ConditionalRule] = []

    # 1. Highway Class and Link Status
    highway_raw = tags.get("highway")
    if not highway_raw:
        if not allow_unknown:
            raise RoadNormalizationError(f"Way {way_id} is missing required 'highway' tag")
        unknowns["highway"] = ""
        highway_class = HighwayClass.OTHER
        is_link = False
    else:
        is_link = highway_raw.endswith("_link")
        base_highway = highway_raw[:-5] if is_link else highway_raw
        if base_highway in _RECOGNIZED_HIGHWAY_CLASSES:
            highway_class = _RECOGNIZED_HIGHWAY_CLASSES[base_highway]
        else:
            if not allow_unknown:
                raise RoadNormalizationError(
                    f"Way {way_id} has unrecognized highway class: '{highway_raw}'"
                )
            highway_class = HighwayClass.OTHER
            unknowns["highway"] = highway_raw

    # 2. Junction / Roundabout
    junction_raw = tags.get("junction")
    is_roundabout = False
    if junction_raw:
        if junction_raw in ("roundabout", "circular"):
            is_roundabout = True
        elif junction_raw in ("jughandle", "filter"):
            is_roundabout = False
        else:
            if not allow_unknown:
                raise RoadNormalizationError(
                    f"Way {way_id} has unrecognized junction type: '{junction_raw}'"
                )
            unknowns["junction"] = junction_raw

    # 3. Oneway Direction
    oneway_raw = tags.get("oneway")
    if oneway_raw is not None:
        normalized_oneway_key = oneway_raw.strip().lower()
        if normalized_oneway_key in _RECOGNIZED_ONEWAY_VALUES:
            oneway = _RECOGNIZED_ONEWAY_VALUES[normalized_oneway_key]
        else:
            if not allow_unknown:
                raise RoadNormalizationError(
                    f"Way {way_id} has unrecognized oneway value: '{oneway_raw}'"
                )
            unknowns["oneway"] = oneway_raw
            oneway = OnewayDirection.BOTH
    else:
        if is_roundabout or highway_class == HighwayClass.MOTORWAY:
            oneway = OnewayDirection.FORWARD
        else:
            oneway = OnewayDirection.BOTH

    # 4. Access Precedence & Mode Resolution
    def parse_access_value(key: str, val: str) -> AccessLevel:
        normalized = val.strip().lower()
        if normalized in _RECOGNIZED_ACCESS_VALUES:
            return _RECOGNIZED_ACCESS_VALUES[normalized]
        if not allow_unknown:
            raise RoadNormalizationError(
                f"Way {way_id} has unrecognized access value for '{key}': '{val}'"
            )
        unknowns[key] = val
        return AccessLevel.UNKNOWN

    general_access = AccessLevel.PUBLIC
    if "access" in tags:
        general_access = parse_access_value("access", tags["access"])

    vehicle_access = general_access
    if "vehicle" in tags:
        vehicle_access = parse_access_value("vehicle", tags["vehicle"])

    motor_vehicle_access = vehicle_access
    if "motor_vehicle" in tags:
        motor_vehicle_access = parse_access_value("motor_vehicle", tags["motor_vehicle"])

    motorcar_access = motor_vehicle_access
    if "motorcar" in tags:
        motorcar_access = parse_access_value("motorcar", tags["motorcar"])

    motorcycle_access = motor_vehicle_access
    if "motorcycle" in tags:
        motorcycle_access = parse_access_value("motorcycle", tags["motorcycle"])

    hgv_access = motor_vehicle_access
    if "hgv" in tags:
        hgv_access = parse_access_value("hgv", tags["hgv"])

    bicycle_access = vehicle_access
    if "bicycle" in tags:
        bicycle_access = parse_access_value("bicycle", tags["bicycle"])

    foot_access = general_access
    if "foot" in tags:
        foot_access = parse_access_value("foot", tags["foot"])

    # 5. Speed Limit & Source
    speed_limit: SpeedLimit | None = None
    maxspeed_raw = tags.get("maxspeed")
    source_maxspeed = tags.get("source:maxspeed") or tags.get("maxspeed:type")

    if maxspeed_raw:
        clean_speed = maxspeed_raw.strip().lower()
        if clean_speed == "none":
            speed_limit = SpeedLimit(
                speed_kmh=None, unit="km/h", raw_value=maxspeed_raw,
                source=source_maxspeed, is_unlimited=True,
            )
        elif clean_speed in ("signals", "variable"):
            speed_limit = SpeedLimit(
                speed_kmh=None, unit="km/h", raw_value=maxspeed_raw,
                source=source_maxspeed, is_variable=True,
            )
        elif clean_speed == "walk":
            speed_limit = SpeedLimit(
                speed_kmh=5.0, unit="km/h", raw_value=maxspeed_raw,
                source=source_maxspeed,
            )
        else:
            match_mph = re.match(r"^(\d+(?:\.\d+)?)\s*mph$", clean_speed)
            match_kmh = re.match(r"^(\d+(?:\.\d+)?)\s*(?:km/?h|kph)?$", clean_speed)
            if match_mph:
                val = float(match_mph.group(1))
                speed_limit = SpeedLimit(
                    speed_kmh=round(val * _MPH_TO_KMH, 1),
                    unit="mph",
                    raw_value=maxspeed_raw,
                    source=source_maxspeed,
                )
            elif match_kmh:
                val = float(match_kmh.group(1))
                speed_limit = SpeedLimit(
                    speed_kmh=val,
                    unit="km/h",
                    raw_value=maxspeed_raw,
                    source=source_maxspeed,
                )
            else:
                if not allow_unknown:
                    raise RoadNormalizationError(
                        f"Way {way_id} has unrecognized maxspeed value: '{maxspeed_raw}'"
                    )
                unknowns["maxspeed"] = maxspeed_raw

    # 6. Barrier
    barrier: Barrier | None = None
    barrier_raw = tags.get("barrier")
    if barrier_raw:
        clean_barrier = barrier_raw.strip().lower()
        if clean_barrier in _RECOGNIZED_BARRIER_TYPES:
            barrier = Barrier(barrier_type=clean_barrier, is_barrier=True)
        else:
            if not allow_unknown:
                raise RoadNormalizationError(
                    f"Way {way_id} has unrecognized barrier type: '{barrier_raw}'"
                )
            unknowns["barrier"] = barrier_raw
            barrier = Barrier(barrier_type=clean_barrier, is_barrier=True)

    # 7. Conditional Rules (*:conditional)
    for tag_key, tag_val in sorted(tags.items()):
        if tag_key.endswith(":conditional"):
            base_attr = tag_key[:-12]
            parts = tag_val.split("@", 1)
            conditional_value = parts[0].strip()
            cond = parts[1].strip() if len(parts) > 1 else ""
            conditionals.append(ConditionalRule(
                attribute=base_attr,
                value=conditional_value,
                condition=cond,
                raw_tag=f"{tag_key}={tag_val}",
            ))

    return NormalizedRoad(
        way_id=way_id,
        highway_class=highway_class,
        is_link=is_link,
        oneway=oneway,
        is_roundabout=is_roundabout,
        general_access=general_access,
        motorcar_access=motorcar_access,
        motorcycle_access=motorcycle_access,
        hgv_access=hgv_access,
        bicycle_access=bicycle_access,
        foot_access=foot_access,
        speed_limit=speed_limit,
        barrier=barrier,
        conditionals=conditionals,
        unknowns=unknowns,
        raw_tags=raw_tags,
    )
