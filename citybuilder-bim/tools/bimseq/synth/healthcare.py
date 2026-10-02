"""Synthetic healthcare project: three-storey hospital wing (8x6 bays) with an imaging suite."""
from __future__ import annotations

from typing import Any

from .base import Cell, SynthBuilder, q

NX, NZ = 8, 6
STOREYS = [("L00", "Ground", 0), ("L01", "Level 1", 1), ("L02", "Level 2", 2)]
H = 3.8                                  # floor-to-ceiling structural height
CORRIDOR_Z = 2                           # corridor runs along x in row z = 2
IMAGING = [(x, z) for x in range(0, 3) for z in range(0, 2)]       # L00 imaging suite cells
THEATRES = {"L01": [(0, 0), (1, 0), (0, 1), (1, 1)]}               # pressure-controlled rooms (L01)


def generate(seed: int = 42) -> dict[str, Any]:
    """Return the healthcare elements.json document (deterministic for a given seed)."""
    b = SynthBuilder("healthcare", "Hospital wing with imaging suite",
                     "Synthetic three-storey hospital wing: concrete frame, glazed south facade, "
                     "dense MEP and an imaging suite on the ground floor.", NX, NZ, seed)
    rng = b.rng
    for sid, name, idx in STOREYS:
        b.add_storey(sid, name, idx)

    def make_tags(sid: str):
        def tags(x0: int, z0: int, x1: int, z1: int) -> list[str]:
            out = []
            if x0 >= 4:
                out.append("occupied_adjacent")          # existing ward along the east edge
            if sid == "L00" and x0 == 0 and z0 == 0:
                out.append("heavy_lift_area")
            if sid == "L01" and x0 == 0 and z0 in (0, 2):
                out.append("pressure_room")
            if sid == "L02" and x0 == 0 and z0 == 0:
                out.append("pressure_room")
            if sid in ("L01", "L02") and x0 == 0 and z0 in (0, 2):
                out.append("or_room")                    # two operating theatres per upper storey
            if sid == "L00" and x0 == 0 and z0 == 0:
                out.append("imaging")
            if sid == "L02" and x0 == 4 and z0 == 4:
                out.append("plant_room")                 # roof plant (chiller, boiler)
            return out
        return tags

    def make_faces(sid: str):
        tags = make_tags(sid)

        def faces(x0: int, z0: int, x1: int, z1: int) -> dict[str, int]:
            t = set(tags(x0, z0, x1, z1))
            if "plant_room" in t:
                return {"plant_pad": 2, "ceiling_void": 1}
            if t & {"or_room", "imaging"}:
                return {"ceiling_void": 2, "walls": 2, "floor": 1}
            return {}
        return faces

    def make_crews(sid: str):
        tags = make_tags(sid)

        def crews(x0: int, z0: int, x1: int, z1: int) -> int:
            t = set(tags(x0, z0, x1, z1))
            if "plant_room" in t:
                return 4
            if t & {"or_room", "imaging"}:
                return 3
            return 4 if sid == "L00" else 2          # ground floor substructure zones 4, wards 2
        return crews

    for sid, _, _ in STOREYS:
        b.tile_zones(sid, 0, 0, NX, NZ, 4, 2, make_tags(sid), faces=make_faces(sid), crews=make_crews(sid))

    for i, sid in enumerate(("L00", "L01", "L02"), start=1):
        b.add_system(f"AHU-{i}", f"Air handling unit {i}", "mechanical")
    b.add_system("CHW", "Chilled water", "mechanical")
    b.add_system("HHW", "Heating hot water", "mechanical")
    b.add_system("DW", "Domestic water", "plumbing")
    b.add_system("MG-O2", "Medical gas oxygen", "medical")
    b.add_system("MG-MA", "Medical gas air", "medical")
    for i, sid in enumerate(("L00", "L01", "L02"), start=1):
        b.add_system(f"ELEC-{i}", f"Power distribution level {i - 1}", "electrical")
    b.add_system("FP-1", "Sprinklers", "fire")

    cells_all = [(x, z) for z in range(NZ) for x in range(NX)]
    col_guid: dict[Cell, str] = {}

    # --- structure
    for x, z in cells_all:
        v = round(rng.uniform(2.2, 3.2), 2)
        b.add("IfcFooting", f"Pad footing F-{x}-{z}", "L00", [(x, z)], predefined="PAD_FOOTING",
              material="Concrete C32/40", qty=q(volume=v, area=round(v / 0.8, 2)), props={"LoadBearing": True},
              bbox=b.centred((x, z), "L00", 2.4, 2.4, -0.8, 0))
    for sid, _, idx in STOREYS:
        for x, z in cells_all:
            parent = col_guid.get((x, z))
            g = b.add("IfcColumn", f"Column C-{x}-{z} {sid}", sid, [(x, z)], predefined="COLUMN",
                      material="Concrete C40/50 reinforced",
                      qty=q(volume=round(0.5 * 0.5 * H, 2), length=H),
                      props={"LoadBearing": True, **({"ParentGuid": parent} if parent else {})},
                      bbox=b.centred((x, z), sid, 0.5, 0.5, 0, H))
            col_guid[(x, z)] = g
        for z in range(0, NZ, 2):
            for x in range(0, NX, 2):
                cells = [(x, z), (x + 1, z), (x, z + 1), (x + 1, z + 1)]
                b.add("IfcSlab", f"Slab {sid} S-{x}-{z}", sid, cells,
                      predefined="BASESLAB" if idx == 0 else "FLOOR",
                      material="Concrete C32/40 reinforced", qty=q(volume=round(144 * 0.25, 1), area=144.0),
                      props={"LoadBearing": True}, bbox=b.box(cells, sid, -0.25, 0.0))
        if idx > 0:
            for (x, z, ax) in ([(x, 0, True) for x in range(NX)] + [(x, NZ - 1, True) for x in range(NX)]
                               + [(0, z, False) for z in range(1, NZ - 1)] + [(NX - 1, z, False) for z in range(1, NZ - 1)]):
                e0 = b.elevation(sid)
                bb = (b.box_xy((x * 6, z * 6 + 2.8, x * 6 + 6, z * 6 + 3.2), e0 + H - 0.6, e0 + H) if ax else
                      b.box_xy((x * 6 + 2.8, z * 6, x * 6 + 3.2, z * 6 + 6), e0 + H - 0.6, e0 + H))
                b.add("IfcBeam", f"Edge beam {sid} {x}-{z}{'x' if ax else 'z'}", sid, [(x, z)], predefined="BEAM",
                      material="Concrete C32/40 reinforced", qty=q(volume=round(0.4 * 0.6 * 6, 2), length=6.0),
                      props={"LoadBearing": True}, bbox=bb)
    # roof: 2x2 blocks on level 2
    for z in range(0, NZ, 2):
        for x in range(0, NX, 2):
            cells = [(x, z), (x + 1, z), (x, z + 1), (x + 1, z + 1)]
            b.add("IfcRoof", f"Roof R-{x}-{z}", "L02", cells, predefined="FLAT_ROOF",
                  material="Concrete with membrane roofing", qty=q(area=144.0, volume=36.0),
                  props={"IsExternal": True}, bbox=b.box(cells, "L02", H, H + 0.4))

    # --- envelope
    for sid, _, idx in STOREYS:
        ext: list[tuple[str, Cell, bool, str]] = []
        for x in range(NX):
            ext.append((f"N-{x}", (x, NZ - 1), True, "N"))
            ext.append((f"S-{x}", (x, 0), True, "S"))
        for z in range(NZ):
            ext.append((f"W-{z}", (0, z), False, "W"))
            ext.append((f"E-{z}", (NX - 1, z), False, "E"))
        for name, cell, along_x, side in ext:
            x, z = cell
            if side == "S" and idx > 0:
                b.add("IfcCurtainWall", f"Curtain wall {sid} {name}", sid, [cell], material="Aluminium and double glazing",
                      qty=q(area=6.0 * H, length=6.0), props={"IsExternal": True},
                      bbox=b.box_xy((x * 6, 0.0, x * 6 + 6, 0.2), b.elevation(sid), b.elevation(sid) + H))
                continue
            if along_x:
                y0 = z * 6 + (0.0 if side == "S" else 5.7)
                bb = b.box_xy((x * 6, y0, x * 6 + 6, y0 + 0.3), b.elevation(sid), b.elevation(sid) + H)
            else:
                x0 = x * 6 + (0.0 if side == "W" else 5.7)
                bb = b.box_xy((x0, z * 6, x0 + 0.3, z * 6 + 6), b.elevation(sid), b.elevation(sid) + H)
            wg = b.add("IfcWall", f"External wall {sid} {name}", sid, [cell], predefined="STANDARD",
                       material="Brick cavity wall with insulation",
                       qty=q(length=6.0, area=round(6.0 * H, 2), volume=round(6.0 * H * 0.3, 2)),
                       props={"IsExternal": True, "LoadBearing": False, "FireRating": 60}, bbox=bb)
            if side == "S" and x in (3, 4):
                b.add("IfcDoor", f"Entrance door {sid} {name}", sid, [cell], predefined="DOOR", material="Aluminium and glass",
                      host=wg, qty=q(area=2.4), props={"IsExternal": True, "Width": 1.5},
                      bbox=b.centred(cell, sid, 1.5, 0.3, 0, 2.4, dy=-2.7))
            elif not (side == "S"):
                b.add("IfcWindow", f"Window {sid} {name}", sid, [cell], predefined="WINDOW", material="Aluminium and double glazing",
                      host=wg, qty=q(area=3.0), props={"IsExternal": True},
                      bbox=b.centred(cell, sid, 2.0, 0.3, 0.9, 2.4))

    # --- interior partitions, doors, lead-lined imaging walls
    for sid, _, idx in STOREYS:
        walls: list[tuple[str, Cell, bool, dict]] = []
        for x in range(NX):
            walls.append((f"Corridor S wall {x}", (x, CORRIDOR_Z), True, {"edge": 0.0, "door": x in (1, 3, 5, 7)}))
            walls.append((f"Corridor N wall {x}", (x, CORRIDOR_Z), True, {"edge": 5.7, "door": x in (0, 2, 5)}))
        for x in (2, 4, 6):
            for z in (0, 1):
                walls.append((f"Room wall {x}-{z}", (x, z), False, {"edge": 0.0, "door": False}))
            for z in (3, 4, 5):
                walls.append((f"Room wall {x}-{z}", (x, z), False, {"edge": 0.0, "door": False}))
        for name, cell, along_x, info in walls:
            x, z = cell
            e0 = b.elevation(sid)
            if along_x:
                bb = b.box_xy((x * 6, z * 6 + info["edge"], x * 6 + 6, z * 6 + info["edge"] + 0.15), e0, e0 + H)
            else:
                bb = b.box_xy((x * 6 + info["edge"], z * 6, x * 6 + info["edge"] + 0.15, z * 6 + 6), e0, e0 + H)
            wg = b.add("IfcWall", f"Partition {sid} {name}", sid, [cell],
                       predefined="PARTITIONING",
                       material="Metal stud plasterboard",
                       qty=q(length=6.0, area=round(6.0 * H, 2), volume=round(6.0 * H * 0.15, 2)),
                       props={"IsExternal": False, "LoadBearing": False, "FireRating": 60}, bbox=bb)
            if info["door"]:
                b.add("IfcDoor", f"Door {sid} {name}", sid, [cell], predefined="DOOR", material="Hollow-core timber",
                      host=wg, qty=q(area=2.1), props={"IsExternal": False, "Width": 1.2},
                      bbox=b.centred(cell, sid, 1.2, 0.2, 0, 2.1, dy=-2.7 if info["edge"] < 1 else 2.7))
    # shielding walls for the imaging suite (explicit, so they always exist)
    for k, (cell, along_x, edge) in enumerate([((2, 0), False, 5.85), ((2, 1), False, 5.85), ((0, 1), True, 5.85),
                                              ((1, 1), True, 5.85), ((2, 1), True, 5.85)]):
        x, z = cell
        e0 = b.elevation("L00")
        bb = (b.box_xy((x * 6, z * 6 + edge, x * 6 + 6, z * 6 + edge + 0.15), e0, e0 + H) if along_x else
              b.box_xy((x * 6 + edge, z * 6, x * 6 + edge + 0.15, z * 6 + 6), e0, e0 + H))
        b.add("IfcWall", f"Lead-lined wall L00 imaging {k + 1}", "L00", [cell], predefined="PARTITIONING",
              material="Lead-lined plasterboard 3mm Pb", qty=q(length=6.0, area=round(6.0 * H, 2), volume=0.9),
              props={"Shielding": True, "LeadMm": 3, "FireRating": 120, "IsExternal": False}, bbox=bb)

    # --- finishes
    for sid, _, idx in STOREYS:
        for z in range(0, NZ, 2):
            for x in range(0, NX, 4):
                cells = [(x + i, z + j) for j in range(2) for i in range(4)]
                hygienic = (sid == "L01" and x == 0) or (sid == "L00" and x == 0 and z == 0) or (sid == "L02" and x == 0 and z == 0)
                b.add("IfcCovering", f"Floor finish {sid} {x}-{z}", sid, cells, predefined="FLOORING",
                      material="Welded sheet vinyl" if hygienic else "Carpet tile",
                      qty=q(area=288.0 * 0.9, volume=0.9), props={"Hygienic": hygienic},
                      bbox=b.box(cells, sid, 0.0, 0.01, inset=0.2))
                b.add("IfcCovering", f"Ceiling {sid} {x}-{z}", sid, cells, predefined="CEILING",
                      material="Sealed hygienic ceiling tile" if hygienic else "Mineral fibre tile",
                      qty=q(area=288.0 * 0.9, volume=1.0), props={"Hygienic": hygienic},
                      bbox=b.box(cells, sid, H - 0.6, H - 0.55, inset=0.2))

    # --- MEP
    for i, (sid, _, idx) in enumerate(STOREYS, start=1):
        e0 = b.elevation(sid)
        ahu, elec = f"AHU-{i}", f"ELEC-{i}"
        for x in range(NX):
            b.add("IfcDuctSegment", f"Supply duct {sid} {x + 1}", sid, [(x, CORRIDOR_Z)], predefined="RIGIDSEGMENT",
                  material="Galvanised sheet steel", system=ahu,
                  qty=q(length=6.0, area=round(6.0 * 1.6, 2), weight=0.12),
                  props={"Width_mm": 800, "Height_mm": 400}, bbox=b.centred((x, CORRIDOR_Z), sid, 6.0, 0.8, 3.0, 3.4, dy=-1.0))
            b.add("IfcDuctSegment", f"Return duct {sid} {x + 1}", sid, [(x, CORRIDOR_Z)], predefined="RIGIDSEGMENT",
                  material="Galvanised sheet steel", system=ahu, qty=q(length=6.0, area=round(6.0 * 1.4, 2), weight=0.1),
                  props={"Width_mm": 700, "Height_mm": 350}, bbox=b.centred((x, CORRIDOR_Z), sid, 6.0, 0.7, 3.0, 3.35, dy=1.0))
        for k, (system, nm) in enumerate([("CHW", "Chilled water"), ("HHW", "Heating water"), ("DW", "Cold water")]):
            for x in range(0, NX, 2):
                b.add("IfcPipeSegment", f"{nm} pipe {sid} {x // 2 + 1}", sid, [(x, CORRIDOR_Z)], predefined="RIGIDSEGMENT",
                      material="Copper" if system == "DW" else "Carbon steel", system=system,
                      qty=q(length=12.0, weight=round(rng.uniform(0.05, 0.2), 2)),
                      props={"Insulated": system != "DW", "Diameter_mm": 65 if system != "DW" else 28},
                      bbox=b.centred((x, CORRIDOR_Z), sid, 12.0, 0.15, 2.8, 2.95, dy=-2.0 + 0.4 * k))
        for system, nm in (("MG-O2", "Oxygen"), ("MG-MA", "Medical air")):
            for x in range(0, NX, 3):
                b.add("IfcPipeSegment", f"{nm} line {sid} {x // 3 + 1}", sid, [(x, CORRIDOR_Z)], predefined="RIGIDSEGMENT",
                      material="Degreased copper", system=system, qty=q(length=18.0, weight=0.08),
                      props={"Diameter_mm": 22, "Medical": True, "Hygienic": True},
                      bbox=b.centred((x, CORRIDOR_Z), sid, 18.0, 0.1, 2.6, 2.7, dy=2.0 + 0.3 * (system == "MG-MA")))
        for x in range(0, NX, 1):
            b.add("IfcCableCarrierSegment", f"Cable tray {sid} {x + 1}", sid, [(x, CORRIDOR_Z)], predefined="CABLETRAYSEGMENT",
                  material="Galvanised steel", system=elec, qty=q(length=6.0, weight=0.12),
                  bbox=b.centred((x, CORRIDOR_Z), sid, 6.0, 0.3, 3.45, 3.55, dy=2.2))
        for x in range(0, NX, 2):
            b.add("IfcCableSegment", f"Power cable {sid} {x // 2 + 1}", sid, [(x, CORRIDOR_Z)], predefined="CABLESEGMENT",
                  material="Copper LSZH", system=elec, qty=q(length=12.0, weight=0.06),
                  bbox=b.centred((x, CORRIDOR_Z), sid, 12.0, 0.1, 3.5, 3.6, dy=2.3))
        b.add("IfcFlowMovingDevice", f"Air handling unit AHU-{i}", sid, [(NX - 1, 0)], predefined="FAN",
              material="Steel casing", system=ahu, qty=q(weight=round(rng.uniform(2.5, 4.0), 1)),
              props={"LongLead": True, "LeadTimeWeeks": 16}, bbox=b.centred((NX - 1, 0), sid, 4.0, 2.0, 0, 2.5))
        b.add("IfcElectricDistributionBoard", f"Distribution board DB-{i}", sid, [(0, CORRIDOR_Z)],
              predefined="DISTRIBUTIONBOARD", material="Steel enclosure", system=elec, qty=q(weight=0.4),
              props={"LeadTimeWeeks": 10}, bbox=b.centred((0, CORRIDOR_Z), sid, 1.0, 0.3, 0, 2.0, dx=-2.5))
        for k in range(2):
            b.add("IfcFlowController", f"Fire damper {sid} {k + 1}", sid, [(2 + 3 * k, CORRIDOR_Z)], predefined="DAMPER",
                  material="Steel", system=ahu, qty=q(), bbox=b.centred((2 + 3 * k, CORRIDOR_Z), sid, 0.8, 0.4, 3.0, 3.4))
        for z in range(0, NZ, 2):
            for x in range(0, NX, 2):
                cells = [(x, z), (x + 1, z)]
                hygienic = sid == "L01" and x == 0
                b.add("IfcAirTerminal", f"Air terminal {sid} {x}-{z}", sid, cells, predefined="DIFFUSER",
                      material="Powder-coated aluminium", system=ahu, qty=q(),
                      props={"Hygienic": hygienic, "HEPA": hygienic}, bbox=b.centred(cells[0], sid, 0.6, 0.6, H - 0.65, H - 0.6, dx=3.0))
                b.add("IfcLightFixture", f"Light fixture {sid} {x}-{z}", sid, cells, predefined="DIRECTLUMINAIRE",
                      material="LED panel", system=elec, qty=q(), bbox=b.centred(cells[0], sid, 1.2, 0.3, H - 0.65, H - 0.6, dx=3.0, dy=1.5))
        for k, cell in enumerate([(4, 4), (5, 4), (6, 5)]):
            b.add("IfcSanitaryTerminal", f"Sanitary fixture {sid} {k + 1}", sid, [cell], predefined="WASHHANDBASIN",
                  material="Vitreous china", system="DW", qty=q(),
                  bbox=b.centred(cell, sid, 0.6, 0.5, 0.0, 0.9))
        b.add("IfcPipeSegment", f"Sprinkler main {sid}", sid, [(NX // 2, 3)], predefined="RIGIDSEGMENT", material="Black steel",
              system="FP-1", qty=q(length=24.0, weight=0.3), bbox=b.centred((NX // 2, 3), sid, 24.0, 0.15, 3.3, 3.45))

    # plant on the roof
    for k, nm in enumerate(["Chiller CH-1", "Boiler B-1"]):
        cell = (6 + k, 5)
        b.add("IfcEnergyConversionDevice", nm, "L02", [cell], predefined="CHILLER" if k == 0 else "BOILER",
              material="Steel", system="CHW" if k == 0 else "HHW", qty=q(weight=round(rng.uniform(8, 14), 1)),
              props={"LongLead": True, "LeadTimeWeeks": 24, "HeavyLift": True},
              bbox=b.centred(cell, "L02", 4.0, 2.5, H + 0.4, H + 3.0))

    # --- medical devices (long lead, heavy)
    devices = [("MRI scanner", "L00", (0, 0), 6.8, 26, "MRI"), ("CT scanner", "L00", (2, 0), 2.4, 20, "CT"),
               ("Digital X-ray", "L00", (1, 1), 0.9, 14, "XRAY"), ("Steriliser SS-1", "L00", (3, 4), 1.8, 12, "STERILISER"),
               ("Steriliser SS-2", "L00", (4, 4), 1.8, 12, "STERILISER"), ("Theatre light 1", "L01", (0, 0), 0.4, 8, "LIGHT"),
               ("Theatre light 2", "L01", (1, 1), 0.4, 8, "LIGHT"), ("Anaesthesia pendant 1", "L01", (0, 1), 0.5, 10, "PENDANT"),
               ("Anaesthesia pendant 2", "L01", (1, 0), 0.5, 10, "PENDANT")]
    for name, sid, cell, wt, lead, kind in devices:
        b.add("IfcMedicalDevice", name, sid, [cell], predefined="NOTDEFINED", material="Mixed",
              qty=q(weight=wt), props={"Device": kind, "LongLead": True, "LeadTimeWeeks": lead, "HeavyLift": wt > 2,
                                      "Shielding": kind in ("MRI", "CT", "XRAY")},
              bbox=b.centred(cell, sid, 2.5 if wt > 2 else 1.0, 2.0 if wt > 2 else 1.0, 0.0, 2.3 if wt > 2 else 3.0),
              visual="equipment")
    return b.document()
