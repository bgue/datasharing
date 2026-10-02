class_name ApiViews
extends RefCounted
## Plain-dictionary views of the simulation for the control API (docs/05 section 6.3) and the UI.


static func cell_arr(c: Vector2i) -> Array:
    return [c.x, c.y]


static func pending_event(gs: SimState) -> Variant:
    if gs.pending_event.is_empty():
        return null
    var ev: EventDef = gs.pending_event["event"]
    var choices: Array = []
    for i in ev.choices.size():
        choices.append({"id": i, "text": str(ev.choices[i]["label"])})
    return {"event_id": ev.id, "name": ev.name, "text": ev.text, "choices": choices}


static func summary(gs: SimState) -> Dictionary:
    var score: Dictionary = Scoring.compute(gs, true)
    return {
        "scenario_id": gs.scenario.id, "name": gs.scenario.name, "sector": gs.scenario.sector,
        "week": gs.week, "day": gs.current_day(), "cash": gs.cash, "budget": gs.bundle.contract_budget(),
        "contract_weeks": gs.bundle.contract_weeks(), "counts": gs.state_counts(), "score": score,
        "tasks_total": gs.runtime.size(), "tasks_finished": gs.finished_task_count(),
        "incidents": gs.incidents, "speed": gs.speed, "spent": gs.spent_total,
        "double_shift_zone_weeks": gs.double_shift_zone_weeks,
        "finished": gs.finished, "won": gs.won, "result": gs.result,
        "pending_event": pending_event(gs),
    }


static func station_name_of(gs: SimState, p: PackageData) -> Variant:
    var rt: PackageRuntime = gs.package_runtime[p.package_id]
    var zr: ZoneRuntime = gs.zone_runtime.get(p.zone_id, null)
    if rt.station_index < 0 or zr == null or zr.card_id == "":
        return null
    return Cards.station_name(gs, zr, rt.station_index)


static func package_view(gs: SimState, p: PackageData) -> Dictionary:
    var rt: PackageRuntime = gs.package_runtime[p.package_id]
    var zr: ZoneRuntime = gs.zone_runtime.get(p.zone_id, null)
    var d: Dictionary = p.to_dict()
    d["state"] = rt.state
    d["released"] = rt.released
    d["priority"] = rt.priority
    d["crews_now"] = rt.crews_now
    d["crew_days_done"] = rt.crew_days_done
    d["remaining_crew_days"] = maxf(p.total_crew_days - rt.crew_days_done, 0.0)
    d["blocked_reason"] = rt.blocked_reason
    d["station"] = station_name_of(gs, p)
    d["behind_takt"] = zr != null and zr.behind_takt and rt.station_index == zr.station_index
    return d


static func task_view(gs: SimState, t: TaskData) -> Dictionary:
    var rt: TaskRuntime = gs.runtime[t.task_id]
    var d: Dictionary = t.raw.duplicate(true)
    d["package_id"] = t.package_id
    d["state"] = TaskRuntime.state_name(rt.state)
    d["progress"] = rt.progress
    d["required"] = rt.required
    d["blocked_reason"] = rt.blocked_reason
    d["actual_start_day"] = rt.actual_start_day if rt.actual_start_day >= 0 else null
    d["actual_finish_day"] = rt.actual_finish_day if (TaskRuntime.is_finished(rt.state) and rt.actual_finish_day >= 0) else null
    return d


static func zone_view(gs: SimState, z: ZoneData) -> Dictionary:
    var d: Dictionary = gs.zone_status(z.id)
    var zr: ZoneRuntime = gs.zone_runtime[z.id]
    var crew_list: Array = []
    for c in gs.crews:
        if str(c["zone_id"]) == z.id:
            crew_list.append({"id": int(c["id"]), "trade": str(c["trade"]), "package_id": gs.crew_package.get(int(c["id"]), null)})
    d["zone_id"] = z.id
    d["id"] = z.id
    d["name"] = z.name
    d["storey_id"] = z.storey_id
    d["tags"] = z.tags.duplicate()
    d["max_crews"] = z.max_crews
    d["crews"] = crew_list.size()
    d["crew_list"] = crew_list
    d["faces"] = z.faces.duplicate()
    d["face_state"] = (gs.zone_face_state.get(z.id, {}) as Dictionary).duplicate(true)
    d["shift_mode"] = zr.shift_mode
    d["shift_allowed"] = z.shift_allowed
    d["card"] = zr.card_id if zr.card_id != "" else null
    d["station"] = Cards.station_name(gs, zr, zr.station_index) if zr.card_id != "" else null
    d["station_index"] = zr.station_index if zr.card_id != "" else null
    d["behind_takt"] = zr.behind_takt
    d["card_done"] = zr.card_done
    d["auto_staff"] = zr.auto_staff
    d["hold_until_week"] = zr.hold_until_week
    d["paused_until_week"] = int(gs.zone_paused_until.get(z.id, 0))
    d["access"] = gs.zone_access(z.id)
    return d


static func crews(gs: SimState) -> Dictionary:
    var list: Array = []
    for c in gs.crews:
        list.append({"id": int(c["id"]), "trade": str(c["trade"]), "zone_id": str(c["zone_id"]),
                "package_id": gs.crew_package.get(int(c["id"]), null)})
    var caps: Dictionary = {}
    var left: Dictionary = {}
    for t in gs.bundle.trades:
        caps[t.id] = gs.crews_cap(t.id)
        left[t.id] = gs.hires_left_this_week(t.id)
    return {"crews": list, "caps": caps, "hires_left": left}


