"""Automatic grid detection (docs/06 C.1): cell size from columns, rotation from walls/beams, site origin."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any, Iterable, Sequence as Seq

from .model import ElementsDoc, JSON

MIN_CELL, MAX_CELL, DEFAULT_CELL = 3.0, 12.0, 6.0
ROTATION_SNAP = 5.0
CELL_SNAP = 0.5
COINCIDENT_M = 0.5
ORIENT_CLASSES = ("IfcWall", "IfcWallStandardCase", "IfcBeam")


@dataclass
class GridItem:
    """One element for detection: class, bbox (model x, y-north, z-up) and optional long-axis angle."""

    ifc_class: str
    lo: tuple[float, float, float]
    hi: tuple[float, float, float]
    axis_deg: float | None = None       # direction of the plan long axis in degrees (any modulus)

    @property
    def centre(self) -> tuple[float, float]:
        return (self.lo[0] + self.hi[0]) / 2, (self.lo[1] + self.hi[1]) / 2


def items_from_elements(doc: ElementsDoc) -> list[GridItem]:
    """Grid items from an elements document (axis-aligned bboxes only, so no axis angles)."""
    out = []
    for e in doc.elements:
        if e.bbox:
            out.append(GridItem(e.ifc_class, tuple(e.bbox["min"]), tuple(e.bbox["max"])))        # type: ignore[arg-type]
    return out


def snap(value: float, step: float) -> float:
    return round(value / step) * step


def nearest_neighbour_distances(points: Seq[tuple[float, float]]) -> list[float]:
    """Distance to the nearest *other* point (coincident points ignored), via a bucket grid."""
    n = len(points)
    if n < 2:
        return []
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    bucket = max(1.0, span / math.sqrt(n) * 1.5)
    grid: dict[tuple[int, int], list[int]] = {}
    for i, (x, y) in enumerate(points):
        grid.setdefault((int(x // bucket), int(y // bucket)), []).append(i)
    out = []
    for i, (x, y) in enumerate(points):
        bx, by = int(x // bucket), int(y // bucket)
        best = math.inf
        ring = 1
        while True:
            for gx in range(bx - ring, bx + ring + 1):
                for gy in range(by - ring, by + ring + 1):
                    for j in grid.get((gx, gy), ()):
                        if j != i:
                            d = math.hypot(points[j][0] - x, points[j][1] - y)
                            if COINCIDENT_M <= d < best:
                                best = d
            if best < math.inf and best <= ring * bucket:
                break
            ring += 1
            if ring > 3 and best == math.inf and ring * bucket > span * 2:
                break
        if best < math.inf:
            out.append(best)
    return out


def estimate_cell_size(items: Iterable[GridItem], snap_m: float = CELL_SNAP, min_cell: float = MIN_CELL,
                       max_cell: float = MAX_CELL) -> tuple[float, int]:
    """(cell size m, columns used): median column NN spacing snapped to 0.5 m, clamped 3..12; 6 if unknown."""
    cols = [it.centre for it in items if it.ifc_class == "IfcColumn"]
    dists = nearest_neighbour_distances(cols)
    if not dists:
        return DEFAULT_CELL, len(cols)
    return min(max_cell, max(min_cell, snap(statistics.median(dists), snap_m))), len(cols)


def _fold90(deg: float) -> float:
    """Fold an angle to (-45, 45] (a square grid repeats every 90 degrees)."""
    d = (deg + 45.0) % 90.0 - 45.0
    return 45.0 if d <= -45.0 else d


def estimate_rotation(items: Seq[GridItem]) -> float:
    """Dominant plan direction of walls and beams (length weighted), snapped to 5 degrees.

    Falls back to the nearest-neighbour directions of columns, then 0. Result in (-45, 45].
    """
    samples: list[tuple[float, float]] = []                    # (angle deg, weight)
    for it in items:
        if it.ifc_class not in ORIENT_CLASSES:
            continue
        dx, dy = it.hi[0] - it.lo[0], it.hi[1] - it.lo[1]
        length = max(dx, dy)
        if length <= 0:
            continue
        angle = it.axis_deg if it.axis_deg is not None else (0.0 if dx >= dy else 90.0)
        samples.append((angle, length))
    if not samples:
        cols = [it.centre for it in items if it.ifc_class == "IfcColumn"]
        for i, (x, y) in enumerate(cols[:2000]):
            best, vec = math.inf, None
            for j, (u, v) in enumerate(cols[:2000]):
                d = math.hypot(u - x, v - y)
                if j != i and COINCIDENT_M <= d < best:
                    best, vec = d, (u - x, v - y)
            if vec:
                samples.append((math.degrees(math.atan2(vec[1], vec[0])), 1.0))
    if not samples:
        return 0.0
    c = sum(w * math.cos(math.radians(4 * a)) for a, w in samples)
    s = sum(w * math.sin(math.radians(4 * a)) for a, w in samples)
    if abs(c) < 1e-9 and abs(s) < 1e-9:
        return 0.0
    rot = math.degrees(math.atan2(s, c)) / 4.0
    return _fold90(snap(rot, ROTATION_SNAP))


def rotate(x: float, y: float, deg: float) -> tuple[float, float]:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return x * c - y * s, x * s + y * c


def detect_grid(items: Seq[GridItem], *, snap_m: float = CELL_SNAP, min_cell: float = MIN_CELL,
                max_cell: float = MAX_CELL, extent: Seq[float] | None = None, rotation: float | None = None) -> JSON:
    """Estimate ``cell_size_m``, ``rotation_deg`` and ``origin`` for a set of elements.

    ``origin`` is the model-space position of the minimum corner of the site bbox measured in the
    rotated frame (``origin_rotated`` is the same corner in rotated-frame coordinates).
    """
    if not items and extent is None:
        return {"cell_size_m": DEFAULT_CELL, "rotation_deg": 0.0, "origin": [0.0, 0.0, 0.0],
                "origin_rotated": [0.0, 0.0], "columns": 0, "items": 0}
    cell, ncols = estimate_cell_size(items, snap_m, min_cell, max_cell)
    rot = estimate_rotation(items) if rotation is None else float(rotation)
    us: list[float] = []
    vs: list[float] = []
    if extent is not None:                        # overall site bbox (min x, min y, max x, max y)
        for x in (extent[0], extent[2]):
            for y in (extent[1], extent[3]):
                u, v = rotate(x, y, -rot)
                us.append(u)
                vs.append(v)
    for it in items:
        for x in (it.lo[0], it.hi[0]):
            for y in (it.lo[1], it.hi[1]):
                u, v = rotate(x, y, -rot)
                us.append(u)
                vs.append(v)
    u0, v0 = min(us), min(vs)
    ox, oy = rotate(u0, v0, rot)
    return {"cell_size_m": cell, "rotation_deg": rot, "origin": [round(ox, 3), round(oy, 3), 0.0],
            "origin_rotated": [round(u0, 3), round(v0, 3)], "columns": ncols, "items": len(items)}


def cells_for_bbox_rotated(lo: Seq[float], hi: Seq[float], origin: Seq[float], cell_size: float,
                           rotation_deg: float, eps: float = 1e-6) -> list[tuple[int, int]]:
    """Cells ``(x, z)`` covered by a bbox footprint in a grid rotated by ``rotation_deg`` about ``origin``."""
    us, vs = [], []
    for x in (lo[0], hi[0]):
        for y in (lo[1], hi[1]):
            u, v = rotate(x - origin[0], y - origin[1], -rotation_deg)
            us.append(u)
            vs.append(v)
    x0, x1 = math.floor(min(us) / cell_size + eps), math.floor(max(us) / cell_size - eps)
    z0, z1 = math.floor(min(vs) / cell_size + eps), math.floor(max(vs) / cell_size - eps)
    x1, z1 = max(x0, x1), max(z0, z1)
    return [(x, z) for z in range(z0, z1 + 1) for x in range(x0, x1 + 1)]
