class_name Events
extends RefCounted
## Weekly event deck: eligibility, triggers, effects and player choices (docs/01 section 5.6).

## Chance that the deck produces an event in a given week (then a weighted pick).
const EVENT_CHANCE: float = 0.5
## Default tile checked by `missing_tile_adjacent_to_tag` triggers.
const DEFAULT_MISSING_TILE: String = SiteTiles.ICRA_BARRIER


static func _zone_has_unfinished(gs: SimState, zone_id: String) -> bool:
    for t in gs.bundle.tasks_by_zone.get(zone_id, []):
        if not TaskRuntime.is_finished(gs.runtime[(t as TaskData).task_id].state):
            return true
    return false


static func _is_active(rt: TaskRuntime) -> bool:
    return rt.state == TaskRuntime.State.ACTIVE or rt.state == TaskRuntime.State.REWORK


## Zones with the tag that have unfinished work and no `tile_id` tile adjacent.
static func zones_missing_tile(gs: SimState, tag: String, tile_id: String = DEFAULT_MISSING_TILE) -> Array[String]:
    var out: Array[String] = []
    var tile_cells: Array[Vector2i] = Logistics.tile_cells(gs.tiles, tile_id)  # once, not a ring per zone
    for z in gs.bundle.zones:
        if not z.tags.has(tag):
            continue
        if not _zone_has_unfinished(gs, z.id):
            continue
        if not Logistics.ring_touches(tile_cells, z.cells):
            out.append(z.id)
    return out


## Evaluates all trigger conditions. Returns {"ok": bool, "zones": Array[String]}.
static func check_trigger(gs: SimState, ev: EventDef) -> Dictionary:
    var tr: Dictionary = ev.trigger
    var zones: Array[String] = []
    if tr.has("phase_active"):
        var found: bool = false
        var ph: String = str(tr["phase_active"])
        for i in gs.runtime.active_sorted():
            var task: TaskData = gs.runtime.task_refs[i]
            if task.phase == ph:
                found = true
                if not zones.has(task.zone_id):
                    zones.append(task.zone_id)
        if not found:
            return {"ok": false, "zones": zones}
    if tr.has("step_active"):
        var found2: bool = false
        for i in gs.runtime.active_sorted():
            var task: TaskData = gs.runtime.task_refs[i]
            if task.step_id == str(tr["step_active"]):
                found2 = true
                if not zones.has(task.zone_id):
                    zones.append(task.zone_id)
        if not found2:
            return {"ok": false, "zones": zones}
    if tr.has("zone_tag"):
        var tag: String = str(tr["zone_tag"])
        var found3: bool = false
        var active_zones: Dictionary = {}
        for i in gs.runtime.active:
            active_zones[gs.runtime.task_refs[i].zone_id] = true
        for z in gs.bundle.zones:
            if not z.tags.has(tag):
                continue
            if active_zones.has(z.id):
                found3 = true
                if not zones.has(z.id):
                    zones.append(z.id)
        if not found3:
            return {"ok": false, "zones": zones}
    if tr.has("missing_tile_adjacent_to_tag"):
        var missing_tile: String = str(tr.get("missing_tile", DEFAULT_MISSING_TILE))
        var mz: Array[String] = zones_missing_tile(gs, str(tr["missing_tile_adjacent_to_tag"]), missing_tile)
        if mz.is_empty():
            return {"ok": false, "zones": zones}
        for zid in mz:
            if not zones.has(zid):
                zones.append(zid)
    if tr.has("congestion_over"):
        var thr: float = float(tr["congestion_over"])
        var any: bool = false
        for z in gs.bundle.zones:
            var n: int = Productivity.zone_crew_count(gs, z.id)
            if n > 0 and float(n) / float(maxi(1, z.max_crews)) > thr:
                any = true
                if not zones.has(z.id):
                    zones.append(z.id)
        if not any:
            return {"ok": false, "zones": zones}
    if tr.has("cash_below"):
        if not (gs.cash < float(tr["cash_below"])):
            return {"ok": false, "zones": zones}
    return {"ok": true, "zones": zones}


## Events that can fire this week (window, once, trigger).
static func eligible(gs: SimState) -> Array[EventDef]:
    var out: Array[EventDef] = []
    for ev in gs.scenario.events:
        if ev.weight <= 0.0:
            continue
        if gs.week < ev.min_week:
            continue
        if ev.max_week >= 0 and gs.week > ev.max_week:
            continue
        if ev.once and gs.fired_events.has(ev.id):
            continue
        if not bool(check_trigger(gs, ev)["ok"]):
            continue
        out.append(ev)
    return out


