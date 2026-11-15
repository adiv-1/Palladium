"""Small, dependency-free box/polygon helpers."""
from __future__ import annotations

import math
from typing import Sequence

from ..types import Box


def area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def center(b: Box) -> tuple[float, float]:
    return (b[0] + b[2]) * 0.5, (b[1] + b[3]) * 0.5


def foot_point(b: Box) -> tuple[float, float]:
    """Bottom-centre of a person box: the best single-point proxy for where they stand."""
    return (b[0] + b[2]) * 0.5, b[3]


def intersection(a: Box, b: Box) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def iou(a: Box, b: Box) -> float:
    inter = intersection(a, b)
    if inter <= 0:
        return 0.0
    return inter / (area(a) + area(b) - inter)


def overlap_frac(inner: Box, outer: Box) -> float:
    """Fraction of ``inner`` that lies inside ``outer``."""
    a = area(inner)
    return intersection(inner, outer) / a if a > 0 else 0.0


def expand(b: Box, dx: float, dy_top: float = 0.0, dy_bottom: float = 0.0) -> Box:
    return b[0] - dx, b[1] - dy_top, b[2] + dx, b[3] + dy_bottom


def scale_box(b: Box, factor: float) -> Box:
    cx, cy = center(b)
    hw, hh = (b[2] - b[0]) * 0.5 * factor, (b[3] - b[1]) * 0.5 * factor
    return cx - hw, cy - hh, cx + hw, cy + hh


def dist(p: tuple[float, float], q: tuple[float, float]) -> float:
    return math.hypot(p[0] - q[0], p[1] - q[1])


def point_in_box(p: tuple[float, float], b: Box) -> bool:
    return b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3]


def point_in_polygon(p: tuple[float, float], poly: Sequence[tuple[float, float]]) -> bool:
    """Ray casting; ``poly`` is a list of vertices in order (either winding)."""
    x, y = p
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def saturate(value: float, lo: float, hi: float) -> float:
    """Map ``value`` linearly onto [0, 1] between ``lo`` and ``hi``."""
    if hi <= lo:
        return 1.0 if value >= hi else 0.0
    return min(1.0, max(0.0, (value - lo) / (hi - lo)))
