"""Large synthetic hospital campus for scale tests: many copies of the healthcare wing on one world grid."""
from __future__ import annotations

import copy
from typing import Any

from . import healthcare

WING_W, WING_D = healthcare.NX, healthcare.NZ
GAP_CELLS = 2                      # street between wings
CELL = 6.0


def default_zone_tiles(wings_x: int, wings_z: int) -> tuple[int, int]:
    """Wings per zone group so a campus has at most 5 x 4 groups (and so a bounded zone/package count)."""
    return max(1, -(-wings_x // 5)), max(1, -(-wings_z // 4))


def generate(wings_x: int = 6, wings_z: int = 6, seed: int = 42, zone_tiles: tuple[int, int] | None = None) -> dict[str, Any]:
    """Return an elements.json document made of ``wings_x * wings_z`` healthcare wings (834 elements each).

    Each wing keeps its own seeded GUIDs, systems and zone structure; wings are offset on the grid and all
    share storeys L00..L02. To keep zone and package counts bounded for scale tests, the zones of every
    group of ``zone_tiles`` wings per storey are merged into one zone (``area`` style) and the systems are
    shared per group (``G<x>.<z>-AHU-1``), so aggregation folds a whole group's ducts into few elements.
    """
    zone_tiles = zone_tiles or default_zone_tiles(wings_x, wings_z)
    pitch_x, pitch_z = WING_W + GAP_CELLS, WING_D + GAP_CELLS
    base = healthcare.generate(seed)
    out: dict[str, Any] = {
        "schema_version": "1.0", "project": copy.deepcopy(base["project"]), "storeys": copy.deepcopy(base["storeys"]),
        "zones": [], "systems": [], "elements": [],
    }
    out["project"]["name"] = f"Hospital campus {wings_x}x{wings_z}"
    out["project"]["description"] = f"Synthetic stress model: {wings_x * wings_z} hospital wings."
    out["project"]["grid"]["width_cells"] = wings_x * pitch_x
    out["project"]["grid"]["depth_cells"] = wings_z * pitch_z
    zone_cells: dict[str, list[list[int]]] = {}
    zone_meta: dict[str, dict[str, Any]] = {}
    systems: dict[str, dict[str, Any]] = {}
    for wz in range(wings_z):
        for wx in range(wings_x):
            w = wz * wings_x + wx
            doc = base if w == 0 else healthcare.generate(seed + w)
            dx, dz = wx * pitch_x, wz * pitch_z
            group = (wx // zone_tiles[0], wz // zone_tiles[1])
            prefix = f"W{w:03d}-"
            sys_prefix = f"G{group[0]}.{group[1]}-"
            zmap: dict[str, str] = {}
            for z in doc["zones"]:
                new_id = f"{z['storey_id']}-G{group[0]}.{group[1]}"
                zmap[z["id"]] = new_id
                meta = zone_meta.setdefault(new_id, {"id": new_id, "name": f"{z['storey_id']} area {group[0]}.{group[1]}",
                                                     "storey_id": z["storey_id"], "max_crews": 4, "tags": []})
                for t in z.get("tags", []):
                    if t not in meta["tags"]:
                        meta["tags"].append(t)
                zone_cells.setdefault(new_id, []).extend([[c[0] + dx, c[1] + dz] for c in z["cells"]])
            for s in doc["systems"]:
                sid = sys_prefix + s["id"]
                systems.setdefault(sid, {**s, "id": sid, "name": f"{s['name']} (group {group[0]}.{group[1]})"})
            for e in doc["elements"]:
                e2 = dict(e)
                e2["guid"] = prefix + e["guid"]
                e2["zone_id"] = zmap[e["zone_id"]]
                e2["cells"] = [[c[0] + dx, c[1] + dz] for c in e["cells"]]
                if e.get("system_id"):
                    e2["system_id"] = sys_prefix + e["system_id"]
                if e.get("host_guid"):
                    e2["host_guid"] = prefix + e["host_guid"]
                if e.get("bbox"):
                    b = e["bbox"]
                    e2["bbox"] = {"min": [b["min"][0] + dx * CELL, b["min"][1] + dz * CELL, b["min"][2]],
                                  "max": [b["max"][0] + dx * CELL, b["max"][1] + dz * CELL, b["max"][2]]}
                props = e.get("properties") or {}
                if "ParentGuid" in props:
                    e2["properties"] = {**props, "ParentGuid": prefix + props["ParentGuid"]}
                out["elements"].append(e2)
    for zid in sorted(zone_meta):
        z = zone_meta[zid]
        z["cells"] = sorted(map(list, {tuple(c) for c in zone_cells[zid]}))
        out["zones"].append(z)
    out["systems"] = list(systems.values())
    return out