static func tiles(gs: SimState) -> Dictionary:
    var list: Array = []
    for c in gs.tiles:
        var td: Dictionary = gs.tiles[c]
        list.append({"cell": cell_arr(c), "tile": str(td["tile"]), "orientation": int(td["orientation"])})
    var eq: Array = []
    for i in gs.equipment_placed.size():
        var e: Dictionary = gs.equipment_placed[i]
        eq.append({"index": i, "id": str(e["id"]), "cell": cell_arr(e["cell"])})
    var access: Dictionary = {}
    for z in gs.bundle.zones:
        access[z.id] = gs.zone_access(z.id)
    return {"tiles": list, "equipment": eq, "access": access, "laydown_capacity": Logistics.laydown_capacity(gs),
            "laydown_used": Logistics.laydown_used(gs)}


static func procurement(gs: SimState) -> Array:
    var out: Array = []
    for t in gs.bundle.tasks:
        if t.lead_time_weeks <= 0:
            continue
        var rt: TaskRuntime = gs.runtime[t.task_id]
        out.append({"task_id": t.task_id, "package_id": t.package_id, "name": t.element_name, "step_id": t.step_id,
                "lead_time_weeks": t.lead_time_weeks, "planned_start_day": t.planned_start_day,
                "ordered": rt.ordered, "delivery_week": rt.delivery_week if rt.ordered else null,
                "late": not TaskRuntime.is_finished(rt.state) and gs.order_is_late(t.task_id),
                "state": TaskRuntime.state_name(rt.state)})
    return out


## Bars in the shape the Gantt panel draws (docs/05 section 5), plus markers.
static func gantt(gs: SimState, zone_ids: Array = [], from_week: int = -1, to_week: int = -1) -> Dictionary:
    var zone_filter: Dictionary = {}
    for z in zone_ids:
        zone_filter[str(z)] = true
    var bars: Array = []
    for p in gs.bundle.packages:
        if not zone_filter.is_empty() and not zone_filter.has(p.zone_id):
            continue
        var rt: PackageRuntime = gs.package_runtime[p.package_id]
        var a_start: int = 1 << 30
        var a_finish: int = -1
        for t in p.tasks:
            var trt: TaskRuntime = gs.runtime[t.task_id]
            if trt.actual_start_day >= 0:
                a_start = mini(a_start, trt.actual_start_day)
            if TaskRuntime.is_finished(trt.state):
                a_finish = maxi(a_finish, trt.actual_finish_day)
        var done: bool = rt.state == "done"
        var s_day: int = a_start if a_start < (1 << 30) else p.planned_start_day
        var f_day: int = a_finish if (done and a_finish >= 0) else maxi(p.planned_finish_day, gs.current_day() if a_start < (1 << 30) else 0)
        if from_week >= 0 and f_day < from_week * 5 and p.planned_finish_day < from_week * 5:
            continue
        if to_week >= 0 and s_day > to_week * 5 and p.planned_start_day > to_week * 5:
            continue
        var zr: ZoneRuntime = gs.zone_runtime[p.zone_id]
        bars.append({
            "package_id": p.package_id, "zone_id": p.zone_id, "storey_id": p.storey_id, "name": p.name,
            "phase": p.phase, "trade": p.trade, "work_face": p.work_face,
            "planned_start_day": p.planned_start_day, "planned_finish_day": p.planned_finish_day,
            "actual_start_day": a_start if a_start < (1 << 30) else null,
            "actual_finish_day": a_finish if (done and a_finish >= 0) else null,
            "progress": clampf(rt.crew_days_done / maxf(p.total_crew_days, 0.0001), 0.0, 1.0) if not done else 1.0,
            "state": rt.state, "discipline": p.discipline,
            "understaffed": rt.state == "understaffed", "held": rt.state == "held",
            "behind_takt": zr.behind_takt and rt.station_index == zr.station_index,
            "crews_now": rt.crews_now, "crew_min": p.crew_min, "crew_ideal": p.crew_ideal, "crew_max": p.crew_max,
            "remaining_crew_days": maxf(p.total_crew_days - rt.crew_days_done, 0.0),
            "blocked_reason": rt.blocked_reason, "station_index": rt.station_index,
        })
    var deliveries: Array = []
    for t in gs.bundle.tasks:
        var rt2: TaskRuntime = gs.runtime[t.task_id]
        if t.lead_time_weeks > 0 and rt2.ordered and (zone_filter.is_empty() or zone_filter.has(t.zone_id)):
            deliveries.append({"zone_id": t.zone_id, "task_id": t.task_id, "week": rt2.delivery_week})
    var stations: Array = []
    for zid in gs.card_zones():
        if not zone_filter.is_empty() and not zone_filter.has(zid):
            continue
        var zr2: ZoneRuntime = gs.zone_runtime[zid]
        var card: SequenceCardData = gs.bundle.cards_by_id[zr2.card_id]
        var list: Array = []
        for i in card.stations.size():
            list.append({"index": i, "name": card.stations[i].name, "takt_weeks": card.stations[i].takt_weeks})
        stations.append({"zone_id": zid, "card_id": zr2.card_id, "current": zr2.station_index, "stations": list,
                "behind_takt": zr2.behind_takt, "station_start_week": zr2.station_start_week,
                "hold_until_week": zr2.hold_until_week})
    return {"bars": bars, "current_day": gs.current_day(), "week": gs.week, "deliveries": deliveries,
            "incidents": gs.incident_log.duplicate(true), "stations": stations}
