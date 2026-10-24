"""Shared validation for experimental BMD level policies and metadata.

Gaps are allowed (no coverage is claimed there); overlaps are rejected until
the native consumer's precedence has been established.
"""
import itertools


def validate_bmd_levels(levels: object) -> None:
    if not isinstance(levels, list) or not levels:
        raise ValueError("bmd_levels must be a non-empty list")
    seen = set()
    for entry in levels:
        if not isinstance(entry, dict):
            raise ValueError("bmd_levels entries must be objects")  # noqa: TRY004 -- Policy validation consistently raises ValueError, including malformed JSON types.
        for field in ("level", "shift", "min_scale", "max_scale"):
            if type(entry.get(field)) is not int:
                raise ValueError(f"BMD {field} must be an explicit integer")
        level, shift = entry["level"], entry["shift"]
        if not 0 <= level <= 15 or not 0 <= shift < 31 - level:
            raise ValueError("Invalid BMD level/shift")
        if level in seen:
            raise ValueError(f"Duplicate BMD level: {level}")
        seen.add(level)
        frc_max = entry.get("frc_max")
        if frc_max is not None and (type(frc_max) is not int or not 0 <= frc_max <= 7):
            raise ValueError("BMD frc_max must be an integer in range 0..7")
        if not 1 <= entry["min_scale"] <= entry["max_scale"] <= 0xffffffff:
            raise ValueError("BMD scales must satisfy 1 <= min_scale <= max_scale <= uint32 max")
    ordered = sorted(levels, key=lambda entry: entry["min_scale"])
    for previous, current in itertools.pairwise(ordered):
        if current["min_scale"] <= previous["max_scale"]:
            raise ValueError("Overlapping BMD scale ranges are not supported")
