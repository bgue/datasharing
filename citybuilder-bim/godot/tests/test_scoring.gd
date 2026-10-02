extends TC


func test_time_score() -> void:
    near(Scoring.time_score(6, 6), 1.0, "on time")
    near(Scoring.time_score(3, 6), 1.0, "early is capped at 1")
    near(Scoring.time_score(9, 6), 0.0, "50% late -> 0 (factor 2)")
    near(Scoring.time_score(7, 6), 1.0 - 1.0 / 6.0 * 2.0, "one week late")
    near(Scoring.time_score(20, 6), 0.0, "clamped at 0")


func test_other_components() -> void:
    near(Scoring.cost_score(90.0, 100.0), 1.0, "under budget")
    near(Scoring.cost_score(110.0, 100.0), 0.8, "10% over budget")
    near(Scoring.cost_score(200.0, 100.0), 0.0, "double budget")
    near(Scoring.safety_score(0, 3), 1.0, "no incidents")
    near(Scoring.safety_score(3, 3), 0.0, "max incidents")
    near(Scoring.quality_score(0, 0), 1.0, "no inspections yet")
    near(Scoring.quality_score(3, 4), 0.75, "first-pass rate")
    near(Scoring.stability_score(5), 0.75, "plan changes")


func test_grade_thresholds() -> void:
    var th: Dictionary = {"S": 90, "A": 75, "B": 60, "C": 40}
    eq(Scoring.grade_for(95.0, th), "S", "S")
    eq(Scoring.grade_for(90.0, th), "S", "S boundary")
    eq(Scoring.grade_for(89.9, th), "A", "A")
    eq(Scoring.grade_for(75.0, th), "A", "A boundary")
    eq(Scoring.grade_for(60.0, th), "B", "B")
    eq(Scoring.grade_for(40.0, th), "C", "C")
    eq(Scoring.grade_for(39.9, th), "D", "below C")


func test_total_from_weights() -> void:
    var gs: SimState = new_state()
    gs.week = 6
    gs.spent_total = 1000.0
    var r: Dictionary = Scoring.compute(gs, true)
    near(float(r["total"]), 100.0, "perfect run scores 100")
    eq(r["grade"], "S", "grade S")
    gs.week = 9
    r = Scoring.compute(gs, true)
    near(float(r["total"]), 100.0 - 35.0, "time component lost (weight 35)")
    eq(r["grade"], "B", "65 -> B")
    gs.incidents = 3
    gs.plan_changes = 20
    r = Scoring.compute(gs, true)
    near(float(r["total"]), 100.0 - 35.0 - 20.0 - 10.0, "safety and stability lost")
    gs.score_adjust = -5.0
    r = Scoring.compute(gs, true)
    near(float(r["total"]), 35.0 - 5.0, "event score_delta applied", 0.001)
    r = Scoring.compute(gs, false)
    eq(r["grade"], "F", "lost level -> F")
    gs.free()
