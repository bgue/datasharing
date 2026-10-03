"""IFC -> elements (streaming) using ifcopenshell (docs/06 C.1, C.2).

Pipeline: spatial structure and relationships are read once into lookup tables; the geometry iterator
(multi-threaded) yields world-space bboxes (placement fallback when geometry fails); a grid is chosen
(auto / fixed / chainage); zones come from IfcSpace/IfcZone with space-tag rules (auto blocks as
fallback); elements are then emitted in chunks into a single elements document or into
``elements.part-N.json[.gz]`` files, so memory stays bounded by compact per-element arrays.

Pure helpers (storey indexing, cell computation, quantity mapping) work without ifcopenshell and are
unit tested; ``available()`` reports whether ifcopenshell imported.
"""
from __future__ import annotations

import math
import statistics
import sys
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from . import grid_detect, zoning
from .bundle import GZ, _dump
from .config import ProjectConfig
from .model import JSON
from .visuals import visual_for

try:
    import ifcopenshell
    import ifcopenshell.geom as _ifc_geom
    import ifcopenshell.util.element as _ifc_element
    import ifcopenshell.util.placement as _ifc_placement
    import ifcopenshell.util.unit as _ifc_unit
    import numpy as _np
    HAVE_IFCOPENSHELL = True
except ImportError:
    ifcopenshell = None            # type: ignore[assignment]
    HAVE_IFCOPENSHELL = False

Cell = tuple[int, int]
Vec = tuple[float, float, float]

SKIP_CLASSES = {"IfcOpeningElement", "IfcVirtualElement", "IfcFeatureElementSubtraction"}
SPLIT_CLASSES = {"IfcColumn", "IfcWall", "IfcWallStandardCase", "IfcCurtainWall", "IfcPipeSegment",
                 "IfcDuctSegment", "IfcCableCarrierSegment", "IfcStair", "IfcPile"}
VERTICAL_SPLIT_MIN_STOREYS = 2
MAX_PROPERTIES = 40

_SYSTEM_DISCIPLINE = {
    "VENTILATION": "mechanical", "AIRCONDITIONING": "mechanical", "HEATING": "mechanical",
    "COOLING": "mechanical", "DOMESTICCOLDWATER": "plumbing", "DOMESTICHOTWATER": "plumbing",
    "SANITARY": "plumbing", "STORMWATER": "civil", "DRAINAGE": "civil", "FIREPROTECTION": "fire",
    "ELECTRICAL": "electrical", "LIGHTING": "electrical", "COMMUNICATION": "electrical",
    "DATA": "electrical", "OXYGEN": "medical", "MEDICALAIR": "medical",
}
_KEYWORD_DISCIPLINE = [("fire", "fire"), ("spr", "fire"), ("elec", "electrical"), ("cable", "electrical"),
                       ("mg", "medical"), ("medical", "medical"), ("water", "plumbing"),
                       ("san", "plumbing"), ("drain", "civil"), ("air", "mechanical"),
                       ("hvac", "mechanical"), ("ahu", "mechanical"), ("chw", "mechanical"),
                       ("hhw", "mechanical"), ("instr", "instrumentation"), ("pipe", "process"),
                       ("process", "process")]


def available() -> bool:
    """True when ifcopenshell could be imported."""
    return HAVE_IFCOPENSHELL


# ---------------------------------------------------------------------------- pure helpers
def index_storeys(elevations: Sequence[tuple[str, float]]) -> dict[str, int]:
    """Storey index by elevation order: the lowest storey at or above -0.5 m is 0 (ground).

    ``elevations`` is ``[(storey_key, elevation_m)]``. Storeys within 5 cm of each other (e.g. the ground
    floors of two buildings) share an index. Storeys below ground get negative indices; if every storey
    is below ground the highest one is index 0.
    """
    levels = sorted({round(z, 2) for _, z in elevations})
    if not levels:
        return {}
    ground = next((i for i, z in enumerate(levels) if z >= -0.5), len(levels) - 1)
    rank = {z: i - ground for i, z in enumerate(levels)}
    return {key: rank[round(z, 2)] for key, z in elevations}


def cells_for_bbox(lo: Sequence[float], hi: Sequence[float], origin: Sequence[float],
                   cell_size: float, eps: float = 1e-6) -> list[Cell]:
    """Grid cells ``[x, z]`` covered by the plan footprint of a bbox (model x, y-north)."""
    x0 = math.floor((lo[0] - origin[0]) / cell_size + eps)
    z0 = math.floor((lo[1] - origin[1]) / cell_size + eps)
    x1 = math.floor((hi[0] - origin[0]) / cell_size - eps)
    z1 = math.floor((hi[1] - origin[1]) / cell_size - eps)
    x1, z1 = max(x0, x1), max(z0, z1)
    return [(x, z) for z in range(z0, z1 + 1) for x in range(x0, x1 + 1)]


def storeys_overlapping(zlo: float, zhi: float, storeys: Sequence[tuple[str, float, float]],
                        min_fraction: float = 0.25) -> list[tuple[str, float]]:
    """Storeys (key, overlap fraction) whose [bottom, top) band a vertical extent covers.

    ``storeys`` is ``[(key, bottom_m, top_m)]``. Fractions are relative to the element extent;
    storeys overlapped by less than ``min_fraction`` of the storey height are ignored.
    """
    out = []
    extent = max(zhi - zlo, 1e-9)
    for key, bot, top in storeys:
        ov = min(zhi, top) - max(zlo, bot)
        if ov > 0 and ov >= min_fraction * (top - bot):
            out.append((key, ov / extent))
    if not out:
        mid = (zlo + zhi) / 2
        best = min(storeys, key=lambda s: abs((s[1] + s[2]) / 2 - mid))
        out = [(best[0], 1.0)]
    total = sum(f for _, f in out)
    return [(k, f / total) for k, f in out]


