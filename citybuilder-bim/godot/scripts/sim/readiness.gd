class_name Readiness
extends RefCounted
## Computes READY / BLOCKED per task: predecessors (FS/SS/FF + lag), phase gates,
## procurement. Access, crane reach, laydown and zone pauses are *impediments*:
## a READY task with an impediment cannot be started by a crew (see Productivity).


static func pred_label(gs: SimState, task_id: String) -> String:
    var cached: Variant = gs.label_cache.get(task_id, null)
    if cached != null:
        return cached
    var t: TaskData = gs.bundle.tasks_by_id.get(task_id, null)
    var label: String = task_id
    if t != null:
        var st: StepDef = gs.bundle.step_of(t)
        label = "%s (%s)" % [st.name if st != null else t.step_id, t.element_name]
    gs.label_cache[task_id] = label
    return label


## Returns "" when all predecessors are satisfied, else a reason.
## `at_day` < 0 means the start of the current week; Productivity passes a mid-week day so a crew
## can carry on into successors released by a task it just finished.
static func predecessor_block(gs: SimState, task: TaskData, at_day: int = -1) -> String:
    var day: int = gs.week * 5 if at_day < 0 else at_day
    var st: TaskStore = gs.runtime
    var me: int = st.idx_of(task.task_id)
    for p in task.predecessors:
        var pid: String = p["task_id"]
        var pi: int = st.idx_of(pid)
        if pi < 0:
            continue
        var lag: int = int(p["lag_days"])
        var why: String = ""
        match str(p["type"]):
            "SS":
                if st.actual_start_day[pi] < 0:
                    why = "Waiting to start: %s"
                elif day < st.actual_start_day[pi] + lag:
                    why = "Lag after start of %s"
            "FF":
                if st.actual_start_day[pi] < 0:
                    why = "Waiting to start: %s"
            _:
                if not st.is_finished_at(pi):
                    why = "Waiting for %s"
                elif day < st.actual_finish_day[pi] + lag:
                    why = "Lag after %s"
        if why != "":
            if me >= 0:
                st.blocker[me] = pi
            return why % pred_label(gs, pid)
    if me >= 0:
        st.blocker[me] = -1
    return ""


## True if an FF predecessor has not yet finished (task cannot complete).
static func ff_pending(gs: SimState, task: TaskData, at_day: int = -1) -> bool:
    var day: int = gs.week * 5 if at_day < 0 else at_day
    var st: TaskStore = gs.runtime
    for p in task.predecessors:
        if str(p["type"]) != "FF":
            continue
        var pi: int = st.idx_of(p["task_id"])
        if pi < 0:
            continue
        if not st.is_finished_at(pi) or day < st.actual_finish_day[pi] + int(p["lag_days"]):
            return true
    return false


## Cumulative gates (docs/02 section 3.2): within the task's scope instance (zone / storey /
## project) a task whose phase order is >= order(before_phase) is held until every task whose
## phase order is <= order(after_phase) is finished (INSPECTED where the step is inspected).
## A scope instance with no such tasks passes. Returns "" or the reason; `cache` memoises counts.
static func gate_block(gs: SimState, task: TaskData, cache: Dictionary = {}) -> String:
    var skip_frozen: bool = not gs.manual_zones.is_empty()
    var task_order: int = gs.bundle.order_of_phase(task.phase)
    var gates: Array[GateDef] = gs.bundle.gates
    var before: PackedInt32Array = gs.gate_orders(false)
    for gi in gates.size():
        if task_order < before[gi]:
            continue
        var gate: GateDef = gates[gi]
        var scope_key: String = SequenceBundle.gate_scope_key(gate, task)
        # cache: gate index -> {scope instance -> open count}
        var per: Variant = cache.get(gi, null)
        if per == null:
            per = {}
            cache[gi] = per
        var counts: Dictionary = per
        if not counts.has(scope_key):
            var open_count: int = 0
            for t2 in gs.bundle.gate_scope_tasks(gate, task):
                if skip_frozen and gs.is_frozen_task(t2):
                    continue  # generated tasks of a zone in manual mode are suppressed
                var i2: int = gs.runtime.idx_of(t2.task_id)
                if not gs.runtime.is_finished_at(i2):
                    open_count += 1
                elif not gate.requires_inspection_types.is_empty() \
                        and gate.requires_inspection_types.has(t2.inspection_type) \
                        and t2.inspection and gs.runtime.state[i2] != TaskRuntime.State.INSPECTED:
                    open_count += 1
            counts[scope_key] = open_count
        var n: int = int(counts[scope_key])
        if n > 0:
            gs.last_gate_gi = gi
            gs.last_gate_n = n
            return gate_reason(gate, n)
    return ""


