class_name Readiness
extends RefCounted
## Computes READY / BLOCKED per task: predecessors (FS/SS/FF + lag), phase gates,
## procurement. Access, crane reach, laydown and zone pauses are *impediments*:
## a READY task with an impediment cannot be started by a crew (see Productivity).


static func pred_label(gs: SimState, task_id: String) -> String:
    var t: TaskData = gs.bundle.tasks_by_id.get(task_id, null)
    if t == null:
        return task_id
    var st: StepDef = gs.bundle.step_of(t)
    return "%s (%s)" % [st.name if st != null else t.step_id, t.element_name]


## Returns "" when all predecessors are satisfied, else a reason.
## `at_day` < 0 means the start of the current week; Productivity passes a mid-week day so a crew
## can carry on into successors released by a task it just finished.
static func predecessor_block(gs: SimState, task: TaskData, at_day: int = -1) -> String:
    var day: int = gs.week * 5 if at_day < 0 else at_day
    for p in task.predecessors:
        var pid: String = p["task_id"]
        var prt: TaskRuntime = gs.runtime.get(pid, null)
        if prt == null:
            continue
        var lag: int = int(p["lag_days"])
        match str(p["type"]):
            "SS":
                if prt.actual_start_day < 0:
                    return "Waiting to start: %s" % pred_label(gs, pid)
                if day < prt.actual_start_day + lag:
                    return "Lag after start of %s" % pred_label(gs, pid)
            "FF":
                if prt.actual_start_day < 0:
                    return "Waiting to start: %s" % pred_label(gs, pid)
            _:
                if not TaskRuntime.is_finished(prt.state):
                    return "Waiting for %s" % pred_label(gs, pid)
                if day < prt.actual_finish_day + lag:
                    return "Lag after %s" % pred_label(gs, pid)
    return ""


## True if an FF predecessor has not yet finished (task cannot complete).
static func ff_pending(gs: SimState, task: TaskData, at_day: int = -1) -> bool:
    var day: int = gs.week * 5 if at_day < 0 else at_day
    for p in task.predecessors:
        if str(p["type"]) != "FF":
            continue
        var prt: TaskRuntime = gs.runtime.get(p["task_id"], null)
        if prt == null:
            continue
        if not TaskRuntime.is_finished(prt.state) or day < prt.actual_finish_day + int(p["lag_days"]):
            return true
    return false


## Cumulative gates (docs/02 section 3.2): within the task's scope instance (zone / storey /
## project) a task whose phase order is >= order(before_phase) is held until every task whose
## phase order is <= order(after_phase) is finished (INSPECTED where the step is inspected).
## A scope instance with no such tasks passes. Returns "" or the reason; `cache` memoises counts.
static func gate_block(gs: SimState, task: TaskData, cache: Dictionary = {}) -> String:
    var task_order: int = gs.bundle.order_of_phase(task.phase)
    for gate in gs.bundle.gates:
        if task_order < gs.bundle.order_of_phase(gate.before_phase):
            continue
        var key: String = "%s|%s" % [gate.id, SequenceBundle.gate_scope_key(gate, task)]
        if not cache.has(key):
            var open_count: int = 0
            for t2 in gs.bundle.gate_scope_tasks(gate, task):
                var rt2: TaskRuntime = gs.runtime[t2.task_id]
                if not TaskRuntime.is_finished(rt2.state):
                    open_count += 1
                elif not gate.requires_inspection_types.is_empty() \
                        and gate.requires_inspection_types.has(t2.inspection_type) \
                        and t2.inspection and rt2.state != TaskRuntime.State.INSPECTED:
                    open_count += 1
            cache[key] = open_count
        var n: int = int(cache[key])
        if n > 0:
            return "Gate '%s': %d task(s) up to %s not complete and inspected" % [gate.name, n, gate.after_phase]
    return ""


static func procurement_block(gs: SimState, task: TaskData) -> String:
    if task.lead_time_weeks <= 0:
        return ""
    var rt: TaskRuntime = gs.runtime[task.task_id]
    if rt.delivery_week < 0:
        return "Long-lead item not ordered (%d wk lead time)" % task.lead_time_weeks
    if gs.week < rt.delivery_week:
        return "Delivery lands in week %d" % rt.delivery_week
    return ""