def tile_zones(cells_by_storey: dict[str, set[Cell]], block: tuple[int, int] = (3, 3),
               max_crews: int = 2) -> tuple[list[dict[str, Any]], dict[tuple[str, Cell], str]]:
    """Auto zones: blocks of up to ``block`` cells per storey, only over occupied cells.

    Returns ``(zones, zone_of)`` where ``zone_of[(storey_id, cell)]`` is the zone id.
    """
    bw, bd = max(1, block[0]), max(1, block[1])
    zones: list[dict[str, Any]] = []
    zone_of: dict[tuple[str, Cell], str] = {}
    for sid in sorted(cells_by_storey):
        blocks: dict[tuple[int, int], list[Cell]] = defaultdict(list)
        for c in sorted(cells_by_storey[sid], key=lambda c: (c[1], c[0])):
            blocks[(c[1] // bd, c[0] // bw)].append(c)
        for n, key in enumerate(sorted(blocks), start=1):
            zid = f"{sid}-Z{n}"
            zones.append({"id": zid, "name": f"{sid} zone {n}", "storey_id": sid,
                          "cells": [[c[0], c[1]] for c in blocks[key]], "max_crews": max_crews, "tags": []})
            for c in blocks[key]:
                zone_of[(sid, c)] = zid
    return zones, zone_of


_PLAN_CLASSES = {"IfcSlab", "IfcRoof", "IfcCovering", "IfcPavement", "IfcCourse", "IfcPlate", "IfcFooting",
                 "IfcEarthworksCut", "IfcEarthworksFill", "IfcRamp", "IfcRampFlight"}


def quantities_from_bbox(ifc_class: str, lo: Sequence[float], hi: Sequence[float]) -> dict[str, float]:
    """Fallback quantities from a bbox (metres): plan area for plan-like classes, side area otherwise."""
    dx, dy, dz = (max(0.0, hi[i] - lo[i]) for i in range(3))
    long_side = max(dx, dy)
    area = dx * dy if ifc_class in _PLAN_CLASSES else long_side * dz
    return {"volume_m3": round(dx * dy * dz, 3), "area_m2": round(area, 3),
            "length_m": round(max(dx, dy, dz), 3), "count": 1.0}


# Quantity name (lower case) -> (key, power of the unit scale, divisor); first match wins.
_QTO_MAP: list[tuple[str, str, int, float]] = [
    ("netvolume", "volume_m3", 3, 1.0), ("grossvolume", "volume_m3", 3, 1.0), ("volume", "volume_m3", 3, 1.0),
    ("netsidearea", "area_m2", 2, 1.0), ("netfootprintarea", "area_m2", 2, 1.0), ("netfloorarea", "area_m2", 2, 1.0),
    ("netarea", "area_m2", 2, 1.0), ("grosssidearea", "area_m2", 2, 1.0), ("grossfootprintarea", "area_m2", 2, 1.0),
    ("grossfloorarea", "area_m2", 2, 1.0), ("grossarea", "area_m2", 2, 1.0), ("area", "area_m2", 2, 1.0),
    ("length", "length_m", 1, 1.0), ("netweight", "weight_t", 0, 1000.0), ("grossweight", "weight_t", 0, 1000.0),
    ("weight", "weight_t", 0, 1000.0), ("count", "count", 0, 1.0),
]


def map_quantity_sets(qtos: dict[str, dict[str, Any]], unit_scale: float = 1.0) -> dict[str, float]:
    """Flatten IfcElementQuantity property sets into the schema's quantity keys (metres, tonnes)."""
    flat: dict[str, Any] = {}
    for props in qtos.values():
        for k, v in props.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and k != "id":
                flat.setdefault(k.lower(), float(v))
    out: dict[str, float] = {}
    for name, key, power, div in _QTO_MAP:
        if key in out or name not in flat or flat[name] <= 0:
            continue
        out[key] = round(flat[name] * (unit_scale ** power) / div, 6)
    return out


def guess_system_discipline(name: str, predefined: str | None = None) -> str:
    """Discipline enum value for a system from its predefined type or name."""
    if predefined and predefined.upper() in _SYSTEM_DISCIPLINE:
        return _SYSTEM_DISCIPLINE[predefined.upper()]
    low = name.lower()
    for key, disc in _KEYWORD_DISCIPLINE:
        if key in low:
            return disc
    return "general"




def plan_axis_deg(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Direction (degrees) of the principal plan axis of a point cloud (PCA of x, y); None if degenerate."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx + syy < 1e-12 or abs(sxx - syy) + abs(sxy) < 1e-9 * (sxx + syy):
        return None
    return math.degrees(0.5 * math.atan2(2 * sxy, sxx - syy))


def chainage_cells(lo: Sequence[float], hi: Sequence[float], origin: Sequence[float], axis: str, cell_length: float,
                   lane_width: float, lanes: int) -> list[Cell]:
    """Cells ``(along, lane)`` of a bbox in chainage mode (along the ``x``/``y`` axis, lanes across it)."""
    ai, ci = (0, 1) if axis == "x" else (1, 0)
    a0 = max(0, math.floor((lo[ai] - origin[ai]) / cell_length + 1e-6))
    a1 = max(a0, math.floor((hi[ai] - origin[ai]) / cell_length - 1e-6))
    c0 = min(lanes - 1, max(0, math.floor((lo[ci] - origin[ci]) / lane_width + 1e-6)))
    c1 = min(lanes - 1, max(c0, math.floor((hi[ci] - origin[ci]) / lane_width - 1e-6)))
    return [(a, c) for c in range(c0, c1 + 1) for a in range(a0, a1 + 1)]


# ---------------------------------------------------------------------------- results
@dataclass
class ExtractResult:
    """What :func:`extract_to` produced."""

    head: JSON                       # project, storeys, zones, systems (schema_version included)
    count: int
    files: list[Path]
    elements: list[JSON] | None = None   # only for in-memory (single document) extraction
    timings: dict[str, float] = field(default_factory=dict)

    def document(self) -> JSON:
        if self.elements is None:
            raise ValueError("extraction was streamed to part files")
        return {**self.head, "elements": self.elements}


@dataclass
class _Rec:
    """Compact per-element record kept in memory between passes."""

    sid: int                          # IFC step id
    guid: str
    cls: str
    lo: tuple[float, float, float]
    hi: tuple[float, float, float]
    storey: str                       # IfcBuildingStorey GlobalId
    building: str | None
    placement_only: bool = False


# ---------------------------------------------------------------------------- relationship tables
def _wrapped(v: Any) -> Any:
    """Plain Python value of an IFC property value entity (or None)."""
    if v is None:
        return None
    w = v.wrappedValue if hasattr(v, "wrappedValue") else v
    return w if isinstance(w, (str, int, float, bool)) else None


_QTY_ATTRS = ("LengthValue", "AreaValue", "VolumeValue", "WeightValue", "CountValue", "TimeValue")


def _read_property_tables(model: Any, want: set[int]) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, dict[str, float]]]]:
    """element step id -> flat scalar properties, and -> quantity sets (name -> {quantity: value})."""
    props: dict[int, dict[str, Any]] = {}
    qtos: dict[int, dict[str, dict[str, float]]] = {}
    for rel in model.by_type("IfcRelDefinesByProperties"):
        objs = [o.id() for o in rel.RelatedObjects if o.id() in want]
        if not objs:
            continue
        d = rel.RelatingPropertyDefinition
        if d is None:
            continue
        if d.is_a("IfcPropertySet"):
            vals: dict[str, Any] = {}
            for p in d.HasProperties or ():
                if p.is_a("IfcPropertySingleValue"):
                    v = _wrapped(p.NominalValue)
                    if v is not None:
                        vals[p.Name] = v
            for oid in objs:
                cur = props.setdefault(oid, {})
                for k, v in vals.items():
                    if len(cur) < MAX_PROPERTIES:
                        cur[k] = v
        elif d.is_a("IfcElementQuantity"):
            q: dict[str, float] = {}
            for item in d.Quantities or ():
                for attr in _QTY_ATTRS:
                    if hasattr(item, attr) and getattr(item, attr) is not None:
                        q[item.Name] = float(getattr(item, attr))
                        break
            for oid in objs:
                qtos.setdefault(oid, {})[d.Name or "Qto"] = q
    return props, qtos


