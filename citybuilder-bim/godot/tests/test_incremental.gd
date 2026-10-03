extends TC
## The incremental readiness / package passes of the weekly loop must give the same game as full passes, and the
## TaskStore (PackedArray task runtime) must behave like the per-task objects it replaced.

const HEALTHCARE: String = "res://scenarios/healthcare_standard/sequence.json"
const RS := TaskRuntime.State


func _digest(gs: SimState) -> String:
    var parts: PackedStringArray = PackedStringArray()
    parts.append("w%d cash %.2f spent %.2f fin %d" % [gs.week, gs.cash, gs.spent_total, gs.finished_task_count()])
    var st: TaskStore = gs.runtime
    var sum: float = 0.0
    var states: PackedStringArray = PackedStringArray()
    for i in st.slot_count():
        states.append(str(st.state[i]))
        sum += st.progress[i]
    parts.append("".join(states))
    parts.append("%.4f" % sum)
    return "|".join(parts)


func _play(full: bool, weeks: int, path: String) -> Array[String]:
    var b := SequenceBundle.load_from_path(path)
    var gs := SimState.new()
    gs.start(b)
    gs.debug_full_refresh = full
    gs.cash += 1.0e9
    Planner.auto_layout(gs, 4)
    # the planner works on the first day of each week only: mid-week tile / crane changes re-run readiness at the start of
    # the week (a quirk of the full pass), which the incremental pass deliberately does not copy
    gs.before_work_day = func(d: int) -> void:
        if d == 0:
            Planner.daily_plan(gs, "ideal", 1.0, true, true)
    var out: Array[String] = []
    for w in weeks:
        if not gs.pending_event.is_empty():
            gs.resolve_event(0)
        Planner.order_all_due(gs, 60)
        gs.advance_week()
        out.append(_digest(gs))
    gs.free()
    return out


func test_incremental_weeks_match_full_refresh_weeks() -> void:
    var a: Array[String] = _play(false, 18, HEALTHCARE)
    var b: Array[String] = _play(true, 18, HEALTHCARE)
    for w in a.size():
        eq(a[w], b[w], "week %d: same states, progress and cash with incremental and full readiness" % w)


func test_incremental_matches_on_the_industrial_bundle_too() -> void:
    var path: String = "res://scenarios/industrial_standard/sequence.json"
    var a: Array[String] = _play(false, 12, path)
    var b: Array[String] = _play(true, 12, path)
    for w in a.size():
        eq(a[w], b[w], "industrial week %d" % w)


func test_store_views_share_state_and_keep_identity() -> void:
    var gs: SimState = new_state()
    var rt: TaskRuntime = gs.runtime["T000001"]
    ok(gs.runtime["T000001"] == rt, "the same view object every time")
    rt.progress = 0.1
    near(gs.runtime.progress[rt.idx], 0.1, "the view writes the PackedArray")
    gs.runtime.progress[rt.idx] = 0.2
    near(rt.progress, 0.2, "and reads it back")
    ok(gs.runtime.has("T000001") and not gs.runtime.has("NOPE"), "has()")
    ok(gs.runtime.get_rt("NOPE") == null, "get_rt of an unknown id is null")
    var n: int = 0
    for id in gs.runtime:
        n += 1
        ok(gs.bundle.tasks_by_id.has(id), "iteration yields task ids")
    eq(n, gs.runtime.size(), "for-in visits every task once")
    gs.free()


func test_store_counters_follow_state_changes() -> void:
    var gs: SimState = new_state()
    var st: TaskStore = gs.runtime
    var total: int = st.size()
    var s0: Dictionary = gs.state_counts()
    eq(int(s0["READY"]) + int(s0["BLOCKED"]), total, "everything ready or blocked at the start")
    set_finished(gs, "T000001")
    eq(st.finished_count(), 1, "one finished")
    ok(st.unpaid.has(st.idx_of("T000001")), "finished and unpaid")
    (gs.runtime["T000001"] as TaskRuntime).paid = true
    ok(not st.unpaid.has(st.idx_of("T000001")), "paid tasks leave the unpaid set")
    gs.set_task_state("T000002", RS.ACTIVE)
    ok(st.active.has(st.idx_of("T000002")), "active set")
    gs.set_task_state("T000002", RS.AWAITING_INSPECTION)
    ok(not st.active.has(st.idx_of("T000002")) and st.awaiting.has(st.idx_of("T000002")), "moves to the awaiting set")
    var counts: Dictionary = gs.state_counts()
    var sum: int = 0
    for k in counts:
        sum += int(counts[k])
    eq(sum, total, "state counts always add up")
    eq(gs.finished_task_count(), 1, "finished count is O(1) and right")
    gs.free()


func test_direct_state_writes_notify_like_set_task_state() -> void:
    var gs: SimState = new_state()
    var seen: Array[String] = []
    gs.task_state_changed.connect(func(id: String, _o: int, _n: int) -> void: seen.append(id))
    (gs.runtime["T000001"] as TaskRuntime).state = RS.ACTIVE
    ok(seen.has("T000001"), "assigning rt.state emits task_state_changed")
    ok(not gs.dirty_tasks.has("T000005"), "starting a task releases nobody through an FS link")
    (gs.runtime["T000001"] as TaskRuntime).state = RS.DONE
    ok(gs.dirty_tasks.has("T000005"), "finishing it queues the successors for the next readiness pass")
    gs.free()


func test_change_log_reports_touched_tasks_once_per_cursor() -> void:
    var gs: SimState = new_state()
    var st: TaskStore = gs.runtime
    var cur: int = st.log_end()
    var i: int = st.idx_of("T000001")
    st.set_progress(i, 0.05)
    st.set_progress(i, 0.06)
    var changed: Variant = st.changed_set_since(cur)
    ok(changed is Dictionary and (changed as Dictionary).has(i), "the touched slot is reported")
    eq((changed as Dictionary).size(), 1, "once, however often it was written")
    ok(st.changed_set_since(st.log_end()).is_empty(), "nothing after the cursor moved on")
    gs.free()


func test_removed_tasks_leave_the_store() -> void:
    var gs: SimState = fixture_state()
    var id: String = ""
    var t := TaskData.new()
    t.task_id = "TX00001"
    t.zone_id = "L00-Z1"
    t.storey_id = "L00"
    t.step_id = "GEN-SURVEY-ASBUILT"
    t.phase = "substructure"
    t.trade = "finishes"
    t.estimated_crew_days = 1.0
    ok(gs.register_task(t), "task registered: %s" % gs.last_error)
    id = t.task_id
    ok(gs.runtime.has(id), "in the store")
    var n: int = gs.runtime.size()
    gs.unregister_task(t)
    ok(not gs.runtime.has(id), "gone from the store")
    eq(gs.runtime.size(), n - 1, "live count follows")
    var visited: int = 0
    for k in gs.runtime:
        visited += 1
    eq(visited, gs.runtime.size(), "iteration skips the dead slot")
    gs.free()
