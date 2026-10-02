extends TC


func _quiet_state() -> SimState:
    var gs: SimState = new_state()
    gs.scenario.events.clear()
    return gs


func test_hire_cost_deducted_weekly() -> void:
    var gs: SimState = _quiet_state()
    ok(gs.hire("concrete") > 0, "hire concrete")
    var c0: float = gs.cash
    near(c0, 40000.0, "no hiring fee up front")
    gs.advance_week()
    near(gs.cash, c0 - 9000.0, "weekly crew cost")
    gs.advance_week()
    near(gs.cash, c0 - 18000.0, "weekly crew cost again")
    near(gs.spent_total, 18000.0, "spend tracked")
    gs.free()


func test_hiring_limits() -> void:
    var gs: SimState = _quiet_state()
    ok(gs.hire("concrete") > 0, "first concrete")
    ok(gs.hire("concrete") > 0, "second concrete (cap 2, max_hire_per_week 2)")
    ok(gs.hire("concrete") < 0, "third refused: crews_available cap")
    ok(gs.hire("finishes") > 0, "one finishes crew")
    ok(gs.hire("finishes") < 0, "second finishes refused (cap 1)")
    ok(gs.hire("nonsense") < 0, "unknown trade refused")
    gs.advance_week()
    var first: int = int(gs.crews[0]["id"])
    ok(gs.fire(first), "fire a crew")
    ok(gs.hire("concrete") > 0, "can hire again after firing and a new week")
    gs.free()


func test_max_hire_per_week() -> void:
    var gs: SimState = _quiet_state()
    gs.scenario.crews_available["concrete"] = 6
    ok(gs.hire("concrete") > 0, "1")
    ok(gs.hire("concrete") > 0, "2")
    ok(gs.hire("concrete") < 0, "3rd in a week refused (max_hire_per_week default 2)")
    gs.advance_week()
    ok(gs.hire("concrete") > 0, "allowed next week")
    gs.free()


func test_equipment_and_rent_in_weekly_outflow() -> void:
    var gs: SimState = _quiet_state()
    gs.place_tile(Vector2i(1, 1), "crane_pad")
    gs.place_equipment("mc1", Vector2i(1, 1))
    gs.place_tile(Vector2i(5, 5), "welfare")
    var out: Dictionary = Economy.weekly_outflow(gs)
    near(float(out["equipment"]), 4000.0, "crane hire")
    near(float(out["rent"]), 400.0, "welfare rent")
    var c0: float = gs.cash
    gs.advance_week()
    near(gs.cash, c0 - 4400.0, "outflow paid")
    gs.free()


func test_progress_payment_arrives() -> void:
    var gs: SimState = _quiet_state()
    for id in ["T000001", "T000002", "T000003", "T000004"]:
        set_finished(gs, id)
    near(Economy.payment_factor(gs), 120000.0 / 27080.0, "payment factor = budget / task cost")
    var expected: float = 4.0 * 720.0 * Economy.payment_factor(gs) * 0.95
    gs.week = 3  # (3+1) % 4 == 0 -> payment week
    var c0: float = gs.cash
    gs.advance_week()
    near(gs.cash - c0, expected, "net payment (5% retention held)", 0.01)
    near(gs.retention_held, expected / 0.95 * 0.05, "retention accumulates", 0.01)
    var c1: float = gs.cash
    gs.week = 7
    gs.advance_week()
    near(gs.cash, c1, "finished tasks are only paid once")
    gs.free()


func test_no_payment_off_cycle() -> void:
    var gs: SimState = _quiet_state()
    set_finished(gs, "T000001")
    var c0: float = gs.cash
    gs.advance_week()  # week 0 -> not a payment week
    near(gs.cash, c0, "no payment in week 0")
    gs.free()


func test_overdraft_fee_and_bankruptcy() -> void:
    var gs: SimState = _quiet_state()
    near(Economy.overdraft_fee(-1000.0), 20.0, "2% of the overdraft")
    near(Economy.overdraft_fee(500.0), 0.0, "no fee when in credit")
    gs.cash = -1000.0
    gs.advance_week()
    near(gs.cash, -1020.0, "overdraft fee charged")
    eq(gs.negative_weeks, 0, "within overdraft limit (20000)")
    gs.cash = -30000.0
    for i in 3:
        gs.advance_week()
    eq(gs.negative_weeks, 3, "three weeks below limit")
    ok(not gs.finished, "not yet game over")
    gs.advance_week()
    ok(gs.finished and not gs.won, "game over after 4 consecutive weeks below the overdraft limit")
    gs.free()