static func weighted_pick(list: Array[EventDef], roll: float) -> EventDef:
    var total: float = 0.0
    for ev in list:
        total += ev.weight
    if total <= 0.0:
        return null
    var x: float = roll * total
    for ev in list:
        x -= ev.weight
        if x < 0.0:
            return ev
    return list[list.size() - 1]


## Weekly draw (advance_week step 2).
static func draw(gs: SimState) -> void:
    var chance_roll: float = gs.rng.randf()
    var pick_roll: float = gs.rng.randf()
    if chance_roll >= EVENT_CHANCE:
        return
    var list: Array[EventDef] = eligible(gs)
    var ev: EventDef = weighted_pick(list, pick_roll)
    if ev == null:
        return
    fire(gs, ev)


static func fire(gs: SimState, ev: EventDef) -> void:
    gs.fired_events[ev.id] = int(gs.fired_events.get(ev.id, 0)) + 1
    var zones: Array[String] = []
    zones.assign(check_trigger(gs, ev)["zones"])
    gs.log_event("EVENT: %s" % ev.name)
    if not ev.choices.is_empty():
        gs.pending_event = {"event": ev, "zones": zones}
        gs.event_fired.emit(ev, ev.choices)
        return
    apply_effect(gs, ev.effect, zones)
    gs.event_fired.emit(ev, [])


## Applies one effect dictionary (event effect or choice effect).
static func apply_effect(gs: SimState, eff: Dictionary, trigger_zones: Array[String] = []) -> void:
    if eff.has("productivity_factor"):
        var dur: int = maxi(1, int(eff.get("duration_weeks", 1)))
        gs.modifiers.append({
            "factor": float(eff["productivity_factor"]),
            "tag": str(eff.get("affects_steps_with_tag", "")),
            "trade": str(eff.get("affects_trade", "")),
            "zone_tag": str(eff.get("affects_zone_tag", "")),
            "from_week": gs.week,
            "until_week": gs.week + dur,
        })
    if eff.has("cash_delta"):
        var d: float = float(eff["cash_delta"])
        if d < 0.0:
            gs.spend(-d, "event")
        else:
            gs.cash += d
            gs.cash_changed.emit(gs.cash)
    if eff.has("delay_lead_time_weeks"):
        var delay: int = int(eff["delay_lead_time_weeks"])
        for i in gs.runtime.ordered:
            if gs.runtime.delivery_week[i] > gs.week:
                gs.runtime.delivery_week[i] += delay
    if eff.has("pause_zone_weeks"):
        var weeks: int = int(eff["pause_zone_weeks"])
        for zid in _target_zones(gs, eff, trigger_zones):
            gs.zone_paused_until[zid] = maxi(int(gs.zone_paused_until.get(zid, 0)), gs.week + 1 + weeks)
    if bool(eff.get("incident", false)):
        var zs: Array[String] = _target_zones(gs, eff, trigger_zones)
        if not zs.is_empty():
            Safety.trigger_incident(gs, zs[0])
    if eff.has("goodwill_delta"):
        gs.goodwill += float(eff["goodwill_delta"])
    if eff.has("score_delta"):
        gs.score_adjust += float(eff["score_delta"])


## Zones an effect applies to: affects_zone_tag, else trigger zones, else one random zone with active work.
static func _target_zones(gs: SimState, eff: Dictionary, trigger_zones: Array[String]) -> Array[String]:
    var out: Array[String] = []
    var tag: String = str(eff.get("affects_zone_tag", ""))
    if tag != "":
        for z in gs.bundle.zones:
            if z.tags.has(tag):
                out.append(z.id)
        return out
    if not trigger_zones.is_empty():
        return trigger_zones
    var active: Array[String] = []
    for i in gs.runtime.active_sorted():
        var az: String = gs.runtime.task_refs[i].zone_id
        if not active.has(az):
            active.append(az)
    if active.is_empty():
        return out
    out.append(active[gs.rng.randi() % active.size()])
    return out


static func resolve(gs: SimState, choice_index: int) -> void:
    if gs.pending_event.is_empty():
        return
    var ev: EventDef = gs.pending_event["event"]
    var zones: Array[String] = []
    zones.assign(gs.pending_event["zones"])
    if choice_index >= 0 and choice_index < ev.choices.size():
        gs.log_event("Chose: %s" % ev.choices[choice_index]["label"])
        apply_effect(gs, ev.choices[choice_index]["effect"], zones)
    gs.pending_event = {}
