extends TC

const RS := TaskRuntime.State


func test_initial_states() -> void:
    var gs: SimState = new_state()
    for id in ["T000001", "T000002", "T000003", "T000004"]:
        eq(state_of(gs, id), RS.READY, "footing %s READY at start" % id)
    for id in ["T000005", "T000006", "T000007", "T000008"]:
        eq(state_of(gs, id), RS.BLOCKED, "column %s BLOCKED at start" % id)
    for id in ["T000009", "T000010", "T000011", "T000012"]:
        eq(state_of(gs, id), RS.BLOCKED, "%s BLOCKED at start" % id)
    var rt: TaskRuntime = gs.runtime["T000005"]
    ok(rt.blocked_reason.begins_with("Waiting for"), "column reason mentions footing: " + rt.blocked_reason)
    gs.free()


func test_slab_waits_for_all_columns() -> void:
    var gs: SimState = new_state()
    for id in ["T000001", "T000002", "T000003", "T000004"]:
        set_finished(gs, id)
    gs.refresh_states()
    for id in ["T000005", "T000006", "T000007", "T000008"]:
        eq(state_of(gs, id), RS.READY, "%s READY once footing inspected" % id)
    eq(state_of(gs, "T000009"), RS.BLOCKED, "slab blocked while columns open")
    for id in ["T000005", "T000006", "T000007"]:
        set_finished(gs, id)
    gs.refresh_states()
    eq(state_of(gs, "T000009"), RS.BLOCKED, "slab blocked until ALL columns done")
    set_finished(gs, "T000008")
    gs.refresh_states()
    eq(state_of(gs, "T000009"), RS.READY, "slab READY after four columns done")
    eq(state_of(gs, "T000010"), RS.BLOCKED, "duct blocked until slab finished")
    gs.free()


func test_inspection_task_not_finished_until_inspected() -> void:
    var gs: SimState = new_state()
    var rt: TaskRuntime = gs.runtime["T000001"]
    gs.set_task_state("T000001", RS.AWAITING_INSPECTION)
    rt.actual_finish_day = -1
    gs.refresh_states()
    eq(state_of(gs, "T000005"), RS.BLOCKED, "column still blocked while footing awaits inspection")
    gs.free()


func test_fs_lag_honoured() -> void:
    var gs: SimState = new_state()
    var col: TaskData = gs.bundle.tasks_by_id["T000005"]
    col.predecessors[0]["lag_days"] = 10
    set_finished(gs, "T000001")
    gs.refresh_states()
    eq(state_of(gs, "T000005"), RS.BLOCKED, "blocked inside lag (day 0 < 0 + 10)")
    ok(String(gs.runtime["T000005"].blocked_reason).begins_with("Lag"), "reason is lag")
    gs.week = 1
    gs.refresh_states()
    eq(state_of(gs, "T000005"), RS.BLOCKED, "still blocked at day 5")
    gs.week = 2
    gs.refresh_states()
    eq(state_of(gs, "T000005"), RS.READY, "ready at day 10")
    gs.free()


func test_ss_and_ff_links() -> void:
    var gs: SimState = new_state()
    var col: TaskData = gs.bundle.tasks_by_id["T000005"]
    col.predecessors[0]["type"] = "SS"
    col.predecessors[0]["lag_days"] = 5
    gs.refresh_states()
    eq(state_of(gs, "T000005"), RS.BLOCKED, "SS: footing not started")
    var f: TaskRuntime = gs.runtime["T000001"]
    f.actual_start_day = 0
    gs.set_task_state("T000001", RS.ACTIVE)
    gs.refresh_states()
    eq(state_of(gs, "T000005"), RS.BLOCKED, "SS: lag 5 days not elapsed")
    gs.week = 1
    gs.refresh_states()
    eq(state_of(gs, "T000005"), RS.READY, "SS+5 satisfied at day 5")
    # FF: may start once the predecessor started, cannot finish before it does.
    col.predecessors[0]["type"] = "FF"
    col.predecessors[0]["lag_days"] = 0
    ok(Readiness.ff_pending(gs, col), "FF pending while footing unfinished")
    set_finished(gs, "T000001")
    ok(not Readiness.ff_pending(gs, col), "FF released after footing finished")
    gs.free()


func test_procurement_blocks_until_delivered() -> void:
    var gs: SimState = new_state()
    var t: TaskData = gs.bundle.tasks_by_id["T000001"]
    t.lead_time_weeks = 3
    gs.refresh_states()
    eq(state_of(gs, "T000001"), RS.BLOCKED, "unordered long-lead task blocked")
    ok(gs.order("T000001"), "order succeeds")
    eq((gs.runtime["T000001"] as TaskRuntime).delivery_week, 3, "delivery week = week + lead")
    eq(state_of(gs, "T000001"), RS.BLOCKED, "blocked until delivered")
    gs.week = 3
    gs.refresh_states()
    eq(state_of(gs, "T000001"), RS.READY, "ready once delivered")
    ok(not gs.order("T000001"), "cannot order twice")
    gs.free()


func test_late_order_warning() -> void:
    var gs: SimState = new_state()
    var t: TaskData = gs.bundle.tasks_by_id["T000009"]
    t.lead_time_weeks = 4  # planned start day 4 -> week 0.8
    ok(gs.order_is_late("T000009"), "late if planned start < order week + lead")
    t.planned_start_day = 100
    ok(not gs.order_is_late("T000009"), "not late with slack")
    gs.free()
