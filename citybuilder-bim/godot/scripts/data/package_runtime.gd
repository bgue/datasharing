class_name PackageRuntime
extends RefCounted
## Mutable per-package simulation state owned by SimState.

## Bound by SimState: changes of `released`, `frozen` and `crews_now` mark the package for the next
## Packages.refresh_states (TaskStore.touched_pkgs).
var store: TaskStore = null
var pid: String = ""
var released: bool = true:
    set(v):
        if v != released:
            released = v
            _touch()
## Bumped on every priority write: caches of the priority-sorted package lists compare it.
static var priority_gen: int = 0
var priority: int = 0:
    set(v):
        priority = v
        priority_gen += 1
## Index of the card station the package belongs to (-1 = no card).
var station_index: int = -1
var crew_days_done: float = 0.0
var state: String = "waiting"
var blocked_reason: String = ""
var crews_now: int = 0:
    set(v):
        if v != crews_now:
            crews_now = v
            _touch()
## Frozen by the zone's manual mode: no work is started or continued, `released` was set to false.
var frozen: bool = false:
    set(v):
        if v != frozen:
            frozen = v
            _touch()
## `released` before the manual mode froze the package (restored when the mode is switched off).
var released_before_freeze: bool = true

func bind(task_store: TaskStore, package_id: String) -> void:
    store = task_store
    pid = package_id


func _touch() -> void:
    if store != null:
        store.touched_pkgs[pid] = true
        store.work_cache.erase(pid)



func to_dict() -> Dictionary:
    return {"r": released, "p": priority, "s": station_index, "fz": frozen, "rbf": released_before_freeze}


func from_dict(d: Dictionary) -> void:
    released = bool(d.get("r", true))
    priority = int(d.get("p", 0))
    station_index = int(d.get("s", -1))
    frozen = bool(d.get("fz", false))
    released_before_freeze = bool(d.get("rbf", true))
