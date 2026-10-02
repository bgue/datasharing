extends TC
## Second shift (docs/05 section 4).

const RS := TaskRuntime.State


func _setup(gs: SimState) -> void:
    gs.scenario.events.clear()
    gs.bundle.zones_by_id["L00-Z1"].max_crews = 10
    build_road(gs)
    gs.place_tile(Vector2i(1, 3), "laydown")
    for t in gs.bundle.packages_by_id["P00001"].tasks:
        (gs.runtime[t.task_id] as TaskRuntime).required = 100.0
    gs.assign_crew(gs.hire("concrete"), "L00-Z1")
    gs.refresh_states()


func test_double_shift_productivity() -> void:
    var single: SimState = new_state()
    _setup(single)
    single.run_work_day(0)
    var dbl: SimState = new_state()
    _setup(dbl)
    ok(dbl.set_shift("L00-Z1", "double"), "double shift allowed")
    dbl.run_work_day(0)
    var a: float = (single.runtime["T000001"] as TaskRuntime).progress
    var b: float = (dbl.runtime["T000001"] as TaskRuntime).progress
    near(b / a, 1.8, "crew-days per crew per day x1.8", 0.001)
    ok((dbl.runtime["T000001"] as TaskRuntime).worked_double, "task flagged as worked under double shift")
    ok(not (single.runtime["T000001"] as TaskRuntime).worked_double, "single shift: not flagged")
    single.free()
    dbl.free()


func test_double_shift_cost() -> void:
    var gs: SimState = new_state()
    _setup(gs)
    near(Economy.crew_cost(gs), 9000.0, "single shift weekly cost")
    gs.set_shift("L00-Z1", "double")
    near(Economy.crew_cost(gs), 9000.0 * 2.2, "x2.2 for crews in a double-shift zone")
    gs.set_shift("L00-Z1", "single")
    near(Economy.crew_cost(gs), 9000.0, "back to single")
    var other: int = gs.hire("concrete")
    gs.assign_crew(other, "L01-Z1")
    near(Economy.crew_cost(gs), 18000.0, "crews in other zones pay the normal rate")
    gs.free()


func test_max_zones_cap() -> void:
    var gs: SimState = new_state()
    gs.bundle.zones_by_id["L01-Z1"].tags.clear()
    gs.scenario.shift_max_zones = 1
    ok(gs.set_shift("L00-Z1", "double"), "first zone")
    ok(not gs.set_shift("L01-Z1", "double"), "second refused at max_zones 1")
    ok(gs.last_error.contains("At most 1"), "reason: " + gs.last_error)
    ok(gs.set_shift("L00-Z1", "double"), "re-setting the same zone is fine")
    ok(gs.set_shift("L00-Z1", "single"), "back to single")
    ok(gs.set_shift("L01-Z1", "double"), "slot freed")
    gs.free()


func test_forbidden_tag_and_zone_flag() -> void:
    var gs: SimState = new_state()
    ok(not gs.set_shift("L01-Z1", "double"), "occupied_adjacent zone refuses double shift (quiet hours)")
    ok(gs.last_error.contains("occupied_adjacent"), "reason names the tag: " + gs.last_error)
    gs.bundle.zones_by_id["L00-Z1"].shift_allowed = false
    ok(not gs.set_shift("L00-Z1", "double"), "shift_allowed=false refuses")
    ok(not gs.set_shift("L00-Z1", "triple"), "unknown mode refused")
    ok(not gs.set_shift("nowhere", "double"), "unknown zone refused")
    gs.free()


func test_inspection_fail_chance_add() -> void:
    var gs: SimState = new_state()
    var rt: TaskRuntime = gs.runtime["T000001"]
    near(Inspections.fail_chance(gs, rt), 0.10, "base chance")
    rt.worked_double = true
    near(Inspections.fail_chance(gs, rt), 0.15, "+0.05 for tasks worked under double shift")
    gs.inspection_fail_override = 0.5
    near(Inspections.fail_chance(gs, rt), 0.55, "the add applies on top of an override")
    gs.inspection_fail_override = 0.98
    near(Inspections.fail_chance(gs, rt), 1.0, "clamped at 1")
    gs.free()


func test_risk_multiplier() -> void:
    var gs: SimState = new_state()
    var t: TaskData = gs.bundle.tasks_by_id["T000001"]
    var base: float = Safety.task_risk_term(gs, t)
    gs.set_shift("L00-Z1", "double")
    near(Safety.task_risk_term(gs, t), base * 1.5, "incident risk x1.5 in a double-shift zone")
    gs.free()


func test_double_shift_zone_weeks_in_score_snapshot() -> void:
    var gs: SimState = new_state()
    gs.scenario.events.clear()
    gs.set_shift("L00-Z1", "double")
    gs.advance_week()
    gs.advance_week()
    eq(gs.double_shift_zone_weeks, 2, "one zone, two weeks")
    eq(Scoring.compute(gs)["double_shift_zone_weeks"], 2, "score snapshot records it")
    eq(gs.snapshot()["double_shift_zone_weeks"], 2, "and the state snapshot")
    near(float(Scoring.compute(gs)["components"]["stability"]), 1.0, "stability is not affected")
    gs.free()


func test_shift_saved_and_loaded() -> void:
    var gs: SimState = new_state()
    gs.set_shift("L00-Z1", "double")
    gs.package_runtime["P00002"].released = false
    gs.package_runtime["P00002"].priority = -3
    var data: Dictionary = JSON.parse_string(JSON.stringify(gs.serialize()))
    var gs2: SimState = new_state()
    ok(gs2.deserialize(data), "load")
    ok(gs2.is_double_shift("L00-Z1"), "shift mode restored")
    ok(not gs2.package_runtime["P00002"].released, "package release restored")
    eq(gs2.package_runtime["P00002"].priority, -3, "package priority restored")
    gs.free()
    gs2.free()