static func gate_reason(gate: GateDef, open_count: int) -> String:
    return "Gate '%s': %d task(s) up to %s not complete and inspected" % [gate.name, open_count, gate.after_phase]


static func procurement_block(gs: SimState, task: TaskData) -> String:
    if task.lead_time_weeks <= 0:
        return ""
    var i: int = gs.runtime.idx_of(task.task_id)
    var dw: int = gs.runtime.delivery_week[i]
    if dw < 0:
        return "Long-lead item not ordered (%d wk lead time)" % task.lead_time_weeks
    if gs.week < dw:
        return "Delivery lands in week %d" % dw
    return ""


## Why a task is not ready ("" = ready): manual freeze, predecessors, gates, procurement, in that order.
static func block_reason(gs: SimState, task: TaskData, gate_cache: Dictionary, at_day: int = -1) -> String:
    if gs.is_frozen_task(task):
        return "Zone %s is in manual mode (generated tasks frozen)" % task.zone_id
    var r: String = predecessor_block(gs, task, at_day)
    if r == "":
        r = gate_block(gs, task, gate_cache)
    if r == "":
        r = procurement_block(gs, task)
    return r


## Result: {"ready": bool, "reason": String}
static func evaluate(gs: SimState, task: TaskData, gate_cache: Dictionary = {}, at_day: int = -1) -> Dictionary:
    var r: String = block_reason(gs, task, gate_cache, at_day)
    return {"ready": r == "", "reason": r}


const NO_LAYDOWN_CACHE: int = -1000000


static func laydown_free_cells(gs: SimState) -> int:
    return Logistics.laydown_capacity(gs) - Logistics.laydown_used(gs)


## Reason a READY task cannot be started right now ("" = free to start).
## `laydown_free` may be passed in to avoid recomputing it for every task.
static func impediment(gs: SimState, task: TaskData, laydown_free: int = NO_LAYDOWN_CACHE) -> String:
    if gs.zone_paused_until.get(task.zone_id, 0) > gs.week:
        return "Zone paused until week %d" % int(gs.zone_paused_until[task.zone_id])
    if task.requires_access and not gs.zone_access(task.zone_id):
        return "No haul-road access from a gate"
    if task.requires_crane and not Logistics.task_crane_covered(gs, task):
        return "Outside crane reach"
    if task.laydown_cells > 0:
        var free: int = laydown_free
        if free == NO_LAYDOWN_CACHE:
            free = laydown_free_cells(gs)
        if free < task.laydown_cells:
            return "No free laydown space (%d cells needed)" % task.laydown_cells
    return ""


## Writes the verdict of an evaluation: READY (reason = the impediment) or BLOCKED (reason). BLOCKED tasks whose reason
## is not "Waiting ..." (gate, lag, delivery, manual freeze) go on the watch list that every incremental pass re-checks:
## only a predecessor changing (queued by note_changed) can lift a "Waiting" block.
static func _apply(gs: SimState, i: int, t: TaskData, why: String, free_laydown: int) -> void:
    gs.stat_evals += 1
    var st: TaskStore = gs.runtime
    if why == "":
        st.set_reason(i, impediment(gs, t, free_laydown))
        st.set_state(i, TaskRuntime.State.READY)
        gs.readiness_watch.erase(i)
        gs.readiness_gate_watch.erase(i)
        st.gate_idx[i] = -1
    else:
        st.set_reason(i, why)
        st.set_state(i, TaskRuntime.State.BLOCKED)
        if why.begins_with("Waiting"):
            gs.readiness_watch.erase(i)
            gs.readiness_gate_watch.erase(i)
            st.gate_idx[i] = -1
        elif why.begins_with("Gate"):
            gs.readiness_watch.erase(i)
            gs.readiness_gate_watch[i] = true
            st.gate_idx[i] = gs.last_gate_gi
            st.gate_cnt[i] = gs.last_gate_n
        else:
            gs.readiness_watch[i] = true
            gs.readiness_gate_watch.erase(i)
            st.gate_idx[i] = -1


static func _evaluable(s: int) -> bool:
    return s == TaskRuntime.State.NOT_STARTED or s == TaskRuntime.State.READY or s == TaskRuntime.State.BLOCKED


## Re-evaluates every task that has not started (full pass: start of a level, task graph or manual-mode changes,
## loads). Emits task_state_changed via SimState. The incremental pass below is what the weekly loop runs.
static func refresh(gs: SimState) -> void:
    var cache: Dictionary = {}
    gs.invalidate_gate_orders()
    gs.bundle.invalidate_start_links()
    gs.dirty_tasks.clear()
    gs.gate_cache = cache  # shared with the mid-week continuation (counts only fall within a week)
    gs.readiness_watch.clear()
    gs.readiness_gate_watch.clear()
    var free_laydown: int = laydown_free_cells(gs)
    var st: TaskStore = gs.runtime
    for i in st.slot_count():
        if st.alive[i] == 0 or not _evaluable(st.state[i]):
            continue
        var t: TaskData = st.task_refs[i]
        _apply(gs, i, t, block_reason(gs, t, cache), free_laydown)


