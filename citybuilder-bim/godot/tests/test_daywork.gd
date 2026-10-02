extends TC
## Day-resolution work pass: five sub-steps per week, incremental readiness, true day dates.

const RS := TaskRuntime.State


func _crewed_state() -> SimState:
    var gs: SimState = new_state()
    gs.scenario.events.clear()
    gs.inspection_fail_override = 0.0
    build_road(gs)
    ok(gs.place_tile(Vector2i(1, 1), "crane_pad"), "crane pad")
    ok(gs.place_equipment("mc1", Vector2i(1, 1)), "crane")
    gs.place_tile(Vector2i(1, 3), "laydown")
    gs.assign_crew(gs.hire("concrete"), "L00-Z1")
    return gs


func _count(gs: SimState, ids: Array, states: Array) -> int:
    var n: int = 0
    for id in ids:
        if state_of(gs, id) in states:
            n += 1
    return n


func test_tiny_tasks_finish_several_per_day() -> void:
    var gs: SimState = _crewed_state()
    var footings: Array = ["T000001", "T000002", "T000003", "T000004"]
    gs.refresh_states()
    gs.run_work_day(0)
    var done0: int = _count(gs, footings, [RS.AWAITING_INSPECTION, RS.INSPECTED])
    ok(done0 >= 2, "one crew-day finishes several 0.24-crew-day footings (got %d)" % done0)
    gs.run_work_day(1)
    eq(_count(gs, footings, [RS.AWAITING_INSPECTION, RS.INSPECTED]), 4, "all four footings poured by the end of day 1")
    gs.free()


func test_dates_have_day_resolution() -> void:
    var gs: SimState = _crewed_state()
    ok(gs.advance_week(), "advance")
    var seen: Dictionary = {}
    for t in gs.bundle.tasks:
        var rt: TaskRuntime = gs.runtime[t.task_id]
        if rt.actual_start_day >= 0:
            ok(rt.actual_start_day < 5, "%s started within week 0 (day %d)" % [t.task_id, rt.actual_start_day])
            seen[rt.actual_start_day] = true
        if TaskRuntime.is_finished(rt.state):
            ok(rt.actual_finish_day >= rt.actual_start_day, "%s finish >= start" % t.task_id)
            ok(rt.actual_finish_day <= 5, "%s finished within the week" % t.task_id)
    ok(seen.size() >= 2, "starts fall on more than one day of the week: %s" % str(seen.keys()))
    gs.free()


func test_successors_start_mid_week_after_inspection() -> void:
    var gs: SimState = _crewed_state()
    gs.advance_week()
    # footings finish on days 0-1, are inspected two working days later, and the columns
    # (crane in reach, same crew) start before the week is over: no whole week lost per hold point.
    var cols: Array = ["T000005", "T000006", "T000007", "T000008"]
    var started: int = 0
    for id in cols:
        if (gs.runtime[id] as TaskRuntime).actual_start_day >= 0:
            started += 1
            ok((gs.runtime[id] as TaskRuntime).actual_start_day >= 2, "column starts after its footing inspection")
    ok(started >= 1, "at least one column started inside week 0 (got %d)" % started)
    gs.free()


func test_gate_reevaluated_daily() -> void:
    var gs: SimState = new_state()
    var duct: TaskData = gs.bundle.tasks_by_id["T000010"]
    duct.predecessors.clear()
    for id in ["T000001", "T000002", "T000003", "T000004", "T000005", "T000006", "T000007"]:
        set_finished(gs, id)
    gs.refresh_states()  # primes the gate counts
    eq(state_of(gs, "T000010"), RS.BLOCKED, "held by the structural gate")
    gs.week = 1
    gs.day_in_week = 0
    set_finished(gs, "T000008")  # last open task of the gate: queues the tasks it holds
    Readiness.process_dirty(gs, 7)  # mid-week day
    eq(state_of(gs, "T000010"), RS.READY, "gate opens mid-week without a full refresh")
    gs.free()


func test_dirty_queue_follows_state_changes() -> void:
    var gs: SimState = new_state()
    gs.refresh_states()
    ok(gs.dirty_tasks.is_empty(), "refresh clears the queue")
    set_finished(gs, "T000001")
    ok(gs.dirty_tasks.has("T000005"), "finishing a footing queues its column")
    Readiness.process_dirty(gs, 5)
    ok(gs.dirty_tasks.is_empty(), "queue consumed")
    eq(state_of(gs, "T000005"), RS.READY, "column READY after the incremental pass")
    gs.free()


func test_before_work_day_hook() -> void:
    var gs: SimState = new_state()
    gs.scenario.events.clear()
    var days: Array = []
    gs.before_work_day = func(d: int) -> void: days.append(d)
    gs.advance_week()
    eq(days, [0, 1, 2, 3, 4], "hook fires before each of the five working days")
    gs.free()
