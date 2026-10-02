class_name Scoring
extends RefCounted
## Score (docs/01 section 7) and grade thresholds.


static func time_score(actual_weeks: int, contract_weeks: int) -> float:
    var cw: float = maxf(1.0, float(contract_weeks))
    return clampf(1.0 - (float(actual_weeks) - cw) / cw * 2.0, 0.0, 1.0)


static func cost_score(spent: float, budget: float) -> float:
    if budget <= 0.0:
        return 1.0
    return clampf(1.0 - maxf(0.0, spent - budget) / budget * 2.0, 0.0, 1.0)


static func safety_score(incidents: int, max_incidents: int) -> float:
    return clampf(1.0 - float(incidents) / float(maxi(1, max_incidents)), 0.0, 1.0)


static func quality_score(first_pass: int, first_total: int) -> float:
    if first_total <= 0:
        return 1.0
    return clampf(float(first_pass) / float(first_total), 0.0, 1.0)


static func stability_score(plan_changes: int) -> float:
    return clampf(1.0 - float(plan_changes) / 20.0, 0.0, 1.0)


## thresholds: {"S": 90, "A": 75, "B": 60, "C": 40}. Below C -> "D".
static func grade_for(total: float, thresholds: Dictionary) -> String:
    for g in ["S", "A", "B", "C"]:
        if thresholds.has(g) and total >= float(thresholds[g]):
            return g
    return "D"


## Full breakdown. `final` = true uses the finished week count; otherwise the current week.
static func compute(gs: SimState, won: bool = true) -> Dictionary:
    var sc: ScenarioData = gs.scenario
    var comps: Dictionary = {
        "time": time_score(gs.week, gs.bundle.contract_weeks()) if won else 0.0,
        "cost": cost_score(gs.spent_total, gs.bundle.contract_budget()),
        "safety": safety_score(gs.incidents, sc.max_incidents_hard),
        "quality": quality_score(gs.inspections_first_pass, gs.inspections_first_total),
        "stability": stability_score(gs.plan_changes),
    }
    var wsum: float = 0.0
    var acc: float = 0.0
    for k in comps:
        var w: float = float(sc.score_weights.get(k, 0.0))
        wsum += w
        acc += w * float(comps[k])
    var total: float = (acc / wsum * 100.0) if wsum > 0.0 else 0.0
    total = clampf(total + gs.score_adjust, 0.0, 100.0)
    var grade: String = grade_for(total, sc.grade_thresholds)
    if not won:
        grade = "F"
    return {"components": comps, "weights": sc.score_weights.duplicate(), "adjust": gs.score_adjust,
            "total": total, "grade": grade, "won": won,
            "double_shift_zone_weeks": gs.double_shift_zone_weeks}