## Incremental pass, equivalent to refresh() when nothing changed outside the notified events: re-evaluates the tasks
## queued by note_changed (a predecessor started / finished, a gate opened) and the watch list (time / delivery /
## gate blockers), and re-checks the impediments (access, crane reach, laydown, pauses) of the READY tasks. Cost is
## proportional to what changed, not to the number of tasks.
static func refresh_incremental(gs: SimState, _include_gates: bool = true) -> void:
    var st: TaskStore = gs.runtime
    var cache: Dictionary = gs.gate_cache
    var free_laydown: int = laydown_free_cells(gs)
    var queue: Dictionary = {}
    for sid in gs.dirty_tasks:
        var di: int = st.idx_of(sid)
        if di >= 0:
            queue[di] = true
    gs.dirty_tasks.clear()
    for wi in gs.readiness_watch:
        queue[wi] = true
    # tasks held by a gate (readiness_gate_watch) are released by note_changed when the gate opens; the count in their
    # reason is read live (TaskStore.gate_live), so they need no weekly pass
    var slots: Array = queue.keys()
    slots.sort()
    for i in slots:
        if not _evaluable(st.state[i]):
            gs.readiness_watch.erase(i)
            gs.readiness_gate_watch.erase(i)
            continue
        var t: TaskData = st.task_refs[i]
        _apply(gs, i, t, block_reason(gs, t, cache), free_laydown)
    for ri in st.ready:
        if queue.has(ri):
            continue
        st.set_reason(ri, impediment(gs, st.task_refs[ri], free_laydown))


## Called by SimState when a task starts (SS links) or finishes (FS/FF links, gates): queues the
## tasks whose readiness may have changed. Gate open-counts in gs.gate_cache are decremented and a
## gate that reaches zero queues every task it holds.
static var dbg_nofilter: bool = false


static func note_changed(gs: SimState, task_id: String, new_state: int) -> void:
    var st: TaskStore = gs.runtime
    var me: int = st.idx_of(task_id)
    # a task that only started can release its SS / FF successors; finishing releases every successor
    var targets: Array = gs.bundle.successors_by_task.get(task_id, []) if TaskRuntime.is_finished(new_state) \
            else gs.bundle.start_successors(task_id)
    for sid in targets:
        # a successor held back by a different (earlier) predecessor stays held back: nothing to re-evaluate
        var si: int = st.idx_of(sid)
        if not dbg_nofilter and si >= 0 and st.blocker[si] >= 0 and st.blocker[si] != me and st.state[si] == TaskRuntime.State.BLOCKED:
            continue
        gs.dirty_tasks.append(sid)
    if not TaskRuntime.is_finished(new_state):
        return
    var task: TaskData = gs.bundle.tasks_by_id[task_id]
    var order: int = gs.bundle.order_of_phase(task.phase)
    var gates: Array[GateDef] = gs.bundle.gates
    var after: PackedInt32Array = gs.gate_orders(true)
    for gi in gates.size():
        if order > after[gi]:
            continue
        var per: Variant = gs.gate_cache.get(gi, null)
        if per == null:
            continue
        var gate: GateDef = gates[gi]
        var skey: String = SequenceBundle.gate_scope_key(gate, task)
        var counts: Dictionary = per
        if not counts.has(skey):
            continue
        var n: int = int(counts[skey]) - 1
        counts[skey] = n
        if n <= 0:
            # the tasks this gate was holding are on the watch list (BLOCKED for a reason other than "Waiting ...")
            for held in gs.bundle.gate_held_tasks(gate, task):
                var hi: int = st.idx_of(held.task_id)
                if hi >= 0 and (dbg_nofilter or gs.readiness_gate_watch.has(hi) or gs.readiness_watch.has(hi)):
                    gs.dirty_tasks.append(held.task_id)


## Re-evaluates the queued tasks at `day` (incremental readiness for the daily work pass).
static func process_dirty(gs: SimState, day: int) -> void:
    if gs.dirty_tasks.is_empty():
        return
    var ids: Array[String] = gs.dirty_tasks
    gs.dirty_tasks = []
    var seen: Dictionary = {}
    var free_laydown: int = laydown_free_cells(gs)
    var st: TaskStore = gs.runtime
    for sid in ids:
        if seen.has(sid):
            continue
        seen[sid] = true
        var i: int = st.idx_of(sid)
        if i < 0 or not _evaluable(st.state[i]):
            continue
        var t: TaskData = st.task_refs[i]
        _apply(gs, i, t, block_reason(gs, t, gs.gate_cache, day), free_laydown)
