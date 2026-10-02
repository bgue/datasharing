extends TC

const RS := TaskRuntime.State


func test_incident_probability_formula() -> void:
    var gs: SimState = new_state()
    var ids: Array[String] = ["T000001"]  # risk 0.15
    near(Safety.incident_probability_for(gs, ids), 0.15 * 0.01 * 1.0 * 1.5, "no hoarding -> x1.5")
    ok(gs.place_tile(Vector2i(1, 1), "hoarding"), "hoarding diagonal-adjacent to the zone")
    near(Safety.incident_probability_for(gs, ids), 0.15 * 0.01, "hoarding adjacent -> x1.0")
    gs.speed = 2
    near(Safety.incident_probability_for(gs, ids), 0.15 * 0.01 * 1.2, "speed > 1x -> x1.2")
    gs.speed = 1
    var two: Array[String] = ["T000001", "T000005"]  # risks 0.15 + 0.25
    near(Safety.incident_probability_for(gs, two), (0.15 + 0.25) * 0.01, "sum over tasks")
    gs.scenario.crews_available["concrete"] = 5
    gs.bundle.trades_by_id["concrete"].max_hire_per_week = 9
    for i in 3:
        gs.assign_crew(gs.hire("concrete"), "L00-Z1")  # 3 crews in a max-2 zone: congestion 0.6
    near(Safety.incident_probability_for(gs, ids), 0.15 * 0.01 / 0.6, "congestion penalty = 1 / congestion factor")
    gs.free()


func test_incident_stops_zone_for_a_week() -> void:
    var gs: SimState = new_state()
    gs.scenario.events.clear()
    build_road(gs)
    var id: int = gs.hire("concrete")
    gs.assign_crew(id, "L00-Z1")
    Safety.trigger_incident(gs, "L00-Z1")
    eq(gs.incidents, 1, "incident counted")
    # In the weekly loop an incident rolled during week w stops the zone for week w + 1
    # (zone_paused_until = w + 2); fired before week 0 is processed it also covers week 0.
    eq(int(gs.zone_paused_until["L00-Z1"]), 2, "paused until week 2")
    gs.advance_week()
    gs.advance_week()
    eq(state_of(gs, "T000001"), RS.READY, "no work in the stopped zone while paused")
    gs.advance_week()
    ok(state_of(gs, "T000001") in [RS.AWAITING_INSPECTION, RS.INSPECTED, RS.REWORK], "work resumes once the pause is over")
    gs.free()


func test_hard_mode_fails_after_max_incidents() -> void:
    var gs: SimState = new_state()
    gs.scenario.events.clear()
    gs.scenario.difficulty = "hard"
    for i in 3:
        Safety.trigger_incident(gs, "L00-Z1")
    gs.advance_week()
    ok(gs.finished and not gs.won, "three recordables fail a hard level")
    gs.free()
