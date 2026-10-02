"""IFC -> elements.json using ifcopenshell (optional dependency).

Pure helpers (storey indexing, cell computation, zone tiling, quantity mapping) work without
ifcopenshell and are unit tested; only :func:`extract` needs it.  Import is guarded: when
ifcopenshell is missing :func:`available` is False and the CLI exits with code 3.
"""
from __future__ import annotations

import math
import statistics
import sys
from collections import defaultdict
from typing import Any, Sequence

from . import grid_detect
from .visuals import visual_for

try:  # pragma: no cover - exercised only where ifcopenshell is installed
    import ifcopenshell
    import ifcopenshell.util.element as _ifc_element
    import ifcopenshell.util.placement as _ifc_placement
    import ifcopenshell.util.unit as _ifc_unit
    try:
        import ifcopenshell.geom as _ifc_geom
    except ImportError:
        _ifc_geom = None
    try:
        import ifcopenshell.util.shape as _ifc_shape
    except ImportError:
        _ifc_shape = None
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

    ``elevations`` is ``[(storey_key, elevation_m)]``. Storeys below ground get negative
    indices; if every storey is below ground the highest one is index 0.
    """
    ordered = sorted(elevations, key=lambda kv: (kv[1], kv[0]))
    if not ordered:
        return {}
    ground = next((i for i, (_, z) in enumerate(ordered) if z >= -0.5), len(ordered) - 1)
    return {key: i - ground for i, (key, _) in enumerate(ordered)}


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


# ---------------------------------------------------------------------------- ifcopenshell access
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


def _bbox_from_geometry(el: Any, settings: Any) -> tuple[Vec, Vec, Any, float | None] | None:
    """World-space bbox (metres), geometry and plan long-axis angle from tessellated geometry, or None."""
    if _ifc_geom is None or getattr(el, "Representation", None) is None:
        return None
    try:
        shape = _ifc_geom.create_shape(settings, el)
        verts = shape.geometry.verts
    except Exception:
        return None
    if not verts:
        return None
    xs, ys, zs = verts[0::3], verts[1::3], verts[2::3]
    axis = plan_axis_deg(xs, ys) if el.is_a() in ("IfcWall", "IfcWallStandardCase", "IfcBeam", "IfcMember") else None
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)), shape.geometry, axis


def _bbox_from_placement(el: Any, unit_scale: float) -> tuple[Vec, Vec, None, None] | None:
    """Degenerate bbox at the object placement origin (metres), or None."""
    placement = getattr(el, "ObjectPlacement", None)
    if placement is None:
        return None
    try:
        m = _ifc_placement.get_local_placement(placement)
        p = (float(m[0][3]) * unit_scale, float(m[1][3]) * unit_scale, float(m[2][3]) * unit_scale)
    except Exception:
        return None
    return p, p, None, None


def _geom_settings() -> Any:
    if _ifc_geom is None:
        return None
    s = _ifc_geom.settings()
    for key in ("USE_WORLD_COORDS", "use-world-coords"):
        try:
            s.set(s.USE_WORLD_COORDS if key == "USE_WORLD_COORDS" else key, True)
            break
        except Exception:
            continue
    return s


def _storey_of(el: Any) -> Any | None:
    """The IfcBuildingStorey containing ``el`` (walking up aggregates), or None."""
    cur = el
    for _ in range(8):
        try:
            container = _ifc_element.get_container(cur)
        except Exception:
            container = None
        if container is not None:
            if container.is_a("IfcBuildingStorey"):
                return container
            cur = container
            continue
        try:
            parent = _ifc_element.get_aggregate(cur)
        except Exception:
            parent = None
        if parent is None:
            return None
        if parent.is_a("IfcBuildingStorey"):
            return parent
        cur = parent
    return None


def _material_name(el: Any) -> str | None:
    """Material name via IfcRelAssociatesMaterial (layer sets and constituents joined with '/')."""
    try:
        mats = _ifc_element.get_materials(el)
    except Exception:
        mats = []
    names = [m.Name for m in mats if getattr(m, "Name", None)]
    if names:
        return " / ".join(dict.fromkeys(names))
    try:
        mat = _ifc_element.get_material(el)
    except Exception:
        return None
    return getattr(mat, "Name", None) if mat is not None else None


def _system_of(el: Any) -> tuple[str, str, str] | None:
    """(id, name, discipline) of the IfcSystem this element is assigned to, if any."""
    for rel in getattr(el, "HasAssignments", None) or []:
        if rel.is_a("IfcRelAssignsToGroup") and rel.RelatingGroup is not None and rel.RelatingGroup.is_a("IfcSystem"):
            grp = rel.RelatingGroup
            name = grp.Name or grp.GlobalId
            return name, name, guess_system_discipline(name, getattr(grp, "PredefinedType", None))
    return None


def _host_guid(el: Any) -> str | None:
    """Host wall GUID for doors/windows via IfcRelFillsElement -> opening -> IfcRelVoidsElement."""
    for fill in getattr(el, "FillsVoids", None) or []:
        opening = fill.RelatingOpeningElement
        for void in getattr(opening, "VoidsElements", None) or []:
            host = void.RelatingBuildingElement
            if host is not None:
                return host.GlobalId
    return None


def _predefined(el: Any) -> str | None:
    try:
        value = _ifc_element.get_predefined_type(el)
    except Exception:
        value = getattr(el, "PredefinedType", None)
    if value is None or str(value).upper() == "NOTDEFINED":
        return None
    return str(value).upper()


def _properties(el: Any) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """(flat scalar properties, quantity sets) of an element."""
    try:
        psets = _ifc_element.get_psets(el)
    except Exception:
        return {}, {}
    props: dict[str, Any] = {}
    qtos: dict[str, dict[str, Any]] = {}
    for pname, values in psets.items():
        if pname.startswith("Qto_") or pname.startswith("BaseQuantities") or "Quantit" in pname:
            qtos[pname] = values
            continue
        for k, v in values.items():
            if k != "id" and isinstance(v, (str, int, float, bool)) and len(props) < MAX_PROPERTIES:
                props[k] = v
    return props, qtos


def load_project_config(path: str | None) -> dict[str, Any]:
    """Optional project_config.json (``grid`` block only is used here); {} when absent."""
    if not path:
        return {}
    import json
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def extract(ifc_path: str, *, sector: str = "industrial", cell_size_m: float | None = None,
            zone_block: Sequence[int] = (3, 3), max_crews: int = 2,
            project_name: str | None = None, log=sys.stderr,
            grid_mode: str = "auto", project_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convert an IFC file into an ``elements.json`` document.

    Grid: ``auto`` (default, also when no project config is given) detects cell size, rotation and
    origin with :mod:`grid_detect`; ``fixed`` uses ``cell_size_m`` (default 6), no rotation and the
    model's bbox minimum. ``project_config["grid"]`` (mode, cell_size_m, rotation_deg, origin) wins
    over arguments. Raises ``RuntimeError`` if ifcopenshell is not installed.
    """
    if not HAVE_IFCOPENSHELL:
        raise RuntimeError("ifcopenshell is not installed")
    gcfg = (project_config or {}).get("grid", {})
    grid_mode = gcfg.get("mode", grid_mode)
    cell_size_m = gcfg.get("cell_size_m") or cell_size_m
    model = ifcopenshell.open(ifc_path)
    try:
        unit_scale = float(_ifc_unit.calculate_unit_scale(model))
    except Exception:
        unit_scale = 1.0
    settings = _geom_settings()

    # --- storeys
    storey_entities = list(model.by_type("IfcBuildingStorey"))
    if not storey_entities:
        raise RuntimeError("IFC model has no IfcBuildingStorey")
    elev: dict[str, float] = {}
    by_key: dict[str, Any] = {}
    for st in storey_entities:
        key = st.GlobalId
        z = getattr(st, "Elevation", None)
        if z is None:
            pl = _bbox_from_placement(st, unit_scale)
            z = pl[0][2] / unit_scale if pl else 0.0
        elev[key] = float(z) * unit_scale
        by_key[key] = st
    index_of = index_storeys(list(elev.items()))
    ordered = sorted(elev, key=lambda k: (elev[k], k))
    tops: dict[str, float] = {}
    for i, key in enumerate(ordered):
        tops[key] = elev[ordered[i + 1]] if i + 1 < len(ordered) else math.inf
    finite = [tops[k] - elev[k] for k in ordered if math.isfinite(tops[k])]
    storey_height = round(statistics.median(finite), 3) if finite else 4.0
    for k in ordered:
        if not math.isfinite(tops[k]):
            tops[k] = elev[k] + storey_height
    storey_id = {k: f"L{index_of[k]:02d}" if index_of[k] >= 0 else f"B{-index_of[k]:02d}" for k in ordered}
    bands = [(k, elev[k], tops[k]) for k in ordered]

    # --- products
    raw: list[dict[str, Any]] = []
    skipped = 0
    for el in model.by_type("IfcElement"):
        if el.is_a() in SKIP_CLASSES or any(el.is_a(c) for c in SKIP_CLASSES):
            continue
        box = _bbox_from_geometry(el, settings) if settings is not None else None
        if box is None:
            box = _bbox_from_placement(el, unit_scale)
        if box is None:
            skipped += 1
            continue
        lo, hi, geometry, axis = box
        props, qtos = _properties(el)
        quantities = map_quantity_sets(qtos, unit_scale)
        fallback = quantities_from_bbox(el.is_a(), lo, hi) if hi != lo else {"count": 1.0}
        for k, v in fallback.items():
            quantities.setdefault(k, v)
        if "volume_m3" not in quantities and geometry is not None and _ifc_shape is not None:
            try:
                quantities["volume_m3"] = round(float(_ifc_shape.get_volume(geometry)), 4)
            except Exception:
                pass
        home = _storey_of(el)
        if home is not None and home.GlobalId in elev:
            home_key = home.GlobalId
        else:
            zmid = (lo[2] + hi[2]) / 2
            home_key = min(ordered, key=lambda k: abs((elev[k] + tops[k]) / 2 - zmid))
        parts = [(home_key, 1.0)]
        if el.is_a() in SPLIT_CLASSES and hi[2] - lo[2] > 0:
            cover = storeys_overlapping(lo[2], hi[2], bands)
            if len(cover) >= VERTICAL_SPLIT_MIN_STOREYS:
                parts = cover
        raw.append({"el": el, "lo": lo, "hi": hi, "props": props, "qty": quantities, "parts": parts,
                    "axis": axis})
    if not raw:
        raise RuntimeError("no IfcElement with usable geometry or placement found")

    rotation = 0.0
    if grid_mode == "auto":
        det = grid_detect.detect_grid([grid_detect.GridItem(r["el"].is_a(), r["lo"], r["hi"], r["axis"])
                                       for r in raw])
        cell_size_m = cell_size_m or det["cell_size_m"]
        rotation = float(gcfg.get("rotation_deg") if gcfg.get("rotation_deg") is not None else det["rotation_deg"])
        origin = tuple(gcfg["origin"]) if gcfg.get("origin") else tuple(det["origin"])
    else:
        cell_size_m = cell_size_m or grid_detect.DEFAULT_CELL
        rotation = float(gcfg.get("rotation_deg") or 0.0)
        origin = tuple(gcfg["origin"]) if gcfg.get("origin") else (
            math.floor(min(r["lo"][0] for r in raw) / cell_size_m) * cell_size_m,
            math.floor(min(r["lo"][1] for r in raw) / cell_size_m) * cell_size_m, 0.0)

    # --- cells per element part
    parts_out: list[dict[str, Any]] = []
    cells_by_storey: dict[str, set[Cell]] = defaultdict(set)
    systems: dict[str, dict[str, str]] = {}
    for r in raw:
        el = r["el"]
        if rotation:
            cells = grid_detect.cells_for_bbox_rotated(r["lo"], r["hi"], origin, cell_size_m, rotation)
        else:
            cells = cells_for_bbox(r["lo"], r["hi"], origin, cell_size_m)
        cells = [c for c in cells if c[0] >= 0 and c[1] >= 0] or [(0, 0)]
        sysinfo = _system_of(el)
        if sysinfo:
            systems.setdefault(sysinfo[0], {"id": sysinfo[0], "name": sysinfo[1], "discipline": sysinfo[2]})
        material = _material_name(el)
        host = _host_guid(el)
        predefined = _predefined(el)
        name = el.Name or f"{el.is_a()} {el.id()}"
        for n, (key, frac) in enumerate(r["parts"]):
            sid = storey_id[key]
            guid = el.GlobalId if n == 0 else f"{el.GlobalId}@{sid}"
            props = dict(r["props"])
            if n > 0:
                props["ParentGuid"] = el.GlobalId
            qty = {k: round(v * frac, 4) if k != "count" else 1.0 for k, v in r["qty"].items()}
            parts_out.append({
                "guid": guid, "ifc_class": el.is_a(), "predefined_type": predefined, "name": name,
                "storey_id": sid, "system_id": sysinfo[0] if sysinfo else None, "host_guid": host,
                "cells": [[c[0], c[1]] for c in cells], "quantities": qty, "material": material,
                "properties": props,
                "bbox": {"min": [round(v, 3) for v in r["lo"]], "max": [round(v, 3) for v in r["hi"]]},
                "visual": visual_for(el.is_a(), props, predefined, name),
                "_cellset": cells,
            })
            cells_by_storey[sid].update(cells)

    zones, zone_of = tile_zones(cells_by_storey, (zone_block[0], zone_block[1]), max_crews)
    elements = []
    for p in parts_out:
        cells = p.pop("_cellset")
        p["zone_id"] = zone_of[(p["storey_id"], cells[0])]
        elements.append(p)
    elements.sort(key=lambda e: (e["storey_id"], e["zone_id"], e["guid"]))

    width = max(c[0] for s in cells_by_storey.values() for c in s) + 1
    depth = max(c[1] for s in cells_by_storey.values() for c in s) + 1
    project_entities = model.by_type("IfcProject")
    pname = project_name or (project_entities[0].Name if project_entities and project_entities[0].Name else "IFC project")
    if skipped:
        print(f"note: {skipped} elements without geometry or placement were skipped", file=log)
    return {
        "schema_version": "1.0",
        "project": {"name": pname, "sector": sector, "source": str(ifc_path).replace("\\", "/").split("/")[-1],
                    "grid": {"cell_size_m": cell_size_m, "storey_height_m": storey_height,
                             "origin": [round(origin[0], 3), round(origin[1], 3), 0.0],
                             "rotation_deg": rotation, "mode": grid_mode,
                             "width_cells": width, "depth_cells": depth}},
        "storeys": [{"id": storey_id[k], "name": getattr(by_key[k], "Name", None) or storey_id[k],
                     "index": index_of[k], "elevation_m": round(elev[k], 3)} for k in ordered],
        "zones": zones, "systems": list(systems.values()), "elements": elements,
    }
