class_name TaskRuntime
extends RefCounted
## Mutable per-task simulation state owned by SimState.

enum State { NOT_STARTED, READY, BLOCKED, ACTIVE, AWAITING_INSPECTION, REWORK, DONE, INSPECTED }

const STATE_NAMES: Array[String] = [
    "NOT_STARTED", "READY", "BLOCKED", "ACTIVE", "AWAITING_INSPECTION", "REWORK", "DONE", "INSPECTED",
]

var state: int = State.NOT_STARTED
## Crew-days of work done against `required`.
var progress: float = 0.0
var required: float = 0.0
var blocked_reason: String = ""
var actual_start_day: int = -1
var actual_finish_day: int = -1
var work_done_day: int = -1
## Absolute working day at the end of which the inspection is resolved
## (work_done_day + 2: the end of the day after next).
var inspection_due_day: int = -1
## Mid-day release marker: a task released by a predecessor finishing earlier the same day
## cannot start before that day (not saved).
var earliest_start: int = -1
var inspection_failures: int = 0
var rework_days_added: float = 0.0
var ordered: bool = false
var delivery_week: int = -1
var paid: bool = false


static func is_finished(s: int) -> bool:
    return s == State.DONE or s == State.INSPECTED


static func state_name(s: int) -> String:
    return STATE_NAMES[s]


func to_dict() -> Dictionary:
    return {
        "state": state, "progress": progress, "required": required,
        "asd": actual_start_day, "afd": actual_finish_day, "wdd": work_done_day,
        "idd": inspection_due_day, "if": inspection_failures, "rda": rework_days_added,
        "ordered": ordered, "dw": delivery_week, "paid": paid,
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
