class_name Inspections
extends RefCounted
## Hold-point inspections: resolved at the end of the day after next (work_done_day + 2), 10% base
## fail, rework = 25% of estimated crew days.

const BASE_FAIL_CHANCE: float = 0.10
const REWORK_FRACTION: float = 0.25


## Fail chance for a task: base (or the test override) plus the double-shift add if it was worked
## under double shift.
static func fail_chance(gs: SimState, rt: TaskRuntime = null) -> float:
    var base: float = BASE_FAIL_CHANCE if gs.inspection_fail_override < 0.0 else gs.inspection_fail_override
    if rt != null and rt.worked_double:
        base += gs.scenario.shift_inspection_fail_add
    return clampf(base, 0.0, 1.0)


## Resolves every inspection due at the end of working day `d` of the current week
## (due day = work_done_day + 2). Returns the number resolved.
static func run_day(gs: SimState, d: int) -> int:
    var end_of_day: int = gs.week * 5 + d + 1
    var resolved: int = 0
    for t in gs.bundle.tasks:
        var rt: TaskRuntime = gs.runtime[t.task_id]
        if rt.state != TaskRuntime.State.AWAITING_INSPECTION or rt.inspection_due_day > end_of_day:
            continue
        resolved += 1
        var first_attempt: bool = rt.inspection_failures == 0
        if gs.rng.randf() < fail_chance(gs, rt):
            fail(gs, t, rt)
            if first_attempt:
                gs.inspections_first_total += 1
        else:
            if first_attempt:
                gs.inspections_first_total += 1
                gs.inspections_first_pass += 1
            rt.actual_finish_day = maxi(rt.work_done_day, end_of_day)
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
