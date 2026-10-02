"""Synthetic industrial project: 12x8-bay process building, pipe rack and yard equipment."""
from __future__ import annotations

from typing import Any

from .base import Cell, SynthBuilder, q

BAYS_X, BAYS_Z = 12, 8          # process building bays
RACK_Z, YARD_Z = BAYS_Z, BAYS_Z + 1
HEIGHT = 10.0                   # eaves height (m)
MODULE_SITES: list[Cell] = [(1, 1), (4, 1), (7, 1), (1, 3), (4, 3), (7, 3), (10, 3), (2, 5), (5, 5), (8, 5)]


def generate(seed: int = 42) -> dict[str, Any]:
    """Return the industrial elements.json document (deterministic for a given seed)."""
    b = SynthBuilder("industrial", "Process building with pipe rack",
                     "Synthetic single-storey process building (12x8 bays) with a pipe rack, equipment "
                     "modules, tanks and transformers.", BAYS_X, BAYS_Z + 2, seed)
    rng = b.rng
    b.add_storey("L00", "Ground", 0)
    module_cells = {(x + dx, z) for (x, z) in MODULE_SITES for dx in (0, 1)}

    def tags(x0: int, z0: int, x1: int, z1: int) -> list[str]:
        out = []
        if any(x0 <= x <= x1 and z0 <= z <= z1 for (x, z) in module_cells):
            out.append("heavy_lift_area")
        if any(x0 <= x <= x1 and z0 <= z <= z1 for (x, z) in module_cells):
            out.append("process_unit")
        if z0 == RACK_Z:
            out.append("pipe_rack")
        return out

    def faces(x0: int, z0: int, x1: int, z1: int) -> dict[str, int]:
        if set(tags(x0, z0, x1, z1)) & {"pipe_rack", "process_unit"}:
            return {"structure": 2, "plant_pad": 2, "ceiling_void": 1}
        return {}

    b.tile_zones("L00", 0, 0, BAYS_X, BAYS_Z + 2, 4, 2, tags, faces=faces)

    for i in range(1, 7):
        b.add_system(f"PR-{i:02d}", f"Pipe rack line {i}", "process")
    for i in range(1, 11):
        b.add_system(f"P-{100 + i}", f"Process unit {i}", "process")
    b.add_system("ELEC-HV", "11 kV distribution", "electrical")
    b.add_system("ELEC-LV", "415 V distribution", "electrical")
    b.add_system("INSTR-1", "Instrumentation and control", "instrumentation")

    bay_cells = [(x, z) for z in range(BAYS_Z) for x in range(BAYS_X)]

    # --- foundations and ground slab
    for x, z in bay_cells:
        v = round(rng.uniform(4.5, 6.5), 2)
        b.add("IfcFooting", f"Pad footing F-{x}-{z}", "L00", [(x, z)], predefined="PAD_FOOTING",
              material="Concrete C32/40", qty=q(volume=v, area=round(v / 0.9, 2)),
              props={"LoadBearing": True}, bbox=b.centred((x, z), "L00", 2.6, 2.6, -0.9, 0))
    for z in range(0, BAYS_Z, 2):
        for x in range(0, BAYS_X, 2):
            cells = [(x, z), (x + 1, z), (x, z + 1), (x + 1, z + 1)]
            b.add("IfcSlab", f"Ground slab S-{x}-{z}", "L00", cells, predefined="BASESLAB",
                  material="Concrete C32/40 reinforced", qty=q(volume=43.2, area=144.0),
                  props={"LoadBearing": True}, bbox=b.box(cells, "L00", -0.3, 0))

    # --- structural steel
    for x, z in bay_cells:
        w = round(rng.uniform(0.9, 1.6), 2)
        b.add("IfcColumn", f"Steel column C-{x}-{z}", "L00", [(x, z)], predefined="COLUMN",
              material="Structural steel S355", qty=q(length=HEIGHT, weight=w, volume=round(w / 7.85, 3)),
              props={"LoadBearing": True, "ProfileName": "HEB 300"},
              bbox=b.centred((x, z), "L00", 0.4, 0.4, 0, HEIGHT))
    for x, z in bay_cells:
        w = round(rng.uniform(0.7, 1.3), 2)
        b.add("IfcBeam", f"Roof beam X B-{x}-{z}", "L00", [(x, z)], predefined="BEAM",
              material="Structural steel S355", qty=q(length=6.0, weight=w, volume=round(w / 7.85, 3)),
              props={"LoadBearing": True}, bbox=b.box_xy((x * 6, z * 6 + 2.8, x * 6 + 6, z * 6 + 3.2),
                                                        HEIGHT - 0.6, HEIGHT))
    for x, z in bay_cells:
        if z == BAYS_Z - 1:
            continue
        w = round(rng.uniform(0.5, 0.9), 2)
        b.add("IfcBeam", f"Roof beam Z B-{x}-{z}", "L00", [(x, z)], predefined="JOIST",
              material="Structural steel S355", qty=q(length=6.0, weight=w, volume=round(w / 7.85, 3)),
              props={"LoadBearing": True}, bbox=b.box_xy((x * 6 + 2.8, z * 6, x * 6 + 3.2, z * 6 + 6),
                                                        HEIGHT - 0.6, HEIGHT))

    # --- envelope: cladding, doors, windows, roof
    walls: list[tuple[str, Cell, bool, int]] = []     # name, cell, runs_along_x, side
    for x in range(BAYS_X):
        walls.append((f"Cladding S-{x}", (x, 0), True, 0))
        walls.append((f"Cladding N-{x}", (x, BAYS_Z - 1), True, 1))
    for z in range(BAYS_Z):
        walls.append((f"Cladding W-{z}", (0, z), False, 0))
        walls.append((f"Cladding E-{z}", (BAYS_X - 1, z), False, 1))
    door_walls = {"Cladding S-3", "Cladding S-8", "Cladding E-2", "Cladding E-5", "Cladding W-3",
                  "Cladding N-6"}
    window_walls = {f"Cladding N-{x}" for x in (1, 3, 5, 8, 10)} | {"Cladding S-1", "Cladding S-10",
                                                                    "Cladding W-6"}
    for name, cell, along_x, side in walls:
        x, z = cell
        if along_x:
            y = z * 6 + (0.15 if side == 0 else 5.85)
            bb = b.box_xy((x * 6, y - 0.15, x * 6 + 6, y + 0.15), 0, HEIGHT)
        else:
            xx = x * 6 + (0.15 if side == 0 else 5.85)
            bb = b.box_xy((xx - 0.15, z * 6, xx + 0.15, z * 6 + 6), 0, HEIGHT)
        wg = b.add("IfcWall", name, "L00", [cell], predefined="STANDARD",
                   material="Insulated metal cladding panel",
                   qty=q(length=6.0, area=6.0 * HEIGHT, volume=round(6.0 * HEIGHT * 0.15, 2)),
                   props={"IsExternal": True, "LoadBearing": False}, bbox=bb)
        if name in door_walls:
            b.add("IfcDoor", name.replace("Cladding", "Roller door"), "L00", [cell], predefined="DOOR",
                  material="Steel", host=wg, qty=q(area=12.0), props={"IsExternal": True, "Width": 4.0},
                  bbox=b.centred(cell, "L00", 4.0, 0.3, 0, 4.5))
        if name in window_walls:
            b.add("IfcWindow", name.replace("Cladding", "Clerestory window"), "L00", [cell],
                  predefined="WINDOW", material="Aluminium glazing", host=wg, qty=q(area=4.5),
                  props={"IsExternal": True}, bbox=b.centred(cell, "L00", 3.0, 0.2, 6.0, 7.5))
    for z in range(0, BAYS_Z, 2):
        for x in range(0, BAYS_X, 2):
            cells = [(x, z), (x + 1, z), (x, z + 1), (x + 1, z + 1)]
            b.add("IfcRoof", f"Roof sheeting R-{x}-{z}", "L00", cells, predefined="FLAT_ROOF",
                  material="Insulated metal cladding panel", qty=q(area=144.0, volume=21.6),
                  bbox=b.box(cells, "L00", HEIGHT, HEIGHT + 0.3))

    # --- access platform, stairs, railings, chimney
    for i, cell in enumerate([(0, 0), (11, 7), (6, 4)]):
        b.add("IfcStair", f"Access stair ST-{i + 1}", "L00", [cell], predefined="STRAIGHT_RUN_STAIR",
              material="Galvanised steel", qty=q(length=7.5, weight=1.8, area=6.0), props={"Rise": 6.0},
              bbox=b.centred(cell, "L00", 1.4, 5.0, 0, 6.0, dx=-1.0))
        for k in range(3):
            b.add("IfcRailing", f"Handrail ST-{i + 1}-{k + 1}", "L00", [cell], predefined="HANDRAIL",
                  material="Galvanised steel", qty=q(length=6.0, weight=0.12),
                  bbox=b.centred(cell, "L00", 0.1, 5.0, 0.0, 1.1, dx=-1.8 + 0.4 * k))
    b.add("IfcChimney", "Exhaust stack", "L00", [(10, YARD_Z)], material="Steel", system="P-101",
          qty=q(length=30, weight=14),
          props={"LeadTimeWeeks": 16}, bbox=b.centred((10, YARD_Z), "L00", 1.6, 1.6, 0, 30))

    # --- pipe rack along z = 8
    sys_cycle = [f"PR-{i:02d}" for i in range(1, 7)]
    for x in range(BAYS_X):
        cell = (x, RACK_Z)
        b.add("IfcFooting", f"Rack footing RF-{x}", "L00", [cell], predefined="PAD_FOOTING",
              material="Concrete C32/40", qty=q(volume=7.2, area=8.0), props={"LoadBearing": True},
              bbox=b.centred(cell, "L00", 3.0, 3.0, -1.0, 0))
        for side, dy in (("a", -1.5), ("b", 1.5)):
            b.add("IfcMember", f"Rack leg {x}{side}", "L00", [cell], predefined="POST",
                  material="Structural steel S355", qty=q(length=7.0, weight=0.65),
                  props={"RackTier": 0, "LoadBearing": True},
                  bbox=b.centred(cell, "L00", 0.3, 0.3, 0, 7.0, dy=dy))
        for tier, zt in ((1, 5.0), (2, 7.0)):
            b.add("IfcMember", f"Rack beam T{tier}-{x}", "L00", [cell], predefined="BRACE",
                  material="Structural steel S355", qty=q(length=3.5, weight=0.4),
                  props={"RackTier": tier}, bbox=b.centred(cell, "L00", 0.3, 3.5, zt - 0.3, zt))
    for k, sysid in enumerate(sys_cycle):
        dy = -1.25 + k * 0.5
        for x in range(BAYS_X):
            cell = (x, RACK_Z)
            b.add("IfcPipeSegment", f"Rack pipe {sysid} seg {x + 1}", "L00", [cell], predefined="RIGIDSEGMENT",
                  material="Carbon steel", system=sysid,
                  qty=q(length=6.0, weight=round(rng.uniform(0.4, 1.1), 2)),
                  props={"Diameter_mm": rng.choice([150, 200, 250, 300]), "Insulated": k < 3},
                  bbox=b.centred(cell, "L00", 6.0, 0.3, 5.0 + (k % 2) * 2 - 0.2, 5.0 + (k % 2) * 2 + 0.2, dy=dy))
        b.add("IfcPipeFitting", f"Rack expansion loop {sysid}", "L00", [(BAYS_X // 2, RACK_Z)],
              predefined="BEND", material="Carbon steel", system=sysid, qty=q(weight=0.3),
              bbox=b.centred((BAYS_X // 2, RACK_Z), "L00", 1.0, 1.0, 5.0, 6.0, dy=dy))
        if k < 3:   # insulation on hot lines, one piece per rack bay
            for x in range(BAYS_X):
                b.add("IfcCovering", f"Rack insulation {sysid} {x + 1}", "L00", [(x, RACK_Z)],
                      predefined="INSULATION", material="Mineral wool with aluminium cladding",
                      system=sysid, qty=q(area=round(6.0 * 0.9, 2), length=6.0),
                      bbox=b.centred((x, RACK_Z), "L00", 6.0, 0.5, 4.7, 5.3, dy=dy))
    for x in range(BAYS_X):
        b.add("IfcCableCarrierSegment", f"Rack cable tray {x + 1}", "L00", [(x, RACK_Z)],
              predefined="CABLELADDERSEGMENT", material="Galvanised steel", system="ELEC-LV",
              qty=q(length=6.0, weight=0.18), bbox=b.centred((x, RACK_Z), "L00", 6.0, 0.4, 7.2, 7.4, dy=1.3))

    # --- equipment modules with process piping
    for i, (x, z) in enumerate(MODULE_SITES):
        cells = [(x, z), (x + 1, z)]
        sysid = f"P-{101 + i}"
        wt = round(rng.uniform(20, 120), 1)
        b.add("IfcFooting", f"Module foundation MF-{i + 1}", "L00", cells, predefined="PAD_FOOTING",
              material="Concrete C40/50", qty=q(volume=round(wt * 0.35, 1), area=40.0),
              props={"LoadBearing": True}, bbox=b.box(cells, "L00", -1.2, 0.0, inset=0.8))
        b.add("IfcBuildingElementProxy", f"Process module M-{i + 1:02d}", "L00", cells, predefined="USERDEFINED",
              material="Structural steel skid", system=sysid,
              qty=q(weight=wt, volume=round(wt * 0.9, 1), area=36.0),
              props={"Module": True, "LongLead": True, "LeadTimeWeeks": rng.choice([14, 18, 22, 26]),
                     "HeavyLift": wt > 60},
              bbox=b.box(cells, "L00", 0.0, 7.5, inset=1.0), visual="equipment")
        b.add("IfcFlowMovingDevice", f"Pump P-{101 + i}A", "L00", [(x, z + 1)], predefined="PUMP",
              material="Cast steel", system=sysid, qty=q(weight=round(rng.uniform(0.8, 2.5), 2)),
              bbox=b.centred((x, z + 1), "L00", 1.2, 0.8, 0, 1.0))
        for k in range(2):
            b.add("IfcFlowController", f"Isolation valve {sysid}-{k + 1}", "L00", [(x, z + 1 + k)],
                  predefined="VALVE", material="Carbon steel", system=sysid, qty=q(weight=0.15),
                  bbox=b.centred((x, z + 1 + k), "L00", 0.4, 0.4, 1.0, 1.6, dx=1.2))
        for zz in range(z + 1, BAYS_Z):
            b.add("IfcPipeSegment", f"Process pipe {sysid} seg {zz - z}", "L00", [(x, zz)],
                  predefined="RIGIDSEGMENT", material="Stainless steel 316", system=sysid,
                  qty=q(length=6.0, weight=round(rng.uniform(0.2, 0.6), 2)),
                  props={"Diameter_mm": rng.choice([80, 100, 150])},
                  bbox=b.centred((x, zz), "L00", 0.25, 6.0, 4.0, 4.3, dx=0.5))
        b.add("IfcPipeFitting", f"Process tee {sysid}", "L00", [(x, BAYS_Z - 1)], predefined="JUNCTION",
              material="Stainless steel 316", system=sysid, qty=q(weight=0.2),
              bbox=b.centred((x, BAYS_Z - 1), "L00", 0.6, 0.6, 4.0, 4.6, dx=0.5))
        b.add("IfcSensor", f"Pressure transmitter {sysid}", "L00", [(x, z + 1)], predefined="PRESSURESENSOR",
              material="Stainless steel", system="INSTR-1", qty=q(), props={"Instrument": True},
              bbox=b.centred((x, z + 1), "L00", 0.3, 0.3, 1.5, 1.9, dx=-1.0), visual="terminal")

    # --- electrical: boards, trays, cables
    for i, cell in enumerate([(0, 7), (11, 7), (0, 0), (11, 0)]):
        b.add("IfcElectricDistributionBoard", f"MCC-{i + 1}", "L00", [cell], predefined="MOTORCONTROLCENTRE",
              material="Steel enclosure", system="ELEC-LV", qty=q(weight=1.6),
              props={"LeadTimeWeeks": 20}, bbox=b.centred(cell, "L00", 3.0, 0.8, 0, 2.2))
    for x in range(BAYS_X):
        for z, tag in ((BAYS_Z - 1, "N"), (0, "S")):
            b.add("IfcCableCarrierSegment", f"Cable tray {tag}-{x + 1}", "L00", [(x, z)],
                  predefined="CABLETRAYSEGMENT", material="Galvanised steel", system="ELEC-LV",
                  qty=q(length=6.0, weight=0.15),
                  bbox=b.centred((x, z), "L00", 6.0, 0.4, 7.0, 7.15, dy=-2.0 if tag == "N" else 2.0))
        b.add("IfcCableSegment", f"Power cable run {x + 1}", "L00", [(x, BAYS_Z - 1)],
              predefined="CABLESEGMENT", material="Copper XLPE", system="ELEC-LV", qty=q(length=6.0, weight=0.05),
              bbox=b.centred((x, BAYS_Z - 1), "L00", 6.0, 0.1, 7.2, 7.3, dy=-2.0))

    # --- yard: transformers, heaters, tanks
    yard: list[tuple[str, str, str, str | None, dict]] = [
        ("IfcTransformer", "Transformer T-1", "ELEC-HV", None, {"w": 38.0, "lead": 40}),
        ("IfcTransformer", "Transformer T-2", "ELEC-HV", None, {"w": 38.0, "lead": 40}),
        ("IfcEnergyConversionDevice", "Fired heater H-1", "P-101", "USERDEFINED", {"w": 55.0, "lead": 30}),
        ("IfcEnergyConversionDevice", "Heat exchanger E-1", "P-102", "HEATEXCHANGER", {"w": 24.0, "lead": 20}),
        ("IfcTank", "Storage tank TK-1", "P-103", "STORAGE", {"w": 42.0, "lead": 24}),
        ("IfcTank", "Storage tank TK-2", "P-104", "STORAGE", {"w": 42.0, "lead": 24}),
        ("IfcTank", "Day tank TK-3", "P-105", "STORAGE", {"w": 18.0, "lead": 16}),
        ("IfcTank", "Buffer tank TK-4", "P-106", "STORAGE", {"w": 18.0, "lead": 16}),
    ]
    for i, (cls, name, sysid, ptype, info) in enumerate(yard):
        cell = (i, YARD_Z)
        b.add("IfcFooting", f"{name} plinth", "L00", [cell], predefined="PAD_FOOTING", material="Concrete C32/40",
              qty=q(volume=round(info["w"] * 0.25, 1), area=25.0), props={"LoadBearing": True},
              bbox=b.centred(cell, "L00", 5.0, 5.0, -0.8, 0))
        tall = 14.0 if cls == "IfcTank" else 4.0
        b.add(cls, name, "L00", [cell], predefined=ptype, material="Carbon steel", system=sysid,
              qty=q(weight=info["w"], volume=round(info["w"] * 1.5, 1)),
              props={"LongLead": True, "LeadTimeWeeks": info["lead"], "Module": False},
              bbox=b.centred(cell, "L00", 4.5, 4.5, 0, tall))
    for k in range(4):   # HV cables from the transformers to the motor control centres
        b.add("IfcCableSegment", f"HV cable run {k + 1}", "L00", [(k, YARD_Z)], predefined="CABLESEGMENT",
              material="Copper XLPE 11 kV", system="ELEC-HV", qty=q(length=6.0, weight=0.12),
              bbox=b.centred((k, YARD_Z), "L00", 6.0, 0.1, 0.2, 0.3, dy=2.4))
    for x in range(0, BAYS_X, 2):
        cells = [(x, YARD_Z), (x + 1, YARD_Z)]
        b.add("IfcSlab", f"Yard slab Y-{x}", "L00", cells, predefined="BASESLAB", material="Concrete C32/40",
              qty=q(volume=21.6, area=72.0), props={"LoadBearing": True}, bbox=b.box(cells, "L00", -0.3, 0))
    return b.document()
