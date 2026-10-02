class_name Productivity
extends RefCounted
## Weekly progress per crew/task (docs/01 section 5.1) and the work pass of advance_week().

const EPS: float = 0.00001


static func congestion_factor(crews_in_zone: int, max_crews: int) -> float:
    var over: int = crews_in_zone - max_crews
    if over <= 0:
        return 1.0
    if over == 1:
        return 0.6
    return 0.35


static func week_of_year(sc: ScenarioData, week: int) -> int:
    return posmod(sc.start_week_of_year + week, 52)


static func weather_factor(sc: ScenarioData, week: int, task: TaskData) -> float:
    if not task.weather_sensitive or sc.monthly_factor.size() < 12:
        return 1.0
    var month: int = clampi(int(float(week_of_year(sc, week)) * 12.0 / 52.0), 0, 11)
    return sc.monthly_factor[month]


## 0.8 on the first repetition, 1.0 after three repetitions of the step.
static func learning_factor(repetitions: int) -> float:
    return 0.8 + 0.2 * float(mini(maxi(repetitions, 0), 3)) / 3.0


static func access_factor(gs: SimState, task: TaskData) -> float:
    if task.requires_access and not gs.zone_access(task.zone_id):
        return 0.0
    if task.requires_crane and not Logistics.crane_covers(gs, task.cells):
        return 0.0
    return 1.0


static func event_factor(gs: SimState, task: TaskData) -> float:
    var f: float = 1.0
    var zone: ZoneData = gs.bundle.zones_by_id.get(task.zone_id, null)
    var step: StepDef = gs.bundle.step_of(task)
    for m in gs.modifiers:
        if int(m["until_week"]) <= gs.week or int(m["from_week"]) > gs.week:
            continue
        var tag: String = str(m.get("tag", ""))
        if tag != "" and not task.has_tag(tag, step.tags if step != null else ([] as Array[String])):
            continue
        var trade: String = str(m.get("trade", ""))
        if trade != "" and trade != task.trade:
            continue
        var ztag: String = str(m.get("zone_tag", ""))
        if ztag != "" and (zone == null or not zone.tags.has(ztag)):
            continue
        f *= float(m["factor"])
    return f


static func zone_crew_count(gs: SimState, zone_id: String) -> int:
    var n: int = 0
    for c in gs.crews:
        if str(c["zone_id"]) == zone_id:
            n += 1
    return n


static func zone_congestion(gs: SimState, zone_id: String) -> float:
    var z: ZoneData = gs.bundle.zones_by_id.get(zone_id, null)
    if z == null:
        return 1.0
    return congestion_factor(zone_crew_count(gs, zone_id), z.max_crews)


## All multipliers for a task, as a Dictionary (also used by the zone inspector / tests).
static func factors(gs: SimState, task: TaskData) -> Dictionary:
    var f: Dictionary = {
        "congestion": zone_congestion(gs, task.zone_id),
        "weather": weather_factor(gs.scenario, gs.week, task),
        "access": access_factor(gs, task),
        "learning": learning_factor(int(gs.learning_counts.get(task.step_id, 0))),
        "event": event_factor(gs, task),
    }
    if int(gs.zone_paused_until.get(task.zone_id, 0)) > gs.week:
        f["event"] = 0.0
    var total: float = 1.0
    for k in ["congestion", "weather", "access", "learning", "event"]:
        total *= float(f[k])
    f["total"] = total
    return f


static func _priority(rt: TaskRuntime) -> int:
    match rt.state:
        TaskRuntime.State.REWORK:
            return 0
        TaskRuntime.State.ACTIVE:
            return 1
    return 2


