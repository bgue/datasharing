"""Synthetic IFC generators: write a real IFC file (ifcopenshell) for a sector project, optionally at scale.

The geometry source is the seeded elements generators in :mod:`bimseq.synth` (so classes, names, properties,
systems and hosts match what the sector mapping rules expect); this module turns an elements document
into IFC: project/site/building/storeys, one extruded box per element, property sets (shared per identical
property set), materials, ``IfcSystem`` groups, openings with ``IfcRelFillsElement`` for hosted doors and
windows, element quantities, and ``IfcSpace`` rooms named after the zoning vocabulary
(``data/zoning/README.md``: "Operating Theatre 1", "MRI 1", "Plant Room", "Ward A", "Corridor", ...).
"""
from __future__ import annotations

import math
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .model import JSON
from .synth import GENERATORS, stress

try:
    import ifcopenshell
    import ifcopenshell.api as _api
    import ifcopenshell.guid as _guid
except ImportError:                                       # pragma: no cover
    ifcopenshell = None                                   # type: ignore[assignment]

WINGS_FOR_SCALE = {1: (1, 1)}
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
SPACE_HEIGHT = 3.0


def wings_for_scale(scale: int) -> tuple[int, int]:
    """Wings (x, z) of the healthcare stress campus for ``--scale N`` (about 834 elements per wing)."""
    n = max(1, scale)
    wx = max(1, round(math.sqrt(n)))
    return wx, max(1, math.ceil(n / wx))


# ---------------------------------------------------------------------------- space plans
def space_plan(sector: str, doc: Mapping[str, Any]) -> list[tuple[str, str, JSON]]:
    """(name, long name, zone) per room, named after the zoning vocabulary; one room per zone of the document."""
    out: list[tuple[str, str, JSON]] = []
    counters: dict[str, int] = defaultdict(int)

    def nxt(key: str) -> int:
        counters[key] += 1
        return counters[key]

    def numbered(base: str) -> str:
        n = nxt(base)
        return base if n == 1 else f"{base} {n}"
    for z in doc["zones"]:
        tags = set(z.get("tags", []))
        cells = z["cells"]
        z0 = min(c[1] for c in cells)
        x0 = min(c[0] for c in cells)
        live = " Live" if "occupied_adjacent" in tags and sector == "healthcare" else ""
        if sector == "healthcare":
            if "or_room" in tags:
                name = f"Operating Theatre {nxt('or')}"
            elif "imaging" in tags:
                name = f"MRI {nxt('mri')}"
            elif "plant_room" in tags:
                name = numbered("Plant Room")
            elif z0 % 6 in (2, 3):
                name = numbered("Corridor")
            else:
                name = f"Ward {LETTERS[(nxt('ward') - 1) % 26]}"
            out.append((name, ("Live ward wing" if live else ""), z))
        elif sector == "industrial":
            if "process_unit" in tags:
                name = f"Process Unit {nxt('pu')}"
            elif "pipe_rack" in tags or "equipment_yard" in tags:
                name = f"Pipe Rack {nxt('rack')}"
            else:
                name = ["Control Room", "Switchroom", "Pump House", "Warehouse"][(nxt("bay") - 1) % 4]
            out.append((name, "", z))
        else:
            if z["storey_id"] != "L00":
                name = f"Utility Trench {nxt('ut')}"
            elif "live_traffic" in tags:
                name = f"Live Lane {nxt('live')}"
            elif "bridge" in tags:
                name = f"Bridge Span {nxt('span')}"
            elif "culvert" in tags:
                name = f"Culvert {nxt('culv')}"
            else:
                name = f"Road Segment {nxt('seg')}"
            out.append((name, "", z))
    return out