def _material_name(mat: Any) -> str | None:
    if mat is None:
        return None
    if mat.is_a("IfcMaterial"):
        return mat.Name
    names: list[str] = []
    if mat.is_a("IfcMaterialLayerSetUsage"):
        mat = mat.ForLayerSet
    if mat.is_a("IfcMaterialLayerSet"):
        names = [ly.Material.Name for ly in mat.MaterialLayers or () if ly.Material is not None and ly.Material.Name]
    elif mat.is_a("IfcMaterialConstituentSet"):
        names = [c.Material.Name for c in mat.MaterialConstituents or () if c.Material is not None and c.Material.Name]
    elif mat.is_a("IfcMaterialProfileSetUsage") or mat.is_a("IfcMaterialProfileSet"):
        ps = mat.ForProfileSet if mat.is_a("IfcMaterialProfileSetUsage") else mat
        names = [p.Material.Name for p in ps.MaterialProfiles or () if p.Material is not None and p.Material.Name]
    elif mat.is_a("IfcMaterialList"):
        names = [m.Name for m in mat.Materials or () if m.Name]
    return " / ".join(dict.fromkeys(names)) or None


def _read_relation_tables(model: Any, want: set[int]) -> tuple[dict[int, str], dict[int, str], dict[int, str],
                                                               dict[str, JSON]]:
    """(material name, system id, host guid, systems) per element step id via the relationship entities."""
    material: dict[int, str] = {}
    for rel in model.by_type("IfcRelAssociatesMaterial"):
        name = _material_name(rel.RelatingMaterial)
        if name:
            for o in rel.RelatedObjects:
                if o.id() in want:
                    material[o.id()] = name
    system: dict[int, str] = {}
    systems: dict[str, JSON] = {}
    for rel in model.by_type("IfcRelAssignsToGroup"):
        grp = rel.RelatingGroup
        if grp is None or not grp.is_a("IfcSystem"):
            continue
        sid = grp.Name or grp.GlobalId
        for o in rel.RelatedObjects:
            if o.id() in want:
                system[o.id()] = sid
                systems.setdefault(sid, {"id": sid, "name": grp.Description or sid,
                                         "discipline": guess_system_discipline(sid, getattr(grp, "PredefinedType", None))})
    host_of_opening: dict[int, str] = {}
    for rel in model.by_type("IfcRelVoidsElement"):
        if rel.RelatingBuildingElement is not None and rel.RelatedOpeningElement is not None:
            host_of_opening[rel.RelatedOpeningElement.id()] = rel.RelatingBuildingElement.GlobalId
    host: dict[int, str] = {}
    for rel in model.by_type("IfcRelFillsElement"):
        h = host_of_opening.get(rel.RelatingOpeningElement.id()) if rel.RelatingOpeningElement is not None else None
        if h and rel.RelatedBuildingElement is not None and rel.RelatedBuildingElement.id() in want:
            host[rel.RelatedBuildingElement.id()] = h
    return material, system, host, systems


