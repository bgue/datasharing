class_name TC
extends RefCounted
## Base class for test files: assertion helpers and bundle/state factories.

const MINIMAL_PATH: String = "res://scenarios/minimal/sequence.json"

var runner: Object = null


func set_runner(r: Object) -> void:
    runner = r


func ok(cond: bool, msg: String) -> void:
    runner.call("check", cond, msg)


func eq(actual: Variant, expected: Variant, msg: String) -> void:
    runner.call("check", actual == expected, "%s (expected %s, got %s)" % [msg, str(expected), str(actual)])


func near(actual: float, expected: float, msg: String, eps: float = 0.0001) -> void:
    runner.call("check", absf(actual - expected) <= eps, "%s (expected %s, got %s)" % [msg, str(expected), str(actual)])


func load_bundle() -> SequenceBundle:
    var b := SequenceBundle.load_from_path(MINIMAL_PATH)
    ok(b.valid, "minimal bundle valid: %s" % ", ".join(b.errors))
    return b


## A fresh simulation on the minimal bundle (not in the scene tree).
func new_state() -> SimState:
    var gs := SimState.new()
    gs.start(load_bundle())
    return gs


func state_of(gs: SimState, task_id: String) -> int:
    return (gs.runtime[task_id] as TaskRuntime).state


func set_finished(gs: SimState, task_id: String, inspected: bool = true) -> void:
    var rt: TaskRuntime = gs.runtime[task_id]
    rt.progress = rt.required
    rt.actual_start_day = 0
    rt.actual_finish_day = 0
    var t: TaskData = gs.bundle.tasks_by_id[task_id]
    gs.set_task_state(task_id, TaskRuntime.State.INSPECTED if (t.inspection and inspected) else TaskRuntime.State.DONE)


## Lay a haul road from the gate (0,2) to (1,2), next to the ground zone at x=2.
func build_road(gs: SimState) -> void:
    ok(gs.place_tile(Vector2i(0, 2), "haul_road"), "road at gate")
    ok(gs.place_tile(Vector2i(1, 2), "haul_road"), "road next to zone: " + gs.last_error)


# ---------------------------------------------------------------- bundle dictionary fixtures (manual / logic tests)

## Library steps the minimal bundle lacks: earthworks, piles, surveys, dewatering, lift study, slab formwork.
const FIXTURE_STEPS: Array = [
    {"id": "CIV-EARTH-CUT", "name": "Excavate to formation", "phase": "substructure", "trade": "concrete", "discipline": "civil",
            "quantity_basis": "volume_m3", "rate_per_crew_day": 20, "unit_cost": 50},
    {"id": "CIV-PILE-DRIVE", "name": "Drive piles", "phase": "substructure", "trade": "concrete", "discipline": "civil",
            "quantity_basis": "count", "rate_per_crew_day": 4, "unit_cost": 800},
    {"id": "GEN-SURVEY-ASBUILT", "name": "As-built survey", "phase": "substructure", "trade": "finishes", "discipline": "general",
            "quantity_basis": "count", "rate_per_crew_day": 1, "unit_cost": 100, "requires_access": false, "min_duration_days": 1},
    {"id": "GEN-SURVEY-SETOUT", "name": "Setting-out survey", "phase": "substructure", "trade": "finishes", "discipline": "general",
            "quantity_basis": "count", "rate_per_crew_day": 1, "unit_cost": 100, "requires_access": false, "min_duration_days": 2},
    {"id": "GEN-DEWATER-RUN", "name": "Dewatering", "phase": "substructure", "trade": "mechanical", "discipline": "civil",
            "quantity_basis": "count", "rate_per_crew_day": 1, "unit_cost": 200, "requires_access": false, "min_duration_days": 5},
    {"id": "GEN-LIFT-STUDY", "name": "Lifting study", "phase": "superstructure", "trade": "finishes", "discipline": "general",
            "quantity_basis": "count", "rate_per_crew_day": 1, "unit_cost": 100, "requires_access": false, "min_duration_days": 2},
    {"id": "GEN-HYDROTEST", "name": "Hydrotest", "phase": "commissioning", "trade": "commissioning", "discipline": "commissioning",
            "quantity_basis": "count", "rate_per_crew_day": 1, "unit_cost": 300, "requires_access": false, "min_duration_days": 3},
    {"id": "STR-SLAB-FORM", "name": "Form slab", "phase": "superstructure", "trade": "concrete", "discipline": "structure",
            "quantity_basis": "area_m2", "rate_per_crew_day": 40, "unit_cost": 20},
]


## A fresh dictionary of the minimal bundle (each call parses the file again).
func minimal_dict() -> Dictionary:
    var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(MINIMAL_PATH))
    return parsed as Dictionary


## Adds FIXTURE_STEPS to the dictionary's step library.
func add_fixture_steps(d: Dictionary) -> void:
    for st in FIXTURE_STEPS:
        (d["step_library"]["steps"] as Array).append((st as Dictionary).duplicate(true))


## Minimal bundle dictionary with the fixture steps and the given recipes embedded (`recipes[]`).
func fixture_dict(recipes: Array = []) -> Dictionary:
    var d: Dictionary = minimal_dict()
    add_fixture_steps(d)
    if not recipes.is_empty():
        d["recipes"] = recipes
    return d


## A running simulation on a bundle dictionary (cash topped up so staffing is never the limit).
func state_from_dict(d: Dictionary) -> SimState:
    var b := SequenceBundle.from_dictionary(d)
    ok(b.valid, "fixture bundle valid: %s" % ", ".join(b.errors))
    var gs := SimState.new()
    ok(gs.start(b), "fixture state starts")
    gs.cash = 1.0e7
    return gs


func fixture_state(recipes: Array = []) -> SimState:
    return state_from_dict(fixture_dict(recipes))
