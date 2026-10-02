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
static func predecessor_block(gs: SimState, task: TaskData) -> String:
    var day: int = gs.week * 5
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
static func ff_pending(gs: SimState, task: TaskData) -> bool:
    for p in task.predecessors:
        if str(p["type"]) != "FF":
            continue
        var prt: TaskRuntime = gs.runtime.get(p["task_id"], null)
        if prt == null:
            continue
        if not TaskRuntime.is_finished(prt.state) or gs.week * 5 < prt.actual_finish_day + int(p["lag_days"]):
            return true
    return false


static func gate_scope_tasks(gs: SimState, gate: GateDef, task: TaskData) -> Array[TaskData]:
    var out: Array[TaskData] = []
    match gate.scope:
        "zone":
            out.assign(gs.bundle.tasks_by_zone.get(task.zone_id, []))
        "storey":
            out.assign(gs.bundle.tasks_by_storey.get(task.storey_id, []))
        _:
            out.assign(gs.bundle.tasks)
    return out


## Returns "" when no gate holds the task, else the reason. `cache` memoises scope checks.
static func gate_block(gs: SimState, task: TaskData, cache: Dictionary = {}) -> String:
    for gate in gs.bundle.gates:
        if gate.before_phase != task.phase:
            continue
        var scope_key: String = ""
        match gate.scope:
            "zone":
                scope_key = task.zone_id
            "storey":
                scope_key = task.storey_id
            _:
                scope_key = "*"
        var key: String = "%s|%s" % [gate.id, scope_key]
        if not cache.has(key):
            var open_count: int = 0
            for t2 in gate_scope_tasks(gs, gate, task):
                if t2.phase != gate.after_phase:
                    continue
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
            return "Gate '%s': %d %s task(s) not complete and inspected" % [gate.name, n, gate.after_phase]
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
static func evaluate(gs: SimState, task: TaskData, gate_cache: Dictionary = {}) -> Dictionary:
    var r: String = predecessor_block(gs, task)
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
