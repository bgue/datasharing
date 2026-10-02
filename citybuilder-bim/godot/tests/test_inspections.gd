extends TC

const RS := TaskRuntime.State


## Runs the footings to AWAITING_INSPECTION with a road + one concrete crew.
func _footings_awaiting() -> SimState:
    var gs: SimState = new_state()
    gs.scenario.events.clear()
    build_road(gs)
    var id: int = gs.hire("concrete")
    gs.assign_crew(id, "L00-Z1")
    gs.advance_week()
    return gs


func test_one_week_wait_then_pass() -> void:
    var gs: SimState = _footings_awaiting()
    eq(state_of(gs, "T000001"), RS.AWAITING_INSPECTION, "footing awaits inspection after work")
    eq((gs.runtime["T000001"] as TaskRuntime).inspection_due_week, 1, "due the following week")
    gs.inspection_fail_override = 0.0
    gs.advance_week()
    eq(state_of(gs, "T000001"), RS.INSPECTED, "passes after one week")
    ok((gs.runtime["T000001"] as TaskRuntime).actual_finish_day >= 5, "finish day recorded at inspection")
    gs.free()


func test_failed_inspection_creates_rework() -> void:
    var gs: SimState = _footings_awaiting()
    gs.inspection_fail_override = 1.0  # force failure
    var rt: TaskRuntime = gs.runtime["T000001"]
    var t: TaskData = gs.bundle.tasks_by_id["T000001"]
    gs.advance_week()
    # Inspection fails in the same pass -> REWORK; the crew then reworks 25% of estimated crew days.
    ok(rt.inspection_failures >= 1, "failure counted")
    near(rt.rework_days_added, 0.25 * t.estimated_crew_days, "rework adds 25% of estimated crew days")
    ok(gs.inspection_failures_total >= 1, "stat tracked")
    ok(state_of(gs, "T000001") in [RS.REWORK, RS.AWAITING_INSPECTION], "task sent back for rework")
    gs.inspection_fail_override = 0.0
    for i in 3:
        gs.advance_week()
    eq(state_of(gs, "T000001"), RS.INSPECTED, "eventually passes after rework")
    gs.free()


func test_rework_state_and_progress_reduction() -> void:
    var gs: SimState = new_state()
    var t: TaskData = gs.bundle.tasks_by_id["T000009"]
    var rt: TaskRuntime = gs.runtime["T000009"]
    rt.progress = rt.required
    gs.set_task_state("T000009", RS.AWAITING_INSPECTION)
    Inspections.fail(gs, t, rt)
    eq(rt.state, RS.REWORK, "state REWORK")
    near(rt.progress, rt.required - 0.25 * t.estimated_crew_days, "progress reduced by 25% of estimated crew days")
    near(Inspections.fail_chance(gs), 0.10, "10% base fail chance")
    gs.free()


func test_seed_is_deterministic() -> void:
    var a: SimState = new_state()
    var b: SimState = new_state()
    var ra: Array[float] = []
    var rb: Array[float] = []
    for i in 20:
        ra.append(a.rng.randf())
        rb.append(b.rng.randf())
    eq(ra, rb, "same scenario id -> same RNG sequence")
    var fails: int = 0
    for i in 2000:
        if a.rng.randf() < Inspections.fail_chance(a):
            fails += 1
    ok(fails > 120 and fails < 280, "about 10%% of 2000 rolls fail (got %d)" % fails)
    a.free()
    b.free()
