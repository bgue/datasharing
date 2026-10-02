class_name PackageRuntime
extends RefCounted
## Mutable per-package simulation state owned by SimState.

var released: bool = true
var priority: int = 0
## Index of the card station the package belongs to (-1 = no card).
var station_index: int = -1
var crew_days_done: float = 0.0
var state: String = "waiting"
var blocked_reason: String = ""
var crews_now: int = 0


func to_dict() -> Dictionary:
    return {"r": released, "p": priority, "s": station_index}


func from_dict(d: Dictionary) -> void:
    released = bool(d.get("r", true))
    priority = int(d.get("p", 0))
    station_index = int(d.get("s", -1))
