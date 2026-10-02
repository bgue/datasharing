extends TC

const RS := TaskRuntime.State


## Runs the footings to the end of week 0 with a road + one concrete crew. With the day-resolution
## work pass they finish on day 0 (work_done_day 1) and their inspection falls due on day 3.
func _footings_week0(fail_chance: float) -> SimState:
    var gs: SimState = new_state()
    gs.scenario.events.clear()
    gs.inspection_fail_override = fail_chance
    build_road(gs)
    var id: int = gs.hire("concrete")
    gs.assign_crew(id, "L00-Z1")
    return gs


func test_two_working_day_wait_then_pass() -> void:
    var gs: SimState = _footings_week0(0.0)
    gs.refresh_states()
    gs.run_work_day(0)
    var rt: TaskRuntime = gs.runtime["T000001"]
    eq(rt.state, RS.AWAITING_INSPECTION, "footing awaits inspection after its work day")
    eq(rt.work_done_day, 1, "work done at the end of day 0")
    eq(rt.inspection_due_day, rt.work_done_day + 2, "inspection due two working days after the work")
    gs.run_work_day(1)
    eq(state_of(gs, "T000001"), RS.AWAITING_INSPECTION, "still waiting at the end of day 1")
    gs.run_work_day(2)
    eq(state_of(gs, "T000001"), RS.INSPECTED, "passes at the end of day 2 (no full week lost)")
    eq(rt.actual_finish_day, 3, "finish day recorded at true day resolution")
    ok(rt.actual_start_day >= 0 and rt.actual_start_day <= 1, "start day is a day of week 0")
    gs.free()


func test_failed_inspection_creates_rework() -> void:
    var gs: SimState = _footings_week0(1.0)  # force failure
    var rt: TaskRuntime = gs.runtime["T000001"]
    var t: TaskData = gs.bundle.tasks_by_id["T000001"]
    gs.advance_week()
    ok(rt.inspection_failures >= 1, "failure counted")
    near(rt.rework_days_added / float(rt.inspection_failures), 0.25 * t.estimated_crew_days, "each failure adds 25% of estimated crew days")
    ok(gs.inspection_failures_total >= 1, "stat tracked")
    ok(state_of(gs, "T000001") in [RS.REWORK, RS.AWAITING_INSPECTION], "task sent back for rework")
    gs.inspection_fail_override = 0.0
    for i in 2:
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