def _container_map(model: Any) -> dict[int, int]:
    """element step id -> containing storey/space step id (IfcRelContainedInSpatialStructure)."""
    out: dict[int, int] = {}
    for rel in model.by_type("IfcRelContainedInSpatialStructure"):
        s = rel.RelatingStructure
        if s is None:
            continue
        for o in rel.RelatedElements:
            out[o.id()] = s.id()
    return out


def _parent_map(model: Any) -> dict[int, int]:
    """object step id -> aggregating parent step id (IfcRelAggregates)."""
    out: dict[int, int] = {}
    for rel in model.by_type("IfcRelAggregates"):
        if rel.RelatingObject is None:
            continue
        for o in rel.RelatedObjects:
            out[o.id()] = rel.RelatingObject.id()
    return out


# ---------------------------------------------------------------------------- geometry
def _geom_settings() -> Any:
    s = _ifc_geom.settings()
    s.set("use-world-coords", True)
    return s


def _placement_point(el: Any, unit_scale: float) -> tuple[float, float, float] | None:
    pl = getattr(el, "ObjectPlacement", None)
    if pl is None:
        return None
    try:
        m = _ifc_placement.get_local_placement(pl)
        return float(m[0][3]) * unit_scale, float(m[1][3]) * unit_scale, float(m[2][3]) * unit_scale
    except Exception:
        return None


def _bboxes(model: Any, elements: Sequence[Any], threads: int, unit_scale: float,
            want_axis_classes: set[str]) -> tuple[dict[int, tuple[Any, Any, float | None]], int]:
    """step id -> (lo, hi, axis_deg) from the multi-threaded geometry iterator; placement fallback.

    Returns ``(boxes, fallback count)``; elements with neither geometry nor placement are absent.
    """
    boxes: dict[int, tuple[Any, Any, float | None]] = {}
    with_rep = [e for e in elements if getattr(e, "Representation", None) is not None]
    if with_rep:
        try:
            it = _ifc_geom.iterator(_geom_settings(), model, threads, include=with_rep)
            ok = it.initialize()
        except Exception:
            ok, it = False, None
        while ok:
            try:
                shape = it.get()
                v = _np.asarray(shape.geometry.verts, dtype=float).reshape(-1, 3)
                if v.size:
                    lo, hi = v.min(axis=0), v.max(axis=0)
                    axis = None
                    ent = model.by_id(shape.id)
                    if ent.is_a() in want_axis_classes:
                        axis = plan_axis_deg(v[:, 0].tolist(), v[:, 1].tolist())
                    boxes[shape.id] = (lo, hi, axis)
            except Exception:
                pass
            try:
                if not it.next():
                    break
            except Exception:
                break
    fallback = 0
    for e in elements:
        if e.id() in boxes:
            continue
        p = _placement_point(e, unit_scale)
        if p is not None:
            pt = _np.array(p)
            boxes[e.id()] = (pt, pt, None)
            fallback += 1
    return boxes, fallback


# ---------------------------------------------------------------------------- main
@dataclass
class _Grid:
    mode: str
    cell_size: float
    rotation: float
    origin: tuple[float, float, float]
    chainage: dict[str, Any] | None = None
    detected: dict[str, Any] = field(default_factory=dict)

    def cells(self, lo: Sequence[float], hi: Sequence[float]) -> list[Cell]:
        if self.chainage:
            c = self.chainage
            return chainage_cells(lo, hi, self.origin, c["axis"], self.cell_size, c["lane_width_m"], c["lanes"])
        if self.rotation:
            cells = grid_detect.cells_for_bbox_rotated(lo, hi, self.origin, self.cell_size, self.rotation)
        else:
            cells = cells_for_bbox(lo, hi, self.origin, self.cell_size)
        return [c for c in cells if c[0] >= 0 and c[1] >= 0] or [(0, 0)]


