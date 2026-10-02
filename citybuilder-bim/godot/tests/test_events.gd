extends TC

const RS := TaskRuntime.State


func _ids(list: Array[EventDef]) -> Array[String]:
    var out: Array[String] = []
    for e in list:
        out.append(e.id)
    return out


func test_missing_icra_barrier_trigger() -> void:
    var gs: SimState = new_state()
    var ids: Array[String] = _ids(Events.eligible(gs))
    ok(ids.has("icra_audit"), "audit eligible with no barrier next to occupied_adjacent zone")
    ok(ids.has("rain"), "rain has no trigger")
    # a barrier next to the L01 zone (cells 2..3,2..3) satisfies the audit
    ok(gs.place_tile(Vector2i(4, 3), "icra_barrier"), "place barrier: " + gs.last_error)
    ids = _ids(Events.eligible(gs))
    ok(not ids.has("icra_audit"), "audit not eligible once barrier is adjacent")
    ok(ids.has("rain"), "rain still eligible")
    ok(gs.remove_tile(Vector2i(4, 3)), "remove barrier")
    ok(_ids(Events.eligible(gs)).has("icra_audit"), "eligible again after barrier removed")
    # a barrier far from the zone does not count
    ok(gs.place_tile(Vector2i(7, 0), "icra_barrier"), "far barrier")
    ok(_ids(Events.eligible(gs)).has("icra_audit"), "far barrier does not satisfy")
    gs.free()


func test_trigger_ignores_finished_zones() -> void:
    var gs: SimState = new_state()
    set_finished(gs, "T000009")
    ok(not _ids(Events.eligible(gs)).has("icra_audit"), "no audit once the tagged zone has no open work")
    gs.free()


func test_audit_effect_pauses_zone_and_costs_score() -> void:
    var gs: SimState = new_state()
    var audit: EventDef = null
    for e in gs.scenario.events:
        if e.id == "icra_audit":
            audit = e
    ok(audit != null, "audit event exists")
    Events.fire(gs, audit)
    ok(int(gs.zone_paused_until.get("L01-Z1", 0)) > gs.week, "L01 zone paused")
    near(gs.score_adjust, -5.0, "score_delta applied")
    gs.free()


func test_window_and_once() -> void:
    var gs: SimState = new_state()
    var ev: EventDef = EventDef.from_dict({"id": "x", "name": "X", "weight": 1, "min_week": 3, "max_week": 5, "once": true, "effect": {}})
    gs.scenario.events.clear()
    gs.scenario.events.append(ev)
    eq(Events.eligible(gs).size(), 0, "before min_week")
    gs.week = 3
    eq(Events.eligible(gs).size(), 1, "inside window")
    gs.week = 6
    eq(Events.eligible(gs).size(), 0, "after max_week")
    gs.week = 4
    Events.fire(gs, ev)
    eq(Events.eligible(gs).size(), 0, "once: not eligible again")
    gs.free()


