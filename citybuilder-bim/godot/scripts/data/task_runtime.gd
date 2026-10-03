class_name TaskRuntime
extends RefCounted
## Mutable per-task simulation state, as a thin view onto a TaskStore slot (see task_store.gd). Field names are the
## ones the UI, API and tests have always used; every access goes through the store's PackedArrays.

enum State { NOT_STARTED, READY, BLOCKED, ACTIVE, AWAITING_INSPECTION, REWORK, DONE, INSPECTED }

const STATE_NAMES: Array[String] = [
    "NOT_STARTED", "READY", "BLOCKED", "ACTIVE", "AWAITING_INSPECTION", "REWORK", "DONE", "INSPECTED",
]

var store: TaskStore = null
var idx: int = -1

var state: int:
    get: return store.state[idx]
    set(v): store.set_state(idx, v)
## Crew-days of work done against `required`.
var progress: float:
    get: return store.progress[idx]
    set(v): store.set_progress(idx, v)
var required: float:
    get: return store.required[idx]
    set(v): store.required[idx] = v
var blocked_reason: String:
    get:
        if store.gate_idx[idx] >= 0 and store.state[idx] == State.BLOCKED and store.gate_live.is_valid():
            return store.gate_live.call(idx, store.reason[idx])
        return store.reason[idx]
    set(v):
        store.gate_idx[idx] = -1
        store.set_reason(idx, v)
var actual_start_day: int:
    get: return store.actual_start_day[idx]
    set(v): store.actual_start_day[idx] = v
var actual_finish_day: int:
    get: return store.actual_finish_day[idx]
    set(v): store.actual_finish_day[idx] = v
var work_done_day: int:
    get: return store.work_done_day[idx]
    set(v): store.work_done_day[idx] = v
## Absolute working day at the end of which the inspection is resolved (work_done_day + 2).
var inspection_due_day: int:
    get: return store.inspection_due_day[idx]
    set(v): store.inspection_due_day[idx] = v
## Mid-day release marker: a task released by a predecessor finishing earlier the same day
## cannot start before that day (not saved).
var earliest_start: int:
    get: return store.earliest_start[idx]
    set(v): store.earliest_start[idx] = v
var inspection_failures: int:
    get: return store.inspection_failures[idx]
    set(v): store.inspection_failures[idx] = v
var rework_days_added: float:
    get: return store.rework_days_added[idx]
    set(v): store.rework_days_added[idx] = v
var ordered: bool:
    get: return (store.flags[idx] & TaskStore.F_ORDERED) != 0
    set(v): store.set_flag(idx, TaskStore.F_ORDERED, v)
var delivery_week: int:
    get: return store.delivery_week[idx]
    set(v): store.delivery_week[idx] = v
var paid: bool:
    get: return (store.flags[idx] & TaskStore.F_PAID) != 0
    set(v): store.set_flag(idx, TaskStore.F_PAID, v)
## Worked at least one day under double shift (adds to the inspection fail chance).
var worked_double: bool:
    get: return (store.flags[idx] & TaskStore.F_DOUBLE) != 0
    set(v): store.set_flag(idx, TaskStore.F_DOUBLE, v)


static func is_finished(s: int) -> bool:
    return s == State.DONE or s == State.INSPECTED


static func state_name(s: int) -> String:
    return STATE_NAMES[s]


func to_dict() -> Dictionary:
    return {
        "state": state, "progress": progress, "required": required,
        "asd": actual_start_day, "afd": actual_finish_day, "wdd": work_done_day,
        "idd": inspection_due_day, "if": inspection_failures, "rda": rework_days_added,
        "ordered": ordered, "dw": delivery_week, "paid": paid, "wd": worked_double,
    }


func from_dict(d: Dictionary) -> void:
    state = int(d.get("state", 0))
    progress = float(d.get("progress", 0.0))
    required = float(d.get("required", 0.0))
    actual_start_day = int(d.get("asd", -1))
    actual_finish_day = int(d.get("afd", -1))
    work_done_day = int(d.get("wdd", -1))
    inspection_due_day = int(d.get("idd", -1))
    inspection_failures = int(d.get("if", 0))
    rework_days_added = float(d.get("rda", 0.0))
    ordered = bool(d.get("ordered", false))
    delivery_week = int(d.get("dw", -1))
    paid = bool(d.get("paid", false))
    worked_double = bool(d.get("wd", false))