def _choose_grid(cfg: ProjectConfig, recs: Sequence[_Rec], axes: Mapping[int, float | None],
                 default_cell: float | None) -> _Grid:
    g = cfg.grid
    lo_all = [min(r.lo[i] for r in recs) for i in range(3)]
    hi_all = [max(r.hi[i] for r in recs) for i in range(3)]
    extent = (lo_all[0], lo_all[1], hi_all[0], hi_all[1])
    if g.mode == "chainage":
        ch = g.chainage
        axis = ch.axis
        if axis == "auto":
            axis = "x" if (extent[2] - extent[0]) >= (extent[3] - extent[1]) else "y"
        origin = (extent[0], extent[1], 0.0) if g.origin is None else tuple(g.origin)       # type: ignore[assignment]
        info = {"axis": axis, "cell_length_m": ch.cell_length_m, "lanes": ch.lanes, "lane_width_m": ch.lane_width_m,
                "alignment_guid": ch.alignment_guid}
        return _Grid("chainage", ch.cell_length_m, 0.0, origin, info,
                     {"mode": "chainage", "axis": axis, "extent": [round(v, 2) for v in extent]})
    if g.mode == "auto":
        items = [grid_detect.GridItem(r.cls, tuple(r.lo), tuple(r.hi), axes.get(r.sid))           # type: ignore[arg-type]
                 for r in recs if r.cls in ("IfcColumn", "IfcWall", "IfcWallStandardCase", "IfcBeam")]
        det = grid_detect.detect_grid(items, snap_m=g.snap_m, min_cell=g.min_cell_m, max_cell=g.max_cell_m,
                                      extent=extent, rotation=g.rotation_deg)
        cell = g.cell_size_m or default_cell or det["cell_size_m"]
        rot = float(det["rotation_deg"])
        if rot and not g.origin:
            # the bbox of a rotated element inflates its extent when rotated back, so for a rotated grid the
            # origin comes from element centres, half a cell outside the first one (elements sit mid-cell)
            u = [grid_detect.rotate((r.lo[0] + r.hi[0]) / 2, (r.lo[1] + r.hi[1]) / 2, -rot) for r in recs]
            u0, v0 = min(a for a, _ in u) - cell / 2, min(b for _, b in u) - cell / 2
            ox, oy = grid_detect.rotate(u0, v0, rot)
            det = {**det, "origin": [round(ox, 3), round(oy, 3), 0.0], "origin_rotated": [round(u0, 3), round(v0, 3)],
                   "origin_from": "element centres"}
        origin = tuple(g.origin) if g.origin else tuple(det["origin"])
        det = {**det, "mode": "auto"}
        return _Grid("auto", float(cell), rot, origin, None, det)                              # type: ignore[arg-type]
    cell = g.cell_size_m or default_cell or grid_detect.DEFAULT_CELL
    origin = tuple(g.origin) if g.origin else (math.floor(lo_all[0] / cell) * cell, math.floor(lo_all[1] / cell) * cell, 0.0)
    return _Grid("fixed", float(cell), float(g.rotation_deg or 0.0), origin, None, {"mode": "fixed"})   # type: ignore[arg-type]


def _default_space_tags(cfg: ProjectConfig, sector: str) -> Path | None:
    if cfg.zones.space_tags:
        return cfg.resolve(cfg.zones.space_tags)
    p = Path(__file__).resolve().parents[2] / "data" / "zoning" / f"space_tags_{sector}.json"
    return p if p.exists() else None


def _is_excluded(el: Any, cfg: ProjectConfig) -> bool:
    cls = el.is_a()
    if cls in cfg.filters.exclude_ifc_classes:
        return True
    inc = cfg.filters.include_ifc_classes
    if inc is not None and not any(el.is_a(c) for c in inc):
        return True
    return any(el.is_a(c) for c in ("IfcOpeningElement", "IfcVirtualElement"))


