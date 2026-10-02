extends TC
## Work faces (docs/05 section 2): per-face caps, exclusive floor, overlay data.


func _zone(gs: SimState) -> ZoneData:
    return gs.bundle.zones_by_id["L00-Z1"]


func test_face_cap_factors() -> void:
    var gs: SimState = new_state()
    var z: ZoneData = _zone(gs)
    z.faces = {"walls": 2}
    near(Packages.face_factor(gs, z, "walls", {"walls": 2}), 1.0, "at cap")
    near(Packages.face_factor(gs, z, "walls", {"walls": 3}), 0.6, "+1 over cap")
    near(Packages.face_factor(gs, z, "walls", {"walls": 4}), 0.35, "+2 over cap")
    near(Packages.face_factor(gs, z, "walls", {"walls": 9}), 0.35, "+7 stays at 0.35")
    near(Packages.face_factor(gs, z, "structure", {"structure": 9}), 1.0, "a face without cap is uncapped")
    near(Packages.face_factor(gs, z, "any", {"any": 9}), 1.0, "any never counts toward a cap")
    gs.free()


func test_exclusive_floor_pushes_other_faces_to_035() -> void:
    var gs: SimState = new_state()
    var z: ZoneData = _zone(gs)
    eq(gs.bundle.exclusive_faces, ["floor"], "default exclusive faces")
    near(Packages.face_factor(gs, z, "walls", {"floor": 1, "walls": 1}), 0.35, "floor active: walls at 0.35")
    near(Packages.face_factor(gs, z, "ceiling_void", {"floor": 2}), 0.35, "any other face too")
    near(Packages.face_factor(gs, z, "floor", {"floor": 1}), 1.0, "the exclusive face itself is not excluded")
    near(Packages.face_factor(gs, z, "any", {"floor": 1}), 1.0, "any is never excluded")
    near(Packages.face_factor(gs, z, "walls", {"walls": 1}), 1.0, "no exclusive face active: normal")
    gs.bundle.exclusive_faces = []
    near(Packages.face_factor(gs, z, "walls", {"floor": 1, "walls": 1}), 1.0, "no exclusive faces configured")
    gs.free()


func _day_progress(gs: SimState, task_id: String) -> float:
    return (gs.runtime[task_id] as TaskRuntime).progress


func _setup_two_crews(gs: SimState) -> void:
    gs.scenario.events.clear()
    gs.bundle.zones_by_id["L00-Z1"].max_crews = 10
    build_road(gs)
    gs.place_tile(Vector2i(1, 3), "laydown")
    var p: PackageData = gs.bundle.packages_by_id["P00001"]
    p.crew_min = 1
    p.crew_ideal = 3
    p.crew_max = 3
    p.work_face = "below_ground"
    for t in p.tasks:
        (gs.runtime[t.task_id] as TaskRuntime).required = 100.0
    gs.scenario.crews_available["concrete"] = 5
    gs.bundle.trades_by_id["concrete"].max_hire_per_week = 5
    gs.assign_crew(gs.hire("concrete"), "L00-Z1")
    gs.assign_crew(gs.hire("concrete"), "L00-Z1")
    gs.refresh_states()


func test_per_face_cap_slows_the_face() -> void:
    var free: SimState = new_state()
    _setup_two_crews(free)
    free.run_work_day(0)
    var capped: SimState = new_state()
    _setup_two_crews(capped)
    capped.bundle.zones_by_id["L00-Z1"].faces = {"below_ground": 1}
    capped.run_work_day(0)
    near(_day_progress(capped, "T000001"), _day_progress(free, "T000001") * 0.6, "two crews on a face capped at 1: x0.6", 0.001)
    var fs: Dictionary = capped.zone_face_state["L00-Z1"]["below_ground"]
    eq(fs["crews"], 2, "overlay data: crews on the face")
    eq(fs["cap"], 1, "overlay data: cap")
    ok(fs["over"], "overlay data: over cap flagged")
    ok(not fs["excluded"], "not excluded")
    free.free()
    capped.free()


func test_exclusive_floor_in_a_running_zone() -> void:
    var gs: SimState = new_state()
    gs.scenario.events.clear()
    gs.bundle.zones_by_id["L00-Z1"].max_crews = 10
    build_road(gs)
    gs.place_tile(Vector2i(1, 3), "laydown")
    # concrete package on the floor, mechanical duct package on the ceiling void
    gs.bundle.packages_by_id["P00001"].work_face = "floor"
    gs.bundle.packages_by_id["P00003"].work_face = "ceiling_void"
    gs.bundle.tasks_by_id["T000010"].predecessors.clear()
    gs.bundle.gates.clear()
    gs.bundle.tasks_by_id["T000010"].requires_crane = false
    (gs.runtime["T000010"] as TaskRuntime).required = 100.0
    for t in gs.bundle.packages_by_id["P00001"].tasks:
        (gs.runtime[t.task_id] as TaskRuntime).required = 100.0
    gs.scenario.crews_available["mechanical"] = 2
    gs.assign_crew(gs.hire("concrete"), "L00-Z1")
    gs.assign_crew(gs.hire("mechanical"), "L00-Z1")
    gs.refresh_states()
    gs.run_work_day(0)
    var fs: Dictionary = gs.zone_face_state["L00-Z1"]
    ok(fs["ceiling_void"]["excluded"], "ceiling void flagged excluded while the floor is being worked")
    ok(not fs["floor"]["excluded"], "the floor is not excluded")
    # duct: weather-insensitive, first repetition learning 0.8, face factor 0.35
    near(_day_progress(gs, "T000010"), 0.8 * 0.35, "excluded face works at 0.35", 0.001)
    gs.free()


func test_any_face_is_never_counted() -> void:
    var gs: SimState = new_state()
    _setup_two_crews(gs)
    gs.bundle.packages_by_id["P00001"].work_face = "any"
    gs.bundle.zones_by_id["L00-Z1"].faces = {"below_ground": 0}
    gs.run_work_day(0)
    ok(not gs.zone_face_state.has("L00-Z1"), "no face state for 'any'")
    ok(_day_progress(gs, "T000001") > 0.0, "work proceeds")
    gs.free()


func test_zone_view_exposes_faces() -> void:
    var gs: SimState = new_state()
    _setup_two_crews(gs)
    gs.bundle.zones_by_id["L00-Z1"].faces = {"below_ground": 1}
    gs.run_work_day(0)
    var v: Dictionary = ApiViews.zone_view(gs, gs.bundle.zones_by_id["L00-Z1"])
    eq(v["faces"], {"below_ground": 1}, "configured caps")
    eq(v["face_state"]["below_ground"]["crews"], 2, "face state")
    gs.free()