func test_other_triggers() -> void:
    var gs: SimState = new_state()
    var mk := func(trig: Dictionary) -> EventDef:
        return EventDef.from_dict({"id": "t", "name": "T", "weight": 1, "trigger": trig, "effect": {}})
    ok(not bool(Events.check_trigger(gs, mk.call({"cash_below": 1000}))["ok"]), "cash_below false at 40000")
    gs.cash = 500.0
    ok(bool(Events.check_trigger(gs, mk.call({"cash_below": 1000}))["ok"]), "cash_below true")
    ok(not bool(Events.check_trigger(gs, mk.call({"phase_active": "substructure"}))["ok"]), "phase_active false when idle")
    gs.set_task_state("T000001", RS.ACTIVE)
    ok(bool(Events.check_trigger(gs, mk.call({"phase_active": "substructure"}))["ok"]), "phase_active true")
    ok(bool(Events.check_trigger(gs, mk.call({"step_active": "STR-FOOT-POUR"}))["ok"]), "step_active true")
    ok(not bool(Events.check_trigger(gs, mk.call({"step_active": "STR-SLAB-POUR"}))["ok"]), "step_active false")
    ok(not bool(Events.check_trigger(gs, mk.call({"zone_tag": "occupied_adjacent"}))["ok"]), "zone_tag: nothing active in tagged zone")
    gs.set_task_state("T000009", RS.ACTIVE)
    ok(bool(Events.check_trigger(gs, mk.call({"zone_tag": "occupied_adjacent"}))["ok"]), "zone_tag true")
    ok(not bool(Events.check_trigger(gs, mk.call({"congestion_over": 1.0}))["ok"]), "congestion_over false without crews")
    gs.scenario.crews_available["concrete"] = 5
    gs.bundle.trades_by_id["concrete"].max_hire_per_week = 9
    for i in 3:
        gs.assign_crew(gs.hire("concrete"), "L00-Z1")
    ok(bool(Events.check_trigger(gs, mk.call({"congestion_over": 1.0}))["ok"]), "3 crews / max 2 = 1.5 > 1.0")
    gs.free()


func test_effects() -> void:
    var gs: SimState = new_state()
    var c0: float = gs.cash
    Events.apply_effect(gs, {"cash_delta": -1500})
    near(gs.cash, c0 - 1500.0, "negative cash_delta")
    Events.apply_effect(gs, {"cash_delta": 700})
    near(gs.cash, c0 - 800.0, "positive cash_delta")
    Events.apply_effect(gs, {"goodwill_delta": -2})
    near(gs.goodwill, -2.0, "goodwill")
    gs.bundle.tasks_by_id["T000001"].lead_time_weeks = 2
    gs.order("T000001")
    Events.apply_effect(gs, {"delay_lead_time_weeks": 3})
    eq((gs.runtime["T000001"] as TaskRuntime).delivery_week, 5, "delivery delayed")
    Events.apply_effect(gs, {"pause_zone_weeks": 2, "affects_zone_tag": "occupied_adjacent"})
    eq(int(gs.zone_paused_until["L01-Z1"]), 3, "pause 2 weeks from next week")
    Events.apply_effect(gs, {"incident": true}, ["L00-Z1"])
    eq(gs.incidents, 1, "incident")
    gs.free()


func test_choices_wait_for_player() -> void:
    var gs: SimState = new_state()
    gs.scenario.events.clear()
    var ev: EventDef = EventDef.from_dict({"id": "rfi", "name": "RFI", "weight": 1, "effect": {}, "choices": [
        {"label": "Pay", "effect": {"cash_delta": -1000}}, {"label": "Wait", "effect": {"score_delta": -3}}]})
    var seen: Array = []
    gs.event_fired.connect(func(e: EventDef, choices: Array) -> void: seen.append([e.id, choices.size()]))
    Events.fire(gs, ev)
    eq(seen, [["rfi", 2]], "event_fired emitted with two choices")
    ok(not gs.pending_event.is_empty(), "pending")
    ok(not gs.advance_week(), "week cannot advance while a choice is pending")
    var c0: float = gs.cash
    gs.resolve_event(0)
    near(gs.cash, c0 - 1000.0, "choice effect applied")
    ok(gs.pending_event.is_empty(), "resolved")
    ok(gs.advance_week(), "week advances after resolution")
    gs.free()


func test_weekly_draw_is_seeded() -> void:
    var a: SimState = new_state()
    var b: SimState = new_state()
    var la: Array[String] = []
    var lb: Array[String] = []
    a.event_fired.connect(func(e: EventDef, _c: Array) -> void: la.append(e.id))
    b.event_fired.connect(func(e: EventDef, _c: Array) -> void: lb.append(e.id))
    for i in 12:
        Events.draw(a)
        Events.draw(b)
    eq(la, lb, "same seed -> same event sequence")
    ok(la.size() > 0, "some events fired in 12 draws")
    a.free()
    b.free()
