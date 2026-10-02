class_name Inspections
extends RefCounted
## Hold-point inspections: 1 week wait, 10% base fail, rework = 25% of estimated crew days.

const BASE_FAIL_CHANCE: float = 0.10
const REWORK_FRACTION: float = 0.25


static func fail_chance(gs: SimState) -> float:
    if gs.inspection_fail_override >= 0.0:
        return gs.inspection_fail_override
    return BASE_FAIL_CHANCE


## Resolves every inspection due this week. Returns number of tasks resolved.
static func run_week(gs: SimState) -> int:
    var resolved: int = 0
    for t in gs.bundle.tasks:
        var rt: TaskRuntime = gs.runtime[t.task_id]
        if rt.state != TaskRuntime.State.AWAITING_INSPECTION or rt.inspection_due_week > gs.week:
            continue
        resolved += 1
        var first_attempt: bool = rt.inspection_failures == 0
        if gs.rng.randf() < fail_chance(gs):
            fail(gs, t, rt)
            if first_attempt:
                gs.inspections_first_total += 1
        else:
            if first_attempt:
                gs.inspections_first_total += 1
                gs.inspections_first_pass += 1
            rt.actual_finish_day = maxi(rt.work_done_day, gs.week * 5)
            gs.set_task_state(t.task_id, TaskRuntime.State.INSPECTED)
            gs.log_event("Inspection passed: %s" % Readiness.pred_label(gs, t.task_id))
    return resolved


static func fail(gs: SimState, task: TaskData, rt: TaskRuntime) -> void:
    var extra: float = maxf(REWORK_FRACTION * task.estimated_crew_days, 0.01)
    rt.inspection_failures += 1
    rt.rework_days_added += extra
    rt.progress = maxf(0.0, rt.required - extra)
    gs.inspection_failures_total += 1
    gs.set_task_state(task.task_id, TaskRuntime.State.REWORK)
    gs.log_event("Inspection FAILED: %s (rework %.2f crew-days)" % [Readiness.pred_label(gs, task.task_id), extra])
