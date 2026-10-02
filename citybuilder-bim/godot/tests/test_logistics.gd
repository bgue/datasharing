extends TC


func _tiles(cells: Array, tile: String = "haul_road") -> Dictionary:
    var d: Dictionary = {}
    for c in cells:
        d[c] = {"tile": tile, "orientation": 0}
    return d


func test_no_road_means_no_access() -> void:
    var gs: SimState = new_state()
    ok(not gs.zone_access("L00-Z1"), "no road: no access")
    ok(not gs.zone_access("L01-Z1"), "no road: no access upstairs either")
    gs.free()


func test_road_from_gate_gives_access() -> void:
    var gs: SimState = new_state()
    build_road(gs)
    ok(gs.zone_access("L00-Z1"), "road gate -> next to zone: access")
    ok(gs.zone_access("L01-Z1"), "same footprint upstairs: access")
    gs.free()


func test_gap_in_road_blocks_access() -> void:
    var gs: SimState = new_state()
    ok(gs.place_tile(Vector2i(0, 2), "haul_road"), "road 0,2")
    ok(gs.place_tile(Vector2i(0, 3), "haul_road"), "road 0,3")  # detour away from the zone
    ok(not gs.zone_access("L00-Z1"), "road that does not touch the zone: no access")
    ok(gs.place_tile(Vector2i(1, 3), "haul_road"), "road 1,3 (adjacent to zone cell 2,3)")
    ok(gs.zone_access("L00-Z1"), "connected through 0,2 -> 0,3 -> 1,3")
    ok(gs.remove_tile(Vector2i(0, 3)), "demolish a link")
    ok(not gs.zone_access("L00-Z1"), "demolishing the link cuts access")
    gs.free()


func test_bfs_static_rules() -> void:
    var gates: Array[Vector2i] = [Vector2i(0, 0)]
    var zone: Array[Vector2i] = [Vector2i(3, 0)]
    var road: Dictionary = _tiles([Vector2i(1, 0), Vector2i(2, 0)])
    ok(Logistics.bfs_access(road, gates, zone), "straight road reaches zone neighbour")
    var broken: Dictionary = _tiles([Vector2i(1, 0)])
    ok(not Logistics.bfs_access(broken, gates, zone), "gap stops BFS")
    var existing: Dictionary = _tiles([Vector2i(1, 0), Vector2i(2, 0)], "existing_road")
    ok(Logistics.bfs_access(existing, gates, zone), "existing_road is passable")
    var laydown: Dictionary = _tiles([Vector2i(1, 0), Vector2i(2, 0)], "laydown")
    ok(not Logistics.bfs_access(laydown, gates, zone), "laydown is not a road")
    var adjacent_gate: Array[Vector2i] = [Vector2i(2, 0)]
    ok(Logistics.bfs_access({}, adjacent_gate, zone), "gate adjacent to zone suffices")


func test_crane_reach() -> void:
    var gs: SimState = new_state()
    var col_cells: Array[Vector2i] = (gs.bundle.tasks_by_id["T000005"] as TaskData).cells
    ok(not Logistics.crane_covers(gs, col_cells), "no crane placed: not covered")
    ok(not gs.place_equipment("mc1", Vector2i(1, 1)), "equipment needs a crane pad")
    ok(gs.place_tile(Vector2i(1, 1), "crane_pad"), "place crane pad")
    var cash_before: float = gs.cash
    ok(gs.place_equipment("mc1", Vector2i(1, 1)), "place mobile crane on pad: " + gs.last_error)
    near(gs.cash, cash_before - 3000.0, "mobilisation cost charged")
    ok(Logistics.crane_covers(gs, col_cells), "crane at (1,1) reach 3 covers (2,2)")
    var far: Array[Vector2i] = [Vector2i(7, 5)]
    ok(not Logistics.crane_covers(gs, far), "(7,5) is outside reach")
    ok(not gs.place_equipment("mc1", Vector2i(1, 1)), "only one mobile crane (max_count 1)")
    gs.free()


func test_crane_required_blocks_start_not_readiness() -> void:
    var gs: SimState = new_state()
    for id in ["T000001", "T000002", "T000003", "T000004"]:
        set_finished(gs, id)
    build_road(gs)
    gs.refresh_states()
    var rt: TaskRuntime = gs.runtime["T000005"]
    eq(rt.state, TaskRuntime.State.READY, "column READY")
    eq(rt.blocked_reason, "Outside crane reach", "impediment reported")
    eq(Productivity.access_factor(gs, gs.bundle.tasks_by_id["T000005"]), 0.0, "access factor 0 without crane")
    gs.free()


func test_placement_rules() -> void:
    var gs: SimState = new_state()
    ok(not gs.place_tile(Vector2i(2, 2), "haul_road"), "building footprint refused")
    ok(not gs.place_tile(Vector2i(6, 2), "laydown"), "occupied cell refused")
    ok(not gs.place_tile(Vector2i(-1, 0), "laydown"), "outside the site refused")
    ok(not gs.place_tile(Vector2i(0, 2), "gate"), "gate is not player placeable")
    var c0: float = gs.cash
    ok(gs.place_tile(Vector2i(5, 5), "laydown"), "laydown placed")
    near(gs.cash, c0 - 300.0, "laydown placement cost")
    near(Economy.tile_rent(gs), 100.0 + 0.0, "weekly rent of laydown (gate/existing are free)")
    ok(gs.remove_tile(Vector2i(5, 5)), "demolish laydown")
    ok(not gs.remove_tile(Vector2i(6, 2)), "existing building is permanent")
    gs.free()


func test_gate_cell_accepts_road_and_restores_gate() -> void:
    var gs: SimState = new_state()
    eq(gs.tile_at(Vector2i(0, 2)), "gate", "gate tile auto-placed")
    ok(not gs.place_tile(Vector2i(0, 2), "laydown"), "only a road may replace a gate")
    ok(gs.place_tile(Vector2i(0, 2), "haul_road"), "road on gate cell")
    ok(gs.remove_tile(Vector2i(0, 2)), "demolish it")
    eq(gs.tile_at(Vector2i(0, 2)), "gate", "gate restored")
    ok(not gs.remove_tile(Vector2i(0, 2)), "gate itself is permanent")
    gs.free()