def extract_to(ifc_path: str, out_path: str | Path | None = None, *, sector: str = "industrial",
               config: ProjectConfig | None = None, cell_size_m: float | None = None,
               threads: int | None = None, log=sys.stderr) -> ExtractResult:
    """Extract elements from an IFC file.

    With ``out_path`` the elements are written: one ``elements.json`` (``.gz`` when ``output.compress``) or,
    above ``output.elements_per_part`` elements, ``<stem>.part-N.json[.gz]`` files (each a complete elements
    document for its slice) plus ``<stem>.index.json`` listing them. Without ``out_path`` the elements are
    returned in memory. Raises ``RuntimeError`` if ifcopenshell is not installed.
    """
    import time
    if not HAVE_IFCOPENSHELL:
        raise RuntimeError("ifcopenshell is not installed")
    cfg = config or ProjectConfig()
    sector = cfg.sector or sector
    timings: dict[str, float] = {}
    t0 = time.perf_counter()

    def tick(label: str) -> None:
        nonlocal t0
        now = time.perf_counter()
        timings[label] = round(now - t0, 2)
        t0 = now

    model = ifcopenshell.open(str(ifc_path))
    try:
        unit_scale = float(_ifc_unit.calculate_unit_scale(model))
    except Exception:
        unit_scale = 1.0
    tick("open")

    # --- spatial structure
    storeys_e = list(model.by_type("IfcBuildingStorey"))
    if not storeys_e:
        raise RuntimeError("IFC model has no IfcBuildingStorey")
    parent = _parent_map(model)
    container = _container_map(model)
    buildings = {b.id(): b for b in model.by_type("IfcBuilding")}
    elev: dict[str, float] = {}
    by_key: dict[str, Any] = {}
    building_of_storey: dict[int, int | None] = {}
    for st in storeys_e:
        z = getattr(st, "Elevation", None)
        if z is None:
            p = _placement_point(st, unit_scale)
            z = (p[2] / unit_scale) if p else 0.0
        elev[st.GlobalId] = float(z) * unit_scale
        by_key[st.GlobalId] = st
        b = parent.get(st.id())
        building_of_storey[st.id()] = b if b in buildings else None
    if cfg.filters.include_storeys:
        keep = set(cfg.filters.include_storeys)
        for key in [k for k, st in by_key.items() if st.Name not in keep and st.GlobalId not in keep]:
            by_key.pop(key)
            elev.pop(key)
    if cfg.scope.buildings != ["*"]:
        wanted = set(cfg.scope.buildings)
        for key in [k for k, st in by_key.items()
                    if building_of_storey[st.id()] is None
                    or (buildings[building_of_storey[st.id()]].Name not in wanted
                        and buildings[building_of_storey[st.id()]].GlobalId not in wanted)]:
            by_key.pop(key)
            elev.pop(key)
    if not by_key:
        raise RuntimeError("no storeys left after filters")
    index_of = index_storeys(list(elev.items()))
    ordered = sorted(elev, key=lambda k: (elev[k], k))
    levels = sorted({round(elev[k], 2) for k in ordered})
    nxt = {z: (levels[i + 1] if i + 1 < len(levels) else math.inf) for i, z in enumerate(levels)}
    tops = {k: nxt[round(elev[k], 2)] for k in ordered}
    finite = [tops[k] - elev[k] for k in ordered if math.isfinite(tops[k]) and tops[k] - elev[k] > 0.1]
    storey_height = cfg.grid.storey_height_m or (round(statistics.median(finite), 3) if finite else 4.0)
    for k in ordered:
        if not math.isfinite(tops[k]):
            tops[k] = elev[k] + storey_height
    storey_id = {k: (f"L{index_of[k]:02d}" if index_of[k] >= 0 else f"B{-index_of[k]:02d}") for k in ordered}
    bands = [(k, elev[k], tops[k]) for k in ordered]
    storey_by_step = {st.id(): st.GlobalId for st in storeys_e}          # includes storeys removed by filters

    def storey_of(entity_id: int) -> str | None:
        cur, seen = entity_id, 0
        while cur is not None and seen < 8:
            if cur in storey_by_step:
                return storey_by_step[cur]
            cur = container.get(cur) if cur in container else parent.get(cur)
            seen += 1
        return None

    # --- candidate elements
    cands = list({e.id(): e for e in model.by_type("IfcElement") if not _is_excluded(e, cfg)}.values())
    tick("structure")
    boxes, fallback = _bboxes(model, cands, threads or min(8, os.cpu_count() or 2), unit_scale,
                              {"IfcWall", "IfcWallStandardCase", "IfcBeam", "IfcMember"})
    tick("geometry")
    recs: list[_Rec] = []
    axes: dict[int, float | None] = {}
    fb_box = cfg.filters.bbox
    dropped = 0
    for e in cands:
        box = boxes.get(e.id())
        if box is None:
            continue
        lo, hi, axis = box
        dims = hi - lo
        if hi is not lo and float(dims.max()) < cfg.filters.min_bbox_m and not (lo == hi).all():
            dropped += 1
            continue
        if fb_box and not all(hi[i] >= fb_box["min"][i] and lo[i] <= fb_box["max"][i] for i in range(3)):
            dropped += 1
            continue
        home = storey_of(e.id())
        if home is None:
            zmid = float(lo[2] + hi[2]) / 2
            home = min(ordered, key=lambda k: abs((elev[k] + tops[k]) / 2 - zmid))
        elif home not in by_key:
            continue
        if cfg.scope.buildings != ["*"] and building_of_storey.get(by_key[home].id()) is None:
            continue
        b = building_of_storey.get(by_key[home].id())
        recs.append(_Rec(e.id(), e.GlobalId, e.is_a(), tuple(float(v) for v in lo), tuple(float(v) for v in hi), home,
                         buildings[b].GlobalId if b else None, hi is lo or bool((lo == hi).all())))
        axes[e.id()] = axis
    if not recs:
        raise RuntimeError("no IfcElement with usable geometry or placement found")
    tick("filter")

    # --- grid
    grid = _choose_grid(cfg, recs, axes, cell_size_m)
    tick("grid")

    # --- relationships / properties for the kept elements
    want = {r.sid for r in recs}
    material, system_of, host_of, systems = _read_relation_tables(model, want)
    if cfg.scope.systems != ["*"]:
        keep_sys = set(cfg.scope.systems)
        recs = [r for r in recs if r.sid not in system_of or system_of[r.sid] in keep_sys]
        want = {r.sid for r in recs}
        systems = {k: v for k, v in systems.items() if k in keep_sys}
    props_of, qtos_of = _read_property_tables(model, want)
    tick("relations")

    # --- occupied cells, spaces and zones
    occupied: dict[str, set[Cell]] = defaultdict(set)
    anchor_cache: dict[int, list[Cell]] = {}
    for r in recs:
        cells = grid.cells(r.lo, r.hi)
        anchor_cache[r.sid] = cells[:1]
        occupied[storey_id[r.storey]].update(cells)
    spaces = _read_spaces(model, by_key, storey_of, storey_id, grid, parent, unit_scale)
    zone_source = cfg.zones.source
    if zone_source == "auto":
        zone_source = "ifc_space" if spaces else "auto"
    storey_order = list(dict.fromkeys(storey_id[k] for k in ordered))
    if zone_source == "file" and cfg.zones.file:
        zones = zoning.zones_from_csv(cfg.resolve(cfg.zones.file), storey_order, cfg.zones.max_crews_default)
        zone_of = {(z["storey_id"], tuple(c)): z["id"] for z in zones for c in z["cells"]}
        extra, extra_of = zoning.build_zones([], [], {sid: {c for c in cs if (sid, c) not in zone_of}
                                                       for sid, cs in occupied.items()}, storey_order=storey_order,
                                             auto_block=cfg.zones.auto_block, max_crews_default=cfg.zones.max_crews_default,
                                             auto_prefix="A")
        zones += extra
        zone_of.update(extra_of)
    else:
        rules_path = _default_space_tags(cfg, sector) if zone_source in ("ifc_space", "ifc_zone") else None
        rules = zoning.load_space_tag_rules(rules_path) if rules_path and rules_path.exists() else []
        zones, zone_of = zoning.build_zones(
            spaces if zone_source in ("ifc_space", "ifc_zone") else [], rules, occupied, storey_order=storey_order,
            source=zone_source, auto_block=cfg.zones.auto_block, max_crews_default=cfg.zones.max_crews_default,
            merge_below=cfg.zones.merge_small_spaces_below_cells,
            auto_prefix="Z" if zone_source == "auto" else "A")
    tick("zones")

    # --- project block
    project_entities = model.by_type("IfcProject")
    pname = cfg.name or (project_entities[0].Name if project_entities and project_entities[0].Name else "IFC project")
    width = max(c[0] for s in occupied.values() for c in s) + 1
    depth = max(c[1] for s in occupied.values() for c in s) + 1
    if grid.chainage:
        depth = max(depth, grid.chainage["lanes"])
    gdict: JSON = {"cell_size_m": grid.cell_size, "storey_height_m": storey_height,
                   "origin": [round(grid.origin[0], 3), round(grid.origin[1], 3), 0.0], "rotation_deg": grid.rotation,
                   "mode": grid.mode, "width_cells": width, "depth_cells": depth, "detected": grid.detected}
    project: JSON = {"name": pname, "sector": sector, "source": Path(str(ifc_path)).name, "grid": gdict}
    areas = _areas(model, recs, buildings, grid, storey_id, index_of, cfg)
    if areas:
        project["areas"] = areas
    storeys_out: list[JSON] = []
    for k in ordered:
        if storey_id[k] not in {s["id"] for s in storeys_out}:
            storeys_out.append({"id": storey_id[k], "name": getattr(by_key[k], "Name", None) or storey_id[k],
                                "index": index_of[k], "elevation_m": round(elev[k], 3)})
    head: JSON = {"schema_version": "1.0", "project": project, "storeys": storeys_out, "zones": zones,
                  "systems": sorted(systems.values(), key=lambda s: s["id"])}
    tick("project")

    # --- emit elements
    recs.sort(key=lambda r: (index_of[r.storey], r.guid))
    per_part = max(100, cfg.output.elements_per_part)
    files: list[Path] = []
    keep_in_memory = out_path is None
    mem: list[JSON] = []
    part: list[JSON] = []
    part_no = 0
    total = 0
    split = out_path is not None and len(recs) > per_part
    compress = cfg.output.compress
    out_p = Path(out_path) if out_path else None

    def flush() -> None:
        nonlocal part, part_no
        if not part or out_p is None:
            return
        name = out_p.with_name(f"{out_p.stem}.part-{part_no}.json")
        files.append(_dump({**head, "elements": part}, name, compress))
        part_no += 1
        part = []

    for r in recs:
        el = model.by_id(r.sid)
        props = props_of.get(r.sid, {})
        qty = map_quantity_sets(qtos_of.get(r.sid, {}), unit_scale)
        for k, v in quantities_from_bbox(r.cls, r.lo, r.hi).items() if not r.placement_only else (("count", 1.0),):
            qty.setdefault(k, v)
        predefined = _predefined(el)
        name = el.Name or f"{r.cls} {r.sid}"
        parts = [(r.storey, 1.0)]
        if r.cls in SPLIT_CLASSES and r.hi[2] - r.lo[2] > 0:
            cover = storeys_overlapping(r.lo[2], r.hi[2], [b for b in bands])
            if len(cover) >= VERTICAL_SPLIT_MIN_STOREYS:
                parts = [(k, f) for k, f in cover if k in by_key]
        cells = grid.cells(r.lo, r.hi)
        for n, (key, frac) in enumerate(parts):
            sid = storey_id[key]
            pr = dict(props)
            if n > 0:
                pr["ParentGuid"] = r.guid
            zone = zone_of.get((sid, cells[0])) or zone_of.get((sid, next((c for c in cells if (sid, c) in zone_of), cells[0])))
            if zone is None:
                continue
            rec = {
                "guid": r.guid if n == 0 else f"{r.guid}@{sid}", "ifc_class": r.cls, "predefined_type": predefined,
                "name": name, "storey_id": sid, "zone_id": zone, "system_id": system_of.get(r.sid),
                "host_guid": host_of.get(r.sid), "cells": [[c[0], c[1]] for c in cells],
                "quantities": {k: (round(v * frac, 4) if k != "count" else 1.0) for k, v in qty.items()},
                "material": material.get(r.sid), "properties": pr,
                "bbox": {"min": [round(v, 3) for v in r.lo], "max": [round(v, 3) for v in r.hi]},
                "visual": visual_for(r.cls, pr, predefined, name),
            }
            total += 1
            if split:
                part.append(rec)
                if len(part) >= per_part:
                    flush()
            else:
                mem.append(rec)
    if split:
        flush()
        index = out_p.with_name(f"{out_p.stem}.index.json")                                    # type: ignore[union-attr]
        files.append(_dump({"schema_version": "1.0", "element_count": total, "parts": [f.name for f in files]},
                           index, False))
    elif out_p is not None:
        files.append(_dump({**head, "elements": mem}, out_p, compress))
    tick("emit")
    if fallback:
        print(f"note: {fallback} elements used placement instead of geometry", file=log)
    if dropped:
        print(f"note: {dropped} elements dropped by filters", file=log)
    return ExtractResult(head, total, files, mem if keep_in_memory else None, timings)


