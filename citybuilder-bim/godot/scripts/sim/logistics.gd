class_name Logistics
extends RefCounted
## Tile graph helpers: gate -> haul road connectivity, crane reach, laydown, adjacency.

const DIRS4: Array[Vector2i] = [Vector2i(1, 0), Vector2i(-1, 0), Vector2i(0, 1), Vector2i(0, -1)]


static func tile_at(tiles: Dictionary, cell: Vector2i) -> String:
    if tiles.has(cell):
        var t: Dictionary = tiles[cell]
        return str(t.get("tile", ""))
    return ""


## BFS from the gate cells over road tiles (gate / haul_road / existing_road) and the
## zone's own cells; success when a cell of, or adjacent to, the zone is reached.
static func bfs_access(tiles: Dictionary, gates: Array[Vector2i], zone_cells: Array[Vector2i]) -> bool:
    if zone_cells.is_empty() or gates.is_empty():
        return false
    var zone_set: Dictionary = {}
    for c in zone_cells:
        zone_set[c] = true
    var gate_set: Dictionary = {}
    for g in gates:
        gate_set[g] = true
    var visited: Dictionary = {}
    var queue: Array[Vector2i] = []
    for g in gates:
        visited[g] = true
        queue.append(g)
    var head: int = 0
    while head < queue.size():
        var cur: Vector2i = queue[head]
        head += 1
        if zone_set.has(cur):
            return true
        for d in DIRS4:
            var n: Vector2i = cur + d
            if zone_set.has(n):
                return true
            if visited.has(n):
                continue
            if SiteTiles.is_road(tile_at(tiles, n)) or gate_set.has(n):
                visited[n] = true
                queue.append(n)
    return false


## Cells reachable from the gates over road tiles (for debugging / overlay).
static func reachable_road_cells(tiles: Dictionary, gates: Array[Vector2i]) -> Dictionary:
    var visited: Dictionary = {}
    var queue: Array[Vector2i] = []
    for g in gates:
        visited[g] = true
        queue.append(g)
    var head: int = 0
    while head < queue.size():
        var cur: Vector2i = queue[head]
        head += 1
        for d in DIRS4:
            var n: Vector2i = cur + d
            if not visited.has(n) and SiteTiles.is_road(tile_at(tiles, n)):
                visited[n] = true
                queue.append(n)
    return visited


static func zone_access(gs: SimState, zone_id: String) -> bool:
    return gs.zone_access(zone_id)


## cranes: Array of {cell: Vector2i, reach: float}. True when every cell is within
## reach (cell-centre distance, in cells) of at least one crane.
static func cranes_cover(cranes: Array[Dictionary], cells: Array[Vector2i]) -> bool:
    if cells.is_empty():
        return not cranes.is_empty()
    for c in cells:
        var covered: bool = false
        for cr in cranes:
            var cc: Vector2i = cr["cell"]
            if Vector2(c - cc).length() <= float(cr["reach"]) + 0.0001:
                covered = true
                break
        if not covered:
            return false
    return true


static func crane_list(gs: SimState) -> Array[Dictionary]:
    var out: Array[Dictionary] = []
    for e in gs.equipment_placed:
        var def: EquipmentDef = gs.equipment_def(str(e["id"]))
        if def != null and def.is_crane():
            out.append({"cell": e["cell"], "reach": float(def.reach_cells)})
    return out


static func crane_covers(gs: SimState, cells: Array[Vector2i]) -> bool:
    return cranes_cover(crane_list(gs), cells)


static func laydown_capacity(gs: SimState) -> int:
    var n: int = 0
    for c in gs.tiles:
        if str((gs.tiles[c] as Dictionary).get("tile", "")) == SiteTiles.LAYDOWN:
            n += 1
    return n


static func laydown_used(gs: SimState) -> int:
    var n: int = 0
    for tid in gs.runtime:
        var rt: TaskRuntime = gs.runtime[tid]
        if rt.state == TaskRuntime.State.ACTIVE or rt.state == TaskRuntime.State.REWORK:
            n += (gs.bundle.tasks_by_id[tid] as TaskData).laydown_cells
    return n


## Cells in the 8-neighbourhood ring around the zone (not part of the zone).
static func ring_cells(zone_cells: Array[Vector2i]) -> Array[Vector2i]:
    var zone_set: Dictionary = {}
    for c in zone_cells:
        zone_set[c] = true
    var seen: Dictionary = {}
    var out: Array[Vector2i] = []
    for c in zone_cells:
        for dx in range(-1, 2):
            for dz in range(-1, 2):
                var n := Vector2i(c.x + dx, c.y + dz)
                if zone_set.has(n) or seen.has(n):
                    continue
                seen[n] = true
                out.append(n)
    return out


static func has_tile_adjacent(tiles: Dictionary, zone_cells: Array[Vector2i], tile_id: String) -> bool:
    for n in ring_cells(zone_cells):
        if tile_at(tiles, n) == tile_id:
            return true
    return false
