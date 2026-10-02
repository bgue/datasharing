extends TC


func test_congestion_factors() -> void:
    near(Productivity.congestion_factor(0, 2), 1.0, "empty")
    near(Productivity.congestion_factor(2, 2), 1.0, "at max")
    near(Productivity.congestion_factor(3, 2), 0.6, "+1")
    near(Productivity.congestion_factor(4, 2), 0.35, "+2")
    near(Productivity.congestion_factor(7, 2), 0.35, "+5 clamps")


func test_zone_congestion_counts_all_trades() -> void:
    var gs: SimState = new_state()
    gs.scenario.crews_available["concrete"] = 5
    gs.scenario.crews_available["finishes"] = 5
    gs.bundle.trades_by_id["concrete"].max_hire_per_week = 9
    for i in 3:
        var id: int = gs.hire("concrete")
        ok(id > 0, "hire %d: %s" % [i, gs.last_error])
        gs.assign_crew(id, "L00-Z1")
    near(Productivity.zone_congestion(gs, "L00-Z1"), 0.6, "3 crews in a 2-crew zone")
    var id2: int = gs.hire("finishes")
    gs.assign_crew(id2, "L00-Z1")
    near(Productivity.zone_congestion(gs, "L00-Z1"), 0.35, "4 crews in a 2-crew zone")
    gs.free()


func test_weather_monthly_factor() -> void:
    var gs: SimState = new_state()
    var footing: TaskData = gs.bundle.tasks_by_id["T000001"]  # weather sensitive
    var column: TaskData = gs.bundle.tasks_by_id["T000005"]  # not
    # start_week_of_year = 10 -> month index int(10*12/52) = 2 (March) -> 0.85
    near(Productivity.weather_factor(gs.scenario, 0, footing), 0.85, "March factor")
    near(Productivity.weather_factor(gs.scenario, 0, column), 1.0, "insensitive step unaffected")
    # week 6 -> week-of-year 16 -> month 3 (April) -> 0.9
    near(Productivity.weather_factor(gs.scenario, 6, footing), 0.9, "April factor")
    # wraps after 52 weeks: week 42 -> woy 0 -> January 0.7
    near(Productivity.weather_factor(gs.scenario, 42, footing), 0.7, "January factor after wrap")
    gs.free()


func test_learning_curve() -> void:
    near(Productivity.learning_factor(0), 0.8, "first repetition")
    near(Productivity.learning_factor(1), 0.8 + 0.2 / 3.0, "second")
    near(Productivity.learning_factor(3), 1.0, "after three")
    near(Productivity.learning_factor(9), 1.0, "capped")


func test_event_modifier_filters() -> void:
    var gs: SimState = new_state()
    var footing: TaskData = gs.bundle.tasks_by_id["T000001"]
    var column: TaskData = gs.bundle.tasks_by_id["T000005"]
    Events.apply_effect(gs, {"productivity_factor": 0.5, "affects_steps_with_tag": "weather_sensitive", "duration_weeks": 2})
    near(Productivity.event_factor(gs, footing), 0.5, "tag-matched task slowed")
    near(Productivity.event_factor(gs, column), 1.0, "non-matching task unaffected")
    Events.apply_effect(gs, {"productivity_factor": 0.5, "affects_trade": "finishes"})
    near(Productivity.event_factor(gs, gs.bundle.tasks_by_id["T000011"]), 0.5, "trade filter")
    Events.apply_effect(gs, {"productivity_factor": 0.5, "affects_zone_tag": "occupied_adjacent"})
    near(Productivity.event_factor(gs, gs.bundle.tasks_by_id["T000009"]), 0.25, "zone tag filter stacks on tag filter")
    gs.week = 2
    gs._expire_modifiers()
    near(Productivity.event_factor(gs, footing), 1.0, "modifiers expire")
    gs.free()


func test_combined_factors_and_weekly_progress() -> void:
    var gs: SimState = new_state()
    gs.scenario.events.clear()
    build_road(gs)
    var id: int = gs.hire("concrete")
    gs.assign_crew(id, "L00-Z1")
    var f: Dictionary = Productivity.factors(gs, gs.bundle.tasks_by_id["T000001"])
    near(float(f["total"]), 1.0 * 0.85 * 1.0 * 0.8 * 1.0, "weather * learning for first footing")
    # crew budget is 5 crew-days; footings need 4 * 0.24 / (0.85*0.8) crew-days of budget
    ok(gs.advance_week(), "advance")
    for tid in ["T000001", "T000002", "T000003", "T000004"]:
        eq(state_of(gs, tid), TaskRuntime.State.AWAITING_INSPECTION, "%s worked to completion in week 0" % tid)
    gs.free()


func test_no_access_means_no_progress() -> void:
    var gs: SimState = new_state()
    gs.scenario.events.clear()
    var id: int = gs.hire("concrete")
    gs.assign_crew(id, "L00-Z1")
    gs.advance_week()
    eq(state_of(gs, "T000001"), TaskRuntime.State.READY, "no road: nothing started")
    near((gs.runtime["T000001"] as TaskRuntime).progress, 0.0, "no progress")
    gs.free()