# ---------------------------------------------------------------------------- IFC writing
class _Writer:
    def __init__(self, schema: str, name: str) -> None:
        self.f = _api.run("project.create_file", version=schema)
        f = self.f
        self.project = _api.run("root.create_entity", f, ifc_class="IfcProject", name=name)
        _api.run("unit.assign_unit", f, length={"is_metric": True, "raw": "METERS"})
        ctx = _api.run("context.add_context", f, context_type="Model")
        self.body = _api.run("context.add_context", f, context_type="Model", context_identifier="Body",
                             target_view="MODEL_VIEW", parent=ctx)
        self.site = _api.run("root.create_entity", f, ifc_class="IfcSite", name="Site")
        _api.run("aggregate.assign_object", f, products=[self.site], relating_object=self.project)
        self.owner = None
        self._dir = f.createIfcDirection((0.0, 0.0, 1.0))
        self._origin = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0.0, 0.0, 0.0)))
        self._materials: dict[str, Any] = {}

    def geometry(self, lo: Sequence[float], hi: Sequence[float]) -> tuple[Any, Any]:
        f = self.f
        dx, dy, dz = (max(0.01, float(hi[i]) - float(lo[i])) for i in range(3))
        placement = f.createIfcLocalPlacement(None, f.createIfcAxis2Placement3D(
            f.createIfcCartesianPoint((float(lo[0]), float(lo[1]), float(lo[2])))))
        prof = f.createIfcRectangleProfileDef("AREA", None, f.createIfcAxis2Placement2D(
            f.createIfcCartesianPoint((dx / 2, dy / 2))), dx, dy)
        solid = f.createIfcExtrudedAreaSolid(prof, self._origin, self._dir, dz)
        rep = f.createIfcShapeRepresentation(self.body, "Body", "SweptSolid", [solid])
        return placement, f.createIfcProductDefinitionShape(None, None, [rep])

    def entity(self, cls: str, name: str, guid: str, placement: Any, shape: Any, predefined: str | None) -> Any:
        """Create the product; PredefinedType is set afterwards and dropped if the schema rejects the value."""
        f = self.f
        kw = dict(GlobalId=guid, Name=name, ObjectPlacement=placement, Representation=shape)
        try:
            ent = f.create_entity(cls, **kw)
        except Exception:
            ent = f.create_entity("IfcBuildingElementProxy", **kw)
        if predefined:
            try:
                ent.PredefinedType = predefined
            except Exception:
                pass
        return ent

    def material(self, name: str) -> Any:
        if name not in self._materials:
            self._materials[name] = self.f.createIfcMaterial(name)
        return self._materials[name]

    @staticmethod
    def _value(f: Any, v: Any) -> Any:
        if isinstance(v, bool):
            return f.createIfcBoolean(v)
        if isinstance(v, int):
            return f.createIfcInteger(v)
        if isinstance(v, float):
            return f.createIfcReal(v)
        return f.createIfcLabel(str(v))

    def pset(self, props: Mapping[str, Any]) -> Any:
        f = self.f
        singles = [f.createIfcPropertySingleValue(k, None, self._value(f, v), None) for k, v in sorted(props.items())
                   if v is not None]
        return f.createIfcPropertySet(_guid.new(), None, "Pset_SiteBuilder", None, singles)

    def qto(self, q: Mapping[str, float]) -> Any:
        f = self.f
        items = []
        for key, make in (("length_m", lambda v: f.createIfcQuantityLength("Length", None, None, v)),
                          ("area_m2", lambda v: f.createIfcQuantityArea("NetArea", None, None, v)),
                          ("volume_m3", lambda v: f.createIfcQuantityVolume("NetVolume", None, None, v)),
                          ("weight_t", lambda v: f.createIfcQuantityWeight("NetWeight", None, None, v * 1000.0)),
                          ("count", lambda v: f.createIfcQuantityCount("Count", None, None, int(round(v))))):
            if q.get(key):
                items.append(make(float(q[key])))
        return f.createIfcElementQuantity(_guid.new(), None, "BaseQuantities", None, None, items)


