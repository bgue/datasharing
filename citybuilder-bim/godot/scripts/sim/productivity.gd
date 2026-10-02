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


## One working day (d = 0..4 of the current week). Crews are dealt over their zone's workable
## packages (Packages.allocate); a package with fewer crews than its minimum makes no progress,
## crews beyond `ideal` contribute packaging.over_ideal_factor, beyond `max` they spill (docs/05 1.2).
## Each working crew spends 1 crew-day on the package's READY / ACTIVE / REWORK tasks (rework first,
## then earliest planned start) with the congestion / face / weather / access / crane / learning /
## event / shift multipliers. Tiny tasks chain within the day.
static func run_day(gs: SimState, d: int) -> void:
    var day: int = gs.week * 5 + d
    var alloc: Dictionary = Packages.allocate(gs)
    Packages.compute_face_state(gs, alloc)
    gs.crew_package.clear()
    var ctx: Dictionary = {"laydown_free": Readiness.laydown_free_cells(gs), "day": day}
    var over_factor: float = gs.bundle.packaging_over_ideal_factor
    var booked: Dictionary = {}  # crew id -> true
    for pkg in gs.bundle.packages:
        if not alloc["by_package"].has(pkg.package_id):
            continue
        var crew_ids: Array = alloc["by_package"][pkg.package_id]
        var n: int = crew_ids.size()
        (gs.package_runtime[pkg.package_id] as PackageRuntime).crews_now = n
        for cid in crew_ids:
            gs.crew_package[int(cid)] = pkg.package_id
        if n < pkg.crew_min:
            for cid in crew_ids:
                _book(gs, int(cid), 0.0, 1.0, booked, true)  # waiting for partners: not "idle" for firing
            continue
        var zone: ZoneData = gs.bundle.zones_by_id[pkg.zone_id]
        var zrt: ZoneRuntime = gs.zone_runtime[pkg.zone_id]
        var counts: Dictionary = {}
        var fs: Dictionary = gs.zone_face_state.get(pkg.zone_id, {})
        for f in fs:
            counts[f] = int(fs[f]["crews"])
        var face_f: float = Packages.face_factor(gs, zone, pkg.work_face, counts)
        var shift_f: float = gs.scenario.shift_productivity_factor if zrt.shift_mode == "double" else 1.0
        for k in n:
            var weight: float = 1.0 if k < pkg.crew_ideal else over_factor
            var trade: String = str(gs.crew_by_id(int(crew_ids[k]))["trade"])
            var crew_id: int = int(crew_ids[k])
            var used: float = _crew_day(gs, pkg, weight * face_f * shift_f, zrt.shift_mode == "double", ctx, 1.0)
            # a crew that ran out of ready work in its package spends the rest of the day on the next
            # released package of its trade in the zone (one that has, or needs only, a single crew)
            if used < 1.0 - EPS:
                for other in Packages.workable(gs, pkg.zone_id, trade):
                    if other.package_id == pkg.package_id:
                        continue
                    var others_crews: int = (alloc["by_package"].get(other.package_id, []) as Array).size()
                    if other.crew_min > 1 and others_crews < other.crew_min:
                        continue  # a package below its minimum crew makes no progress anyway
                    var more: float = _crew_day(gs, other, face_f * shift_f, zrt.shift_mode == "double", ctx, 1.0 - used)
                    used += more
                    if used >= 1.0 - EPS:
                        break
            _book(gs, crew_id, used, 1.0 - used, booked)
    for cid in alloc["idle"]:
        _book(gs, int(cid), 0.0, 1.0, booked)
    # crews without a zone are paid and idle too
    for c in gs.crews:
        if not booked.has(int(c["id"])):
            _book(gs, int(c["id"]), 0.0, 1.0, booked)


