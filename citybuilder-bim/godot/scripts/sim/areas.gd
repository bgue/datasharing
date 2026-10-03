class_name Areas
extends RefCounted
## Areas of a project (`project.areas[]`, parsed by SequenceBundle into `bundle.areas`): named parts of a big site with
## their cells, storeys and zones, used for camera bookmarks (the Areas panel, key B, `view.jump_to_area`), the area
## filter of the timeline and `state.areas`.

## Pseudo area id of the whole site.
const SITE_ID: String = "site"


## Bounding rectangle {x, z, w, d} of a cell list (zeros when empty).
static func bounds_of(cells: Array) -> Dictionary:
    if cells.is_empty():
        return {"x": 0, "z": 0, "w": 0, "d": 0}
    var lo := Vector2i(1 << 30, 1 << 30)
    var hi := Vector2i(-(1 << 30), -(1 << 30))
    for c in cells:
        var v: Vector2i = c
        lo = Vector2i(mini(lo.x, v.x), mini(lo.y, v.y))
        hi = Vector2i(maxi(hi.x, v.x), maxi(hi.y, v.y))
    return {"x": lo.x, "z": lo.y, "w": hi.x - lo.x + 1, "d": hi.y - lo.y + 1}


## Rows for `state.areas`: {id, name, storey_ids, zone_ids, cell_count, bounds, zones, tasks, tasks_done, active,
## done_share (crew-day weighted), crews[, cells]}. `with_cells` adds the cell list.
static func list(gs: SimState, with_cells: bool = false) -> Array[Dictionary]:
    var out: Array[Dictionary] = []
    if gs == null or gs.bundle == null:
        return out
    for a in gs.bundle.areas:
        out.append(summary(gs, a, with_cells))
    return out


static func summary(gs: SimState, area: Dictionary, with_cells: bool = false) -> Dictionary:
    var st: TaskStore = gs.runtime
    var zone_set: Dictionary = {}
    for z in area["zone_ids"]:
        zone_set[str(z)] = true
    var tasks: int = 0
    var done_n: int = 0
    var active: int = 0
    var est: float = 0.0
    var done: float = 0.0
    for zid in zone_set:
        for t in gs.bundle.tasks_by_zone.get(zid, []):
            var task: TaskData = t
            var i: int = st.idx_of(task.task_id)
            if i < 0:
                continue
            tasks += 1
            var s: int = st.state[i]
            est += task.estimated_crew_days
            if TaskRuntime.is_finished(s):
                done_n += 1
                done += task.estimated_crew_days
            else:
                if s == TaskRuntime.State.ACTIVE or s == TaskRuntime.State.REWORK:
                    active += 1
                done += minf(st.progress[i], task.estimated_crew_days)
    var crews: int = 0
    for c in gs.crews:
        if zone_set.has(str(c["zone_id"])):
            crews += 1
    var cells: Array = area["cells"]
    var row: Dictionary = {
        "id": area["id"], "name": area["name"], "storey_ids": (area["storey_ids"] as Array).duplicate(),
        "zone_ids": (area["zone_ids"] as Array).duplicate(), "zones": zone_set.size(), "cell_count": cells.size(),
        "bounds": bounds_of(cells), "tasks": tasks, "tasks_done": done_n, "active": active,
        "done_share": snappedf(done / est, 0.0001) if est > 0.0 else 0.0, "crews": crews,
    }
    if with_cells:
        var arr: Array = []
        for c in cells:
            arr.append([(c as Vector2i).x, (c as Vector2i).y])
        row["cells"] = arr
    return row


## Storey index the camera focus goes to for an area: its first listed storey, else the storey of most of its zones.
static func focus_storey(gs: SimState, area: Dictionary) -> int:
    for sid in area["storey_ids"]:
        if gs.bundle.storey_index_by_id.has(str(sid)):
            return int(gs.bundle.storey_index_by_id[str(sid)])
    var votes: Dictionary = {}
    for zid in area["zone_ids"]:
        var z: ZoneData = gs.bundle.zones_by_id.get(str(zid), null)
        if z != null:
            votes[z.storey_id] = int(votes.get(z.storey_id, 0)) + 1
    var best: String = ""
    var best_n: int = 0
    for sid in votes:
        if int(votes[sid]) > best_n:
            best_n = int(votes[sid])
            best = str(sid)
    if best != "":
        return int(gs.bundle.storey_index_by_id.get(best, gs.focus_storey_index))
    return gs.focus_storey_index


## The area (or the whole site for "" / "site") as {id, name, cells, storey_index, camera} for the camera jump; {} when unknown.
static func target(gs: SimState, area_id: String) -> Dictionary:
    if area_id == "" or area_id == SITE_ID:
        return {"id": SITE_ID, "name": "Whole site", "cells": [], "storey_index": gs.focus_storey_index, "camera": {}, "site": true}
    var area: Dictionary = gs.bundle.areas_by_id.get(area_id, {})
    if area.is_empty():
        return {}
    return {"id": area_id, "name": area["name"], "cells": area["cells"], "storey_index": focus_storey(gs, area),
            "camera": area["camera"], "site": false}
