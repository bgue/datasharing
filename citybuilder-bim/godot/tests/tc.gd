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
