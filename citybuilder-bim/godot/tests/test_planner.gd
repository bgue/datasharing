extends TC
## Planner behaviour behind the autopilot: idle crews are fired, hiring waits for reassignment,
## staffing follows workable packages, utilisation is reported.

const RS := TaskRuntime.State


func _state() -> SimState:
    var gs: SimState = new_state()
    gs.scenario.events.clear()
    gs.scenario.crews_available["concrete"] = 5
    gs.bundle.trades_by_id["concrete"].max_hire_per_week = 5
    build_road(gs)
    gs.place_tile(Vector2i(1, 3), "laydown")
    return gs


func test_idle_crews_are_fired_after_five_working_days() -> void:
    var gs: SimState = _state()
    var idle: int = gs.hire("concrete")
    gs.assign_crew(idle, "L01-Z1")  # nothing is ready on level 1
    for d in 4:
        gs.run_work_day(d)
    eq(int(gs.crew_idle_days.get(idle, 0)), 4, "four idle working days counted")
    eq(Planner.fire_idle_crews(gs), 0, "not yet")
    gs.run_work_day(4)
    eq(int(gs.crew_idle_days[idle]), 5, "five consecutive idle days")
    eq(Planner.fire_idle_crews(gs), 1, "fired")
    eq(gs.crews.size(), 0, "gone")
    gs.free()


func test_working_crews_are_not_fired_and_the_counter_resets() -> void:
    var gs: SimState = _state()
    var c: int = gs.hire("concrete")
    gs.assign_crew(c, "L01-Z1")
    gs.run_work_day(0)
    gs.run_work_day(1)
    eq(int(gs.crew_idle_days[c]), 2, "idle on level 1")
    gs.assign_crew(c, "L00-Z1")  # footings are ready there
    gs.run_work_day(2)
    eq(int(gs.crew_idle_days[c]), 0, "a working day resets the counter")
    gs.free()


func test_crews_waiting_for_a_partner_are_not_idle() -> void:
    var gs: SimState = _state()
    gs.bundle.packages_by_id["P00001"].crew_min = 2
    gs.bundle.packages_by_id["P00001"].crew_ideal = 2
    gs.bundle.packages_by_id["P00001"].crew_max = 2
    var c: int = gs.hire("concrete")
    gs.assign_crew(c, "L00-Z1")
    for d in 5:
        gs.run_work_day(d)
    eq(int(gs.crew_idle_days.get(c, 0)), 0, "below the package minimum the crew waits for a partner, it is not idle")
    gs.free()


func test_reassign_idle_crew_before_hiring() -> void:
    var gs: SimState = _state()
    var spare: int = gs.hire("concrete")  # unassigned
    gs.refresh_states()
    Planner.daily_plan(gs, "ideal", 1.0, true, true)
    eq(gs.crews.size(), 1, "no second crew hired while one is free")
    eq(str(gs.crew_by_id(spare)["zone_id"]), "L00-Z1", "the free crew went to the ready footings")
    gs.free()


func test_hiring_follows_workable_packages() -> void:
    var gs: SimState = new_state()  # no road: nothing can start, nothing is workable
    gs.scenario.events.clear()
    gs.refresh_states()
    Planner.daily_plan(gs, "ideal", 1.0, true, true)
    eq(gs.crews.size(), 0, "no access: no package is workable, nobody is hired")
    eq(Planner.zone_trade_need(gs, "L00-Z1", "concrete", "ideal"), 0, "zone need ignores work that cannot start")
    build_road(gs)
    gs.place_tile(Vector2i(1, 3), "laydown")
    gs.refresh_states()
    ok(Planner.zone_trade_need(gs, "L00-Z1", "concrete", "ideal") >= 1, "access: footings need a crew")
    Planner.daily_plan(gs, "ideal", 1.0, true, true)
    eq(gs.crews.size(), 1, "one crew for 0.96 crew-days of ready work")
    gs.free()


func test_need_is_capped_by_ready_work() -> void:
    var gs: SimState = _state()
    var p: PackageData = gs.bundle.packages_by_id["P00001"]
    p.crew_min = 1
    p.crew_ideal = 4
    p.crew_max = 5
    gs.bundle.zones_by_id["L00-Z1"].max_crews = 6
    eq(Planner.zone_trade_need(gs, "L00-Z1", "concrete", "ideal"), 1, "under a week of ready work: one crew, not the ideal of 4")
    for t in p.tasks:
        (gs.runtime[t.task_id] as TaskRuntime).required = 20.0
    eq(Planner.zone_trade_need(gs, "L00-Z1", "concrete", "ideal"), 4, "plenty of ready work: the ideal crew")
    eq(Planner.zone_trade_need(gs, "L00-Z1", "concrete", "min"), 1, "min level")
    gs.free()


func test_zone_staff_with_fire_idle() -> void:
    var gs: SimState = _state()
    var idle: int = gs.hire("concrete")
    gs.assign_crew(idle, "L01-Z1")
    for d in 5:
        gs.run_work_day(d)
    var r: Dictionary = Planner.staff_zone(gs, "L01-Z1", "ideal", true, true)
    eq(r["fired"], 1, "zone.staff with hire and fire_idle fires the idle crew of the zone")
    var keep: int = gs.hire("concrete")
    gs.assign_crew(keep, "L01-Z1")
    for d in 5:
        gs.run_work_day(d)
    r = Planner.staff_zone(gs, "L01-Z1", "ideal", true, false)
    eq(r["fired"], 0, "fire_idle=false leaves crews alone")
    gs.free()


func test_utilisation_is_reported() -> void:
    var gs: SimState = _state()
    gs.hire("concrete")
    near(gs.crew_utilisation(), 0.0, "nothing booked yet")
    gs.advance_week()
    var u: float = gs.crew_utilisation()
    ok(u >= 0.0 and u <= 1.0, "utilisation is a fraction: %f" % u)
    near(u, float(gs.last_report["utilisation"]), "weekly report carries it")
    ok(gs.last_report.has("utilisation_week"), "and the week's own figure")
    near(float(ApiViews.summary(gs)["utilisation"]), u, "state.summary exposes it")
    near(float(gs.snapshot()["utilisation"]), u, "snapshot too")
    gs.free()


func test_manage_cranes_demobilises_when_no_crane_work_is_near() -> void:
    var gs: SimState = _state()
    gs.place_tile(Vector2i(1, 1), "crane_pad")
    gs.place_equipment("mc1", Vector2i(1, 1))
    gs.bundle.tasks_by_id["T000005"].planned_start_day = 500  # columns planned far away
    for id in ["T000006", "T000007", "T000008", "T000009"]:
        gs.bundle.tasks_by_id[id].planned_start_day = 500
    var r: Array[int] = Planner.manage_cranes(gs)
    eq(r[0], 1, "crane demobilised: no crane work within two weeks")
    eq(gs.equipment_placed.size(), 0, "no weekly hire")
    gs.bundle.tasks_by_id["T000005"].planned_start_day = 2
    r = Planner.manage_cranes(gs)
    eq(r[1], 1, "crane placed again on the existing pad when columns come up")
    gs.free()