## One working day (d = 0..4 of the current week). Every assigned crew spends 1 crew-day on
## READY / ACTIVE / REWORK tasks of its trade in its zone (rework first, then highest planned
## start first), with the congestion / weather / access / crane / learning / event multipliers.
## Several tiny tasks can finish in one day; the crew carries on into successors it just released.
static func run_day(gs: SimState, d: int) -> void:
    var day: int = gs.week * 5 + d
    var laydown_free: int = Readiness.laydown_free_cells(gs)
    for crew in gs.crews:
        var zone_id: String = str(crew["zone_id"])
        if zone_id == "":
            continue
        var trade: String = str(crew["trade"])
        var cands: Array[TaskData] = []
        for t in gs.bundle.tasks_by_zone.get(zone_id, []):
            var task: TaskData = t
            if task.trade != trade:
                continue
            var rt: TaskRuntime = gs.runtime[task.task_id]
            if rt.state == TaskRuntime.State.READY or rt.state == TaskRuntime.State.ACTIVE \
                    or rt.state == TaskRuntime.State.REWORK:
                cands.append(task)
        cands.sort_custom(func(a: TaskData, b: TaskData) -> bool:
            var pa: int = _priority(gs.runtime[a.task_id])
            var pb: int = _priority(gs.runtime[b.task_id])
            if pa != pb:
                return pa < pb
            if a.planned_start_day != b.planned_start_day:
                return a.planned_start_day < b.planned_start_day
            return a.task_id < b.task_id)
        var budget: float = 1.0
        var worked_before: float = gs.crew_days_worked
        var idx: int = 0
        while idx < cands.size() and budget > EPS:
            var task: TaskData = cands[idx]
            idx += 1
            var rt: TaskRuntime = gs.runtime[task.task_id]
            if rt.state == TaskRuntime.State.READY:
                if Readiness.impediment(gs, task, laydown_free) != "":
                    continue
                rt.actual_start_day = maxi(day, rt.earliest_start)
                laydown_free -= task.laydown_cells
                gs.spend(task.cost, "materials: " + task.element_name)
                gs.set_task_state(task.task_id, TaskRuntime.State.ACTIVE)
                gs.log_event("Started %s" % Readiness.pred_label(gs, task.task_id))
            var fac: Dictionary = factors(gs, task)
            var m: float = float(fac["total"])
            if m <= EPS:
                continue
            if not gs.worked_this_week.has(task.task_id):
                gs.worked_this_week.append(task.task_id)
            var cap: float = rt.required
            if Readiness.ff_pending(gs, task, day):
                cap = rt.required * 0.999
            var need: float = maxf(0.0, cap - rt.progress)
            var used: float = minf(budget, need / m)
            rt.progress += used * m
            budget -= used
            gs.crew_days_worked += used
            if rt.progress >= rt.required - EPS:
                var done_day: int = maxi(day + 1, rt.actual_start_day)
                _complete_work(gs, task, rt, done_day)
                laydown_free += task.laydown_cells  # finished / awaiting-inspection tasks free their laydown
                if TaskRuntime.is_finished(rt.state) and budget > EPS:
                    _release_successors(gs, task, zone_id, trade, done_day, cands)
        gs.crew_days_idle += maxf(0.0, budget)
        var bt: Array = gs.crew_days_by_trade.get(trade, [0.0, 0.0])
        bt[0] += gs.crew_days_worked - worked_before
        bt[1] += maxf(0.0, budget)
        gs.crew_days_by_trade[trade] = bt


## Successors of a task that just finished, in the crew's zone and trade, become READY mid-week.
static func _release_successors(gs: SimState, task: TaskData, zone_id: String, trade: String, day: int, cands: Array[TaskData]) -> void:
    for sid in gs.bundle.successors_by_task.get(task.task_id, []):
        var succ: TaskData = gs.bundle.tasks_by_id[sid]
        if succ.zone_id != zone_id or succ.trade != trade:
            continue
        var srt: TaskRuntime = gs.runtime[sid]
        if srt.state != TaskRuntime.State.BLOCKED and srt.state != TaskRuntime.State.NOT_STARTED:
            continue
        var ev: Dictionary = Readiness.evaluate(gs, succ, gs.gate_cache, day)
        if bool(ev["ready"]):
            srt.blocked_reason = ""
            srt.earliest_start = day
            gs.set_task_state(sid, TaskRuntime.State.READY)
            cands.append(succ)


static func _complete_work(gs: SimState, task: TaskData, rt: TaskRuntime, day: int) -> void:
    rt.progress = rt.required
    rt.work_done_day = day
    gs.learning_counts[task.step_id] = int(gs.learning_counts.get(task.step_id, 0)) + 1
    if task.inspection:
        rt.inspection_due_day = day + 2
        gs.set_task_state(task.task_id, TaskRuntime.State.AWAITING_INSPECTION)
    else:
        rt.actual_finish_day = day
        gs.set_task_state(task.task_id, TaskRuntime.State.DONE)
        gs.log_event("Finished %s" % Readiness.pred_label(gs, task.task_id))