## Result: {"ready": bool, "reason": String}
static func evaluate(gs: SimState, task: TaskData, gate_cache: Dictionary = {}, at_day: int = -1) -> Dictionary:
    var r: String = predecessor_block(gs, task, at_day)
    if r == "":
        r = gate_block(gs, task, gate_cache)
    if r == "":
        r = procurement_block(gs, task)
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
    if task.requires_crane and not Logistics.crane_covers(gs, task.cells):
        return "Outside crane reach"
    if task.laydown_cells > 0:
        var free: int = laydown_free
        if free == NO_LAYDOWN_CACHE:
            free = laydown_free_cells(gs)
        if free < task.laydown_cells:
            return "No free laydown space (%d cells needed)" % task.laydown_cells
    return ""


## Re-evaluates every task that has not started. Emits task_state_changed via SimState.
static func refresh(gs: SimState) -> void:
    var cache: Dictionary = {}
    gs.dirty_tasks.clear()
    gs.gate_cache = cache  # shared with the mid-week continuation (counts only fall within a week)
    var free_laydown: int = laydown_free_cells(gs)
    for t in gs.bundle.tasks:
        var rt: TaskRuntime = gs.runtime[t.task_id]
        if rt.state != TaskRuntime.State.NOT_STARTED and rt.state != TaskRuntime.State.READY \
                and rt.state != TaskRuntime.State.BLOCKED:
            continue
        var ev: Dictionary = evaluate(gs, t, cache)
        if ev["ready"]:
            rt.blocked_reason = impediment(gs, t, free_laydown)
            gs.set_task_state(t.task_id, TaskRuntime.State.READY)
        else:
            rt.blocked_reason = str(ev["reason"])
            gs.set_task_state(t.task_id, TaskRuntime.State.BLOCKED)


## Called by SimState when a task starts (SS links) or finishes (FS/FF links, gates): queues the
## tasks whose readiness may have changed. Gate open-counts in gs.gate_cache are decremented and a
## gate that reaches zero queues every task it holds.
static func note_changed(gs: SimState, task_id: String, new_state: int) -> void:
    for sid in gs.bundle.successors_by_task.get(task_id, []):
        gs.dirty_tasks.append(sid)
    if not TaskRuntime.is_finished(new_state):
        return
    var task: TaskData = gs.bundle.tasks_by_id[task_id]
    var order: int = gs.bundle.order_of_phase(task.phase)
    for gate in gs.bundle.gates:
        if order > gs.bundle.order_of_phase(gate.after_phase):
            continue
        var key: String = "%s|%s" % [gate.id, SequenceBundle.gate_scope_key(gate, task)]
        if not gs.gate_cache.has(key):
            continue
        var n: int = int(gs.gate_cache[key]) - 1
        gs.gate_cache[key] = n
        if n <= 0:
            for held in gs.bundle.gate_held_tasks(gate, task):
                gs.dirty_tasks.append(held.task_id)


## Re-evaluates the queued tasks at `day` (incremental readiness for the daily work pass).
static func process_dirty(gs: SimState, day: int) -> void:
    if gs.dirty_tasks.is_empty():
        return
    var ids: Array[String] = gs.dirty_tasks
    gs.dirty_tasks = []
    var seen: Dictionary = {}
    var free_laydown: int = laydown_free_cells(gs)
    for sid in ids:
        if seen.has(sid):
            continue
        seen[sid] = true
        var rt: TaskRuntime = gs.runtime[sid]
        if rt.state != TaskRuntime.State.NOT_STARTED and rt.state != TaskRuntime.State.BLOCKED \
                and rt.state != TaskRuntime.State.READY:
            continue
        var t: TaskData = gs.bundle.tasks_by_id[sid]
        var ev: Dictionary = evaluate(gs, t, gs.gate_cache, day)
        if ev["ready"]:
            rt.blocked_reason = impediment(gs, t, free_laydown)
            gs.set_task_state(sid, TaskRuntime.State.READY)
        else:
            rt.blocked_reason = str(ev["reason"])
            gs.set_task_state(sid, TaskRuntime.State.BLOCKED)
