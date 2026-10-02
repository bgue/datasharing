"""Synthetic civil project: road widening with a 3-span bridge, culvert and utilities (IFC 4.3 classes)."""
from __future__ import annotations

from typing import Any

from .base import SynthBuilder, q

LEN, WID = 30, 6                       # grid: chainage along x, lateral along z
LIVE_ROWS = (0, 1)                     # existing carriageway kept open (z)
WORK_ROWS = (2, 3, 4, 5)               # new widening
BRIDGE_X = range(12, 19)               # bridge cells x = 12..18
ABUTMENTS = (12, 18)
PIERS = (14, 16)
SUPPORTS = (12, 14, 16, 18)
CULVERT_X = 24
FILL_X = {10, 11, 19, 20}


def generate(seed: int = 42) -> dict[str, Any]:
    """Return the civil elements.json document (deterministic for a given seed)."""
    b = SynthBuilder("civil", "Road widening with bridge and culvert",
                     "Synthetic 180 m road widening: earthworks, pavement layers, 3-span bridge, "
                     "culvert, drainage and diverted utilities beneath.", LEN, WID, seed)
    rng = b.rng
    b.add_storey("L00", "Road level", 0)
    b.add_storey("UG1", "Underground utilities", -1)

    def tags(x0: int, z0: int, x1: int, z1: int) -> list[str]:
        out = []
        if z0 in LIVE_ROWS:
            out.append("live_traffic")
        bridge = x1 >= BRIDGE_X.start and x0 <= BRIDGE_X.stop - 1
        if bridge and z0 not in LIVE_ROWS:
            out.append("heavy_lift_area")
        out.append("bridge" if bridge else "segment")
        if x0 <= CULVERT_X <= x1 and z0 not in LIVE_ROWS:
            out.append("culvert")                       # zones holding the culvert segments and headwalls
        return out

    def faces(x0: int, z0: int, x1: int, z1: int) -> dict[str, int]:
        return {"below_ground": 2, "structure": 2, "floor": 2}      # floor = pavement surface

    def crews(x0: int, z0: int, x1: int, z1: int) -> int:
        t = set(tags(x0, z0, x1, z1))
        return 4 if "bridge" in t else 3                  # bridge 4; culvert and road segments 3

    b.tile_zones("L00", 0, 0, LEN, WID, 5, 2, tags, faces=faces, crews=crews)
    b.tile_zones("UG1", 0, 0, LEN, WID, 5, 2, lambda x0, z0, x1, z1: ["live_traffic"] if z0 in LIVE_ROWS else [])

    b.add_system("DR-01", "Storm drainage", "civil")
    b.add_system("UT-WATER", "Diverted water main", "plumbing")
    b.add_system("UT-GAS", "Diverted gas main", "process")
    b.add_system("UT-COMMS", "Communications ducts", "electrical")

    road_x = [x for x in range(LEN) if x not in BRIDGE_X]

    # --- earthworks per work cell (cut in the cuttings, fill on approach embankments)
    for x in road_x:
        for z in WORK_ROWS:
            fill = x in FILL_X
            depth = round(rng.uniform(0.8, 1.6) if fill else rng.uniform(0.9, 2.6), 2)
            vol = round(36.0 * depth, 1)
            cls = "IfcEarthworksFill" if fill else "IfcEarthworksCut"
            b.add(cls, f"{'Fill' if fill else 'Cut'} CH{x * 6:03d}-{z}", "L00", [(x, z)],
                  predefined="EMBANKMENT" if fill else "TRENCH", material="Fill material" if fill else "Mixed soil",
                  qty=q(volume=vol, area=36.0), props={"Depth_m": depth, "Material": "granular" if fill else "soil"},
                  bbox=b.box([(x, z)], "L00", -depth, 0.0))

    # --- pavement build-up
    layers = [("IfcCourse", "SUBBASE", "Sub-base", "Crushed rock Type 1", 0.25),
              ("IfcCourse", "BASE", "Base course", "Asphalt base AC 20", 0.20),
              ("IfcPavement", "FLEXIBLE", "Wearing course", "Asphalt SMA 14", 0.10)]
    for x in road_x:
        for z in WORK_ROWS:
            z0 = 0.0
            for cls, ptype, nm, mat, t in layers:
                b.add(cls, f"{nm} CH{x * 6:03d}-{z}", "L00", [(x, z)], predefined=ptype, material=mat,
                      qty=q(volume=round(36.0 * t, 2), area=36.0), props={"Thickness_m": t, "Layer": ptype},
                      bbox=b.box([(x, z)], "L00", z0, z0 + t))
                z0 += t
    # kerbs on both edges of the widened carriageway
    for x in road_x:
        for z, side in ((2, "inner"), (5, "outer")):
            b.add("IfcKerb", f"Kerb {side} CH{x * 6:03d}", "L00", [(x, z)], predefined="KERB", material="Precast concrete",
                  qty=q(length=6.0, volume=0.6), bbox=b.box_xy((x * 6, z * 6 + (0 if side == "inner" else 5.7),
                                                               x * 6 + 6, z * 6 + (0.3 if side == "inner" else 6)),
                                                              0, 0.2))
    for x in range(LEN):
        b.add("IfcRailing", f"Safety barrier CH{x * 6:03d}", "L00", [(x, 5)], predefined="GUARDRAIL",
              material="Galvanised steel", qty=q(length=6.0, weight=0.35),
              bbox=b.box_xy((x * 6, 5 * 6 + 5.6, x * 6 + 6, 5 * 6 + 5.8), 0, 0.9))

    # --- bridge: piles, abutments, piers, bearings, girders, deck
    zs = list(WORK_ROWS)
    for sx in SUPPORTS:
        kind = "Abutment" if sx in ABUTMENTS else "Pier"
        for k, z in enumerate(zs):
            b.add("IfcPile", f"Bored pile {kind[0]}{SUPPORTS.index(sx) + 1}-{k + 1}", "L00", [(sx, z)],
                  predefined="BORED", material="Concrete C35/45 reinforced",
                  qty=q(length=18.0, volume=round(3.14159 * 0.45 ** 2 * 18, 1)),
                  props={"BridgePart": "foundation", "LeadTimeWeeks": 0},
                  bbox=b.centred((sx, z), "L00", 0.9, 0.9, -18.0, 0))
    for sx in PIERS:
        b.add("IfcDeepFoundation", f"Pile cap P{PIERS.index(sx) + 1}", "L00", [(sx, 3), (sx, 4)],
              material="Concrete C35/45 reinforced", qty=q(volume=62.0, area=40.0),
              props={"BridgePart": "pile_cap"}, bbox=b.box([(sx, 3), (sx, 4)], "L00", -1.5, 0, inset=1.0))
        for z in zs:
            b.add("IfcColumn", f"Pier column P{PIERS.index(sx) + 1}-{z}", "L00", [(sx, z)], predefined="COLUMN",
                  material="Concrete C40/50 reinforced", qty=q(volume=round(3.14159 * 0.8 ** 2 * 6.5, 1), length=6.5),
                  props={"BridgePart": "pier", "LoadBearing": True},
                  bbox=b.centred((sx, z), "L00", 1.6, 1.6, 0, 6.5))
    for sx in ABUTMENTS:
        for z in zs:
            b.add("IfcWall", f"Abutment wall A{ABUTMENTS.index(sx) + 1}-{z}", "L00", [(sx, z)], predefined="RETAININGWALL",
                  material="Concrete C35/45 reinforced", qty=q(volume=48.0, area=36.0, length=6.0),
                  props={"BridgePart": "abutment", "LoadBearing": True},
                  bbox=b.centred((sx, z), "L00", 1.2, 6.0, 0, 6.0))
    for sx in SUPPORTS:
        for z in zs:
            b.add("IfcBearing", f"Elastomeric bearing S{SUPPORTS.index(sx) + 1}-{z}", "L00", [(sx, z)],
                  predefined="ELASTOMERIC", material="Elastomer", qty=q(weight=0.35),
                  props={"BridgePart": "bearing", "LeadTimeWeeks": 12},
                  bbox=b.centred((sx, z), "L00", 0.6, 0.6, 6.5, 6.8))
    for span, sx in enumerate((12, 14, 16)):
        for z in zs:
            cells = [(sx, z), (sx + 1, z), (sx + 2, z)]
            b.add("IfcBeam", f"Precast girder G{span + 1}-{z}", "L00", cells, predefined="BEAM",
                  material="Prestressed concrete C60/75", qty=q(volume=round(rng.uniform(34, 40), 1), length=12.0,
                                                               weight=round(rng.uniform(85, 100), 1)),
                  props={"BridgePart": "girder", "LoadBearing": True, "Precast": True, "LeadTimeWeeks": 10},
                  bbox=b.box_xy((sx * 6, z * 6 + 2.4, sx * 6 + 12, z * 6 + 3.6), 6.8, 8.0))
    for x in BRIDGE_X:
        for z in zs:
            b.add("IfcSlab", f"Deck slab CH{x * 6:03d}-{z}", "L00", [(x, z)], predefined="BASESLAB",
                  material="Concrete C40/50 reinforced", qty=q(volume=round(36.0 * 0.25, 1), area=36.0),
                  props={"BridgePart": "deck", "LoadBearing": True}, bbox=b.box([(x, z)], "L00", 8.0, 8.25))
            b.add("IfcPavement", f"Deck surfacing CH{x * 6:03d}-{z}", "L00", [(x, z)], predefined="FLEXIBLE",
                  material="Mastic asphalt", qty=q(volume=round(36.0 * 0.08, 2), area=36.0),
                  props={"Layer": "deck_surfacing"}, bbox=b.box([(x, z)], "L00", 8.25, 8.33))
        for z, side in ((2, "inner"), (5, "outer")):
            b.add("IfcRailing", f"Bridge parapet {side} CH{x * 6:03d}", "L00", [(x, z)], predefined="GUARDRAIL",
                  material="Galvanised steel", qty=q(length=6.0, weight=0.5), props={"BridgePart": "parapet"},
                  bbox=b.box_xy((x * 6, z * 6 + (0 if side == "inner" else 5.8), x * 6 + 6,
                                 z * 6 + (0.2 if side == "inner" else 6)), 8.33, 9.4))
            b.add("IfcKerb", f"Bridge kerb {side} CH{x * 6:03d}", "L00", [(x, z)], predefined="KERB",
                  material="Precast concrete", qty=q(length=6.0, volume=0.6),
                  bbox=b.box_xy((x * 6, z * 6 + (0.2 if side == "inner" else 5.5), x * 6 + 6,
                                 z * 6 + (0.5 if side == "inner" else 5.8)), 8.25, 8.45))

    # --- culvert under the road
    for z in zs:
        b.add("IfcBuildingElementProxy", f"Culvert segment C{z - 1}", "L00", [(CULVERT_X, z)], predefined="USERDEFINED",
              material="Precast concrete box", qty=q(volume=18.0, length=6.0, weight=45.0, area=36.0),
              props={"Precast": True, "LeadTimeWeeks": 8}, bbox=b.centred((CULVERT_X, z), "L00", 6.0, 6.0, -2.5, 0.0),
              visual="culvert")
    for z, side in ((2, "inlet"), (5, "outlet")):
        b.add("IfcWall", f"Culvert headwall {side}", "L00", [(CULVERT_X, z)], predefined="RETAININGWALL",
              material="Concrete C32/40", qty=q(volume=14.0, area=20.0, length=6.0),
              props={"Culvert": True, "LoadBearing": True},
              bbox=b.box_xy((CULVERT_X * 6, z * 6 + (0 if side == "inlet" else 5.6), CULVERT_X * 6 + 6,
                             z * 6 + (0.4 if side == "inlet" else 6)), -2.5, 1.0))

    # --- signs (temporary traffic management and permanent)
    for i, x in enumerate(range(1, LEN, 2)):
        b.add("IfcSign", f"Sign {i + 1}", "L00", [(x, 1)], predefined="MARKER" if i % 3 else "PICTORAL",
              material="Aluminium", qty=q(), props={"Temporary": i % 2 == 0},
              bbox=b.centred((x, 1), "L00", 0.8, 0.1, 0, 3.0, dy=2.5))

    # --- underground utilities (storey -1)
    for x in range(LEN):
        b.add("IfcPipeSegment", f"Storm drain CH{x * 6:03d}", "UG1", [(x, 2)], predefined="CULVERT",
              material="Concrete pipe DN600", system="DR-01", qty=q(length=6.0, weight=2.2),
              props={"Diameter_mm": 600, "Gradient": 0.005}, bbox=b.centred((x, 2), "UG1", 6.0, 0.8, -2.8, -2.0))
        b.add("IfcPipeSegment", f"Water main CH{x * 6:03d}", "UG1", [(x, 0)], predefined="RIGIDSEGMENT",
              material="Ductile iron DN300", system="UT-WATER", qty=q(length=6.0, weight=0.7),
              props={"Diameter_mm": 300, "Diverted": True}, bbox=b.centred((x, 0), "UG1", 6.0, 0.4, -1.5, -1.1))
        b.add("IfcCableCarrierSegment", f"Comms duct CH{x * 6:03d}", "UG1", [(x, 1)], predefined="CABLETRUNKINGSEGMENT",
              material="HDPE duct bank", system="UT-COMMS", qty=q(length=6.0, weight=0.3),
              bbox=b.centred((x, 1), "UG1", 6.0, 0.5, -1.0, -0.6))
        if x < 12:
            b.add("IfcPipeSegment", f"Gas main CH{x * 6:03d}", "UG1", [(x, 0)], predefined="RIGIDSEGMENT",
                  material="PE100 pipe DN200", system="UT-GAS", qty=q(length=6.0, weight=0.2),
                  props={"Diameter_mm": 200, "Diverted": True}, bbox=b.centred((x, 0), "UG1", 6.0, 0.3, -1.4, -1.1, dy=1.0))
        if x % 3 == 1:
            b.add("IfcDistributionChamberElement", f"Manhole MH{x // 3 + 1}", "UG1", [(x, 2)], predefined="MANHOLE",
                  material="Precast concrete ring", system="DR-01", qty=q(volume=6.5, length=3.0),
                  props={"Depth_m": 3.0}, bbox=b.centred((x, 2), "UG1", 1.5, 1.5, -3.0, 0.0))
    return b.document()