def doc_to_ifc(doc: Mapping[str, Any], out_path: str | Path, sector: str, *, schema: str | None = None,
               with_spaces: bool = True, buildings: Sequence[tuple[str, Sequence[str]]] | None = None,
               quantities: str = "weight") -> dict[str, int]:
    """Write ``doc`` (an elements document) as IFC and return entity counts.

    ``buildings``: optional ``[(building name, zone id prefixes)]`` to split elements into several
    IfcBuilding instances (wings) by zone-id prefix; default is one building.
    ``quantities``: ``all`` writes an IfcElementQuantity per element, ``weight`` only for elements with a
    weight (equipment, steel), ``none`` never (bbox fallback in the extractor).
    """
    if ifcopenshell is None:
        raise RuntimeError("ifcopenshell is not installed")
    schema = schema or ("IFC4X3" if sector == "civil" else "IFC4")
    w = _Writer(schema, doc["project"]["name"])
    f = w.f
    storeys: dict[str, Any] = {}
    bld = _api.run("root.create_entity", f, ifc_class="IfcBuilding", name=doc["project"]["name"])
    _api.run("aggregate.assign_object", f, products=[bld], relating_object=w.site)
    for s in doc["storeys"]:
        st = _api.run("root.create_entity", f, ifc_class="IfcBuildingStorey", name=s["name"])
        st.Elevation = float(s.get("elevation_m", 0.0))
        _api.run("aggregate.assign_object", f, products=[st], relating_object=bld)
        storeys[s["id"]] = st
    sys_entities: dict[str, Any] = {}
    sys_members: dict[str, list[Any]] = defaultdict(list)
    by_storey: dict[str, list[Any]] = defaultdict(list)
    mat_members: dict[str, list[Any]] = defaultdict(list)
    prop_groups: dict[tuple, list[Any]] = defaultdict(list)
    by_guid: dict[str, Any] = {}
    hosted: list[tuple[Any, Any, Mapping[str, Any]]] = []
    count = 0
    for e in doc["elements"]:
        placement, shape = w.geometry(e["bbox"]["min"], e["bbox"]["max"])
        ent = w.entity(e["ifc_class"], e["name"], _ifc_guid(e["guid"]), placement, shape, e.get("predefined_type"))
        by_guid[e["guid"]] = ent
        by_storey[e["storey_id"]].append(ent)
        count += 1
        if e.get("material"):
            mat_members[e["material"]].append(ent)
        if e.get("system_id"):
            sys_members[e["system_id"]].append(ent)
        props = {**(e.get("properties") or {})}
        if props:
            prop_groups[tuple(sorted(props.items(), key=lambda kv: kv[0]))].append(ent)
        if quantities == "all" or (quantities == "weight" and e["quantities"].get("weight_t")):
            q = w.qto(e["quantities"])
            f.createIfcRelDefinesByProperties(_guid.new(), None, None, None, [ent], q)
        if e.get("host_guid"):
            hosted.append((ent, e["host_guid"], e))
    for sid, ents in by_storey.items():
        f.createIfcRelContainedInSpatialStructure(_guid.new(), None, None, None, ents, storeys[sid])
    for name, ents in mat_members.items():
        f.createIfcRelAssociatesMaterial(_guid.new(), None, None, None, ents, w.material(name))
    for key, ents in prop_groups.items():
        f.createIfcRelDefinesByProperties(_guid.new(), None, None, None, ents, w.pset(dict(key)))
    systems_meta = {s["id"]: s for s in doc.get("systems", [])}
    for sid, ents in sys_members.items():
        meta = systems_meta.get(sid, {"name": sid})
        grp = f.create_entity("IfcSystem", GlobalId=_guid.new(), Name=sid, Description=meta.get("name"))
        f.createIfcRelAssignsToGroup(_guid.new(), None, None, None, ents, None, grp)
    # openings and fills for hosted doors/windows
    for ent, host_guid, e in hosted:
        host = by_guid.get(host_guid)
        if host is None:
            continue
        opening = f.create_entity("IfcOpeningElement", GlobalId=_guid.new(), Name=f"Opening for {e['name']}",
                                  ObjectPlacement=ent.ObjectPlacement)
        f.createIfcRelVoidsElement(_guid.new(), None, None, None, host, opening)
        f.createIfcRelFillsElement(_guid.new(), None, None, None, opening, ent)
    spaces = 0
    if with_spaces:
        room_by_storey: dict[str, list[Any]] = defaultdict(list)
        elev = {s["id"]: float(s.get("elevation_m", 0.0)) for s in doc["storeys"]}
        cs = float(doc["project"]["grid"]["cell_size_m"])
        for name, long_name, z in space_plan(sector, doc):
            xs = [c[0] for c in z["cells"]]
            ys = [c[1] for c in z["cells"]]
            lo = (min(xs) * cs + 0.1, min(ys) * cs + 0.1, elev[z["storey_id"]])
            hi = ((max(xs) + 1) * cs - 0.1, (max(ys) + 1) * cs - 0.1, elev[z["storey_id"]] + SPACE_HEIGHT)
            placement, shape = w.geometry(lo, hi)
            sp = f.create_entity("IfcSpace", GlobalId=_guid.new(), Name=name, LongName=long_name or None,
                                 ObjectPlacement=placement, Representation=shape)
            room_by_storey[z["storey_id"]].append(sp)
            spaces += 1
        for sid, sps in room_by_storey.items():
            f.createIfcRelAggregates(_guid.new(), None, None, None, storeys[sid], sps)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    f.write(str(out_path))
    return {"elements": count, "spaces": spaces, "storeys": len(storeys), "systems": len(sys_members),
            "hosted": len(hosted), "property_sets": len(prop_groups)}


def _ifc_guid(guid: str) -> str:
    """A valid 22 character IFC GlobalId derived from any string (kept as is when already valid)."""
    import hashlib
    import re
    if re.fullmatch(r"[0-9A-Za-z_$]{22}", guid):
        return guid
    n = int(hashlib.sha1(guid.encode()).hexdigest(), 16) & ((1 << 128) - 1)
    return _guid.compress(f"{n:032x}")


def generate_doc(sector: str, scale: int = 1, seed: int = 42) -> JSON:
    """The elements document that will be written: the sector generator, or the stress campus for ``scale > 1``."""
    if scale <= 1:
        return GENERATORS[sector](seed)
    if sector != "healthcare":
        raise ValueError("--scale is only available for the healthcare campus")
    wx, wz = wings_for_scale(scale)
    return stress.generate(wx, wz, seed)


def synth_ifc(sector: str, out_path: str | Path, scale: int = 1, seed: int = 42, **kw: Any) -> dict[str, int]:
    """Generate ``sector`` (optionally a ``scale``-wing campus) and write it as IFC; returns entity counts."""
    return doc_to_ifc(generate_doc(sector, scale, seed), out_path, sector, **kw)
