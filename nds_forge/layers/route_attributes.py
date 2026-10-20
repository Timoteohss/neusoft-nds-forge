"""ROUTE link attributes used by the versioned Curitiba v9 experiment.

The policy is evidence from the legacy profile, not a legal or national
default. Keep profile values explicit and report the fallback provenance.
"""
from __future__ import annotations

import math
import re

SCALE = (1 << 31) / 180


def bearing_degrees(a, b):
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    delta = lon2 - lon1
    return math.degrees(math.atan2(math.sin(delta) * math.cos(lat2),
                          math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(delta))) % 360


def distance_meters(a, b):
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1, max(0, h))))


def build_link_attributes(a, b, tags, direction, policy):
    """Encode the observed basic fields; policy contains FRC and speed tables."""
    if a == b:
        raise ValueError("Quantized segment has zero length")
    a, b = [v / SCALE for v in a], [v / SCALE for v in b]
    highway = tags.get("highway", "residential").removesuffix("_link")
    speed_tag = tags.get("maxspeed", "")
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(mph|km/h|kmh)?\s*", speed_tag)
    if match and float(match[1]) > 0:
        speed = float(match[1]) * (1.609344 if match[2] == "mph" else 1)
        speed_source = "OSM maxspeed used as prototype travel speed"
    else:
        fallback = policy["fallback_speed_kmh"]
        if highway not in fallback:
            raise ValueError(f"No explicit fallback speed for highway={highway!r}")
        speed = fallback[highway]
        speed_source = "Explicit versioned profile fallback; legal limit unknown"
    speed = max(1, min(255, round(float(speed))))
    frc = policy["frc_by_highway"].get(highway)
    if frc is None:
        raise ValueError(f"No explicit FRC mapping for highway={highway!r}")
    if direction not in {"forward": 1, "reverse": 2, "both": 3, "unknown": 0}:
        raise ValueError(f"Unknown direction enum: {direction!r}")
    dirs = {"forward": 1, "reverse": 2, "both": 3, "unknown": 0}
    return {"length_raw": max(1, round(distance_meters(a, b) * 100)),
            "road_raw": (int(frc) << 12) | (dirs[direction] << 6), "access_raw": 0,
            "scalar8": speed,
            "scalar6_a": int(math.floor(bearing_degrees(a, b) / 5.625 + 0.5)) & 63,
            "scalar6_b": int(math.floor(bearing_degrees(b, a) / 5.625 + 0.5)) & 63,
            "speed_source": speed_source}