def extract(ifc_path: str, *, sector: str = "industrial", cell_size_m: float | None = None,
            config: ProjectConfig | None = None, log=sys.stderr, **_: Any) -> JSON:
    """Convenience wrapper: the whole elements document in memory."""
    return extract_to(ifc_path, None, sector=sector, config=config, cell_size_m=cell_size_m, log=log).document()


def _predefined(el: Any) -> str | None:
    try:
        value = _ifc_element.get_predefined_type(el)
    except Exception:
        value = getattr(el, "PredefinedType", None)
    if value is None or str(value).upper() == "NOTDEFINED":
        return None
    return str(value).upper()


def _read_spaces(model: Any, by_key: Mapping[str, Any], storey_of, storey_id: Mapping[str, str], grid: _Grid,
                 parent: Mapping[int, int], unit_scale: float) -> list[zoning.SpaceRec]:
    """IfcSpace records with grid cells, storey, names and (if any) IfcZone membership."""
    spaces = list(model.by_type("IfcSpace"))
    if not spaces:
        return []
    boxes, _ = _bboxes(model, spaces, min(8, os.cpu_count() or 2), unit_scale, set())
    zone_name: dict[int, str] = {}
    for z in model.by_type("IfcZone"):
        for rel in getattr(z, "IsGroupedBy", None) or ():
            for o in rel.RelatedObjects:
                zone_name.setdefault(o.id(), z.Name or z.GlobalId)
    psets, _q = _read_property_tables(model, {s.id() for s in spaces})
    out: list[zoning.SpaceRec] = []
    for sp in spaces:
        box = boxes.get(sp.id())
        key = storey_of(sp.id())
        if box is None or key is None or key not in by_key:
            continue
        lo, hi, _axis = box
        cells = grid.cells(lo, hi)
        out.append(zoning.SpaceRec(sp.GlobalId, sp.Name or sp.GlobalId, storey_id[key], cells, sp.LongName or "",
                                   sp.ObjectType or "", psets.get(sp.id(), {}), zone_name.get(sp.id())))
    return out