static func _book(gs: SimState, crew_id: int, worked: float, idle: float, booked: Dictionary, waiting_for_crew: bool = false) -> void:
    booked[crew_id] = true
    var trade: String = str(gs.crew_by_id(crew_id).get("trade", ""))
    gs.crew_days_worked += worked
    gs.crew_days_idle += idle
    gs.week_crew_days_worked += worked
    gs.week_crew_days_idle += idle
    var bt: Array = gs.crew_days_by_trade.get(trade, [0.0, 0.0])
    bt[0] += worked
    bt[1] += idle
    gs.crew_days_by_trade[trade] = bt
    # consecutive working days without productive work (used by the planner to fire idle crews)
    if worked > 0.01:
        gs.crew_idle_days[crew_id] = 0
    elif not waiting_for_crew:
        gs.crew_idle_days[crew_id] = int(gs.crew_idle_days.get(crew_id, 0)) + 1


## One crew's day on one package. Returns the fraction of the day it was busy.
static func _crew_day(gs: SimState, pkg: PackageData, weight: float, double_shift: bool, ctx: Dictionary, day_fraction: float) -> float:
    var day: int = int(ctx["day"])
    var released: bool = (gs.package_runtime[pkg.package_id] as PackageRuntime).released
    var cands: Array[TaskData] = []
    for t in pkg.tasks:
        var rt0: TaskRuntime = gs.runtime[t.task_id]
        if rt0.state == TaskRuntime.State.ACTIVE or rt0.state == TaskRuntime.State.REWORK \
                or (rt0.state == TaskRuntime.State.READY and released):
            cands.append(t)
    cands.sort_custom(func(a: TaskData, b: TaskData) -> bool:
        var pa: int = _priority(gs.runtime[a.task_id])
        var pb: int = _priority(gs.runtime[b.task_id])
        if pa != pb:
            return pa < pb
        if a.planned_start_day != b.planned_start_day:
            return a.planned_start_day < b.planned_start_day
        return a.task_id < b.task_id)
    var budget: float = day_fraction
    var idx: int = 0
    while idx < cands.size() and budget > EPS:
        var task: TaskData = cands[idx]
        idx += 1
        var rt: TaskRuntime = gs.runtime[task.task_id]
        if rt.state == TaskRuntime.State.READY:
            if Readiness.impediment(gs, task, int(ctx["laydown_free"])) != "":
                continue
            rt.actual_start_day = maxi(day, rt.earliest_start)
            ctx["laydown_free"] = int(ctx["laydown_free"]) - task.laydown_cells
            gs.spend(task.cost, "materials: " + task.element_name)
            gs.set_task_state(task.task_id, TaskRuntime.State.ACTIVE)
            gs.log_event("Started %s" % Readiness.pred_label(gs, task.task_id))
        var fac: Dictionary = factors(gs, task)
        var m: float = float(fac["total"]) * weight
        if m <= EPS:
            continue
        if double_shift:
            rt.worked_double = true
        if not gs.worked_this_week.has(task.task_id):
            gs.worked_this_week.append(task.task_id)
        var cap: float = rt.required
        if Readiness.ff_pending(gs, task, day):
            cap = rt.required * 0.999
        var need: float = maxf(0.0, cap - rt.progress)
        var used: float = minf(budget, need / m)
        rt.progress += used * m
        budget -= used
        if rt.progress >= rt.required - EPS:
            var done_day: int = maxi(day + 1, rt.actual_start_day)
            _complete_work(gs, task, rt, done_day)
            ctx["laydown_free"] = int(ctx["laydown_free"]) + task.laydown_cells
            if TaskRuntime.is_finished(rt.state) and budget > EPS:
                _release_successors(gs, task, pkg, released, done_day, cands)
    return day_fraction - budget


## Successors of a task that just finished, in the crew's package, become READY mid-day.
static func _release_successors(gs: SimState, task: TaskData, pkg: PackageData, released: bool, day: int, cands: Array[TaskData]) -> void:
    for sid in gs.bundle.successors_by_task.get(task.task_id, []):
        var succ: TaskData = gs.bundle.tasks_by_id[sid]
        if succ.package_id != pkg.package_id:
            continue
        var srt: TaskRuntime = gs.runtime[sid]
        if srt.state != TaskRuntime.State.BLOCKED and srt.state != TaskRuntime.State.NOT_STARTED:
            continue
        var ev: Dictionary = Readiness.evaluate(gs, succ, gs.gate_cache, day)
        if bool(ev["ready"]):
            srt.blocked_reason = ""
            srt.earliest_start = day
            gs.set_task_state(sid, TaskRuntime.State.READY)
            if released:
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
