"""ROUTE tile math shared with the pinned v9 route compiler."""
from __future__ import annotations

from .bmd_codec import nds_coordinate, tile_for, tile_origin


def split_segment_by_tile(a, b, level, shift):
    """Split lon/lat segment at tile borders and quantize per tile."""
    xa, ya = nds_coordinate(a[0]), nds_coordinate(a[1]) & 0x7fffffff
    xb, yb = nds_coordinate(b[0]), nds_coordinate(b[1]) & 0x7fffffff
    step = 1 << (31 - level)
    cuts = {0.0, 1.0}
    for start, end, mask in ((xa, xb, 0xffffffff), (ya, yb, 0x7fffffff)):
        start &= mask
        end &= mask
        low, high = sorted((start, end))
        first = (low // step + 1) * step
        for boundary in range(first, high, step):
            t = (boundary - start) / (end - start) if end != start else 0
            if 1e-12 < t < 1 - 1e-12:
                cuts.add(t)
    for lo, hi in zip(sorted(cuts), sorted(cuts)[1:]):
        if hi - lo <= 1e-12:
            continue
        mid = (lo + hi) / 2
        midpoint = (a[0] + (b[0] - a[0]) * mid, a[1] + (b[1] - a[1]) * mid)
        tile = tile_for(*midpoint, level)
        origin = tile_origin(tile, level)
        endpoints = []
        for t in (lo, hi):
            point = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            coord = (nds_coordinate(point[0]), nds_coordinate(point[1]))
            endpoints.append(tuple(origin[i] + round((coord[i] - origin[i]) / (1 << shift)) * (1 << shift)
                                   for i in range(2)))
        if endpoints[0] != endpoints[1]:
            yield tile, endpoints