def _areas(model: Any, recs: Sequence[_Rec], buildings: Mapping[int, Any], grid: _Grid, storey_id: Mapping[str, str],
           index_of: Mapping[str, int], cfg: ProjectConfig) -> list[JSON]:
    """project.areas: one entry per IfcBuilding (when there are several, or when the config lists areas)."""
    by_b: dict[str, dict[str, Any]] = {}
    for r in recs:
        if r.building is None:
            continue
        a = by_b.setdefault(r.building, {"lo": list(r.lo), "hi": list(r.hi), "storeys": set()})
        a["lo"] = [min(a["lo"][i], r.lo[i]) for i in range(3)]
        a["hi"] = [max(a["hi"][i], r.hi[i]) for i in range(3)]
        a["storeys"].add(storey_id[r.storey])
    names = {b.GlobalId: b.Name or b.GlobalId for b in buildings.values()}
    cfg_by_guid = {a.get("building_guid"): a for a in cfg.areas if a.get("building_guid")}
    if len(by_b) < 2 and not cfg.areas:
        return []
    out: list[JSON] = []
    for n, (guid, a) in enumerate(sorted(by_b.items(), key=lambda kv: kv[1]["lo"][:2]), start=1):
        cells = sorted(set(grid.cells(a["lo"], a["hi"])))
        sample = cells if len(cells) <= 64 else [cells[round(i * (len(cells) - 1) / 63)] for i in range(64)]
        cx = (min(c[0] for c in cells) + max(c[0] for c in cells)) // 2
        cz = (min(c[1] for c in cells) + max(c[1] for c in cells)) // 2
        area: JSON = {"id": cfg_by_guid.get(guid, {}).get("id", f"area{n}"),
                      "name": cfg_by_guid.get(guid, {}).get("name", names.get(guid, f"Area {n}")),
                      "cells": [[c[0], c[1]] for c in sample], "storey_ids": sorted(a["storeys"]),
                      "camera_bookmark": {"cells": [[cx, cz]], "storey_index": 0}}
        out.append(area)
    known = {a["id"] for a in out}
    for a in cfg.areas:
        if a["id"] not in known and not a.get("building_guid"):
            out.append({"id": a["id"], "name": a["name"], "cells": a.get("camera_bookmark", {}).get("cells", [[0, 0]]),
                        "storey_ids": [], "camera_bookmark": a.get("camera_bookmark", {})})
    return out
