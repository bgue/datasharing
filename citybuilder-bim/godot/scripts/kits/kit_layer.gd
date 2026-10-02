class_name KitLayer
extends Node3D
## Draws the kit instances of a bundle (docs/06 Track B): one MeshInstance3D per instance, layer fills from
## task progress, rebuilt only when a layer fill moved by more than REBUILD_DELTA. Instances above the
## focus storey fade out, and instances farther than their lod_collapse_distance from the camera collapse
## to a tinted bounding box. BimView hands the elements `handles()` says yes to over to this node.
##
## World placement: 1 world unit = 1 grid cell = cell_size_m metres; meshes are built in metres, so each
## MeshInstance3D is scaled by 1 / cell_size_m and sits at the centre of its cell rectangle.

const REBUILD_DELTA: float = 0.02
const ABOVE_FOCUS_TRANSPARENCY: float = 0.93
const UPDATE_INTERVAL_S: float = 0.15
const LOD_INTERVAL_S: float = 0.3
const LOD_HYSTERESIS: float = 0.9
## Per-frame time budget (ms) for mesh rebuilds when update() runs from _process; the rest waits a frame.
const FRAME_BUDGET_MS: float = 12.0

var gs: SimState = null
var registry: KitRegistry = null
var kit_instances: KitInstances = null
var focus_storey_index: int = 0
var show_ghost: bool = true
var lod_enabled: bool = true
## Stats of the last update() call (tests / profiling).
var last_update_ms: float = 0.0
var last_rebuilds: int = 0
var total_rebuilds: int = 0

var _cell_m: float = 6.0
var _nodes: Array[Dictionary] = []  # per instance: {mi, fills, states, present, ghost, mode, overall}
var _dirty: bool = true
var _since_update: float = 0.0
var _since_lod: float = 0.0
var _progress: Callable = Callable()
var _task_state: Callable = Callable()
var _deadline_us: int = 0
var _deferred: bool = false


func setup(state: SimState, reg: KitRegistry = null) -> void:
    gs = state
    registry = reg if reg != null else KitRegistry.shared()
    _cell_m = gs.bundle.cell_size_m
    kit_instances = KitInstances.new(gs.bundle, registry)
    _progress = Callable(self, "_task_progress")
    _task_state = Callable(self, "_task_state_of")
    _create_nodes()
    gs.week_advanced.connect(func(_w: int) -> void: _dirty = true)
    gs.task_state_changed.connect(func(_t: String, _o: int, _n: int) -> void: _dirty = true)
    update()


## Crew-days done on a task, from the simulation runtime.
func _task_progress(t: TaskData) -> float:
    var rt: TaskRuntime = gs.runtime.get(t.task_id)
    if rt == null:
        return 0.0
    match rt.state:
        TaskRuntime.State.AWAITING_INSPECTION, TaskRuntime.State.DONE, TaskRuntime.State.INSPECTED:
            return t.estimated_crew_days
    return minf(rt.progress, t.estimated_crew_days)


func _task_state_of(t: TaskData) -> int:
    var rt: TaskRuntime = gs.runtime.get(t.task_id)
    return TaskRuntime.State.NOT_STARTED if rt == null else rt.state


func _create_nodes() -> void:
    for n in _nodes:
        (n["mi"] as Node).queue_free()
    _nodes.clear()
    for inst in kit_instances.instances():
        var mi := MeshInstance3D.new()
        mi.name = "Kit_%s_%d" % [inst["kit"], inst["id"]]
        mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
        var r: Rect2i = inst["rect"]
        var cx: float = float(r.position.x) + float(r.size.x - 1) * 0.5
        var cz: float = float(r.position.y) + float(r.size.y - 1) * 0.5
        mi.position = Vector3(cx, gs.bundle.storey_y_for_index(int(inst["storey_index"])), cz)
        mi.scale = Vector3.ONE / _cell_m
        add_child(mi)
        _nodes.append({"mi": mi, "fills": {}, "states": {}, "present": [], "ghost": true, "mode": "", "overall": -1.0})


# ------------------------------------------------------------------ public

func handles(guid: String) -> bool:
    return kit_instances != null and kit_instances.instance_of(guid) >= 0


func instance_count() -> int:
    return _nodes.size()


func instance_node(i: int) -> MeshInstance3D:
    return _nodes[i]["mi"]


func instance_mode(i: int) -> String:
    return str(_nodes[i]["mode"])


func set_focus_storey(idx: int) -> void:
    focus_storey_index = idx
    _apply_focus()


func set_ghost_visible(v: bool) -> void:
    show_ghost = v
    _dirty = true


func mark_dirty() -> void:
    _dirty = true


## Refreshes fills from the simulation and rebuilds the instances that changed. Returns the number of
## meshes rebuilt. `camera` drives the LOD collapse (the viewport camera when null).
func update(camera: Camera3D = null, refresh_progress: bool = true, budget_ms: float = 0.0) -> int:
    var t0: int = Time.get_ticks_usec()
    _deadline_us = t0 + int(budget_ms * 1000.0) if budget_ms > 0.0 else 0
    if refresh_progress:
        kit_instances.refresh(_progress, _task_state)
    var cam_pos: Vector3 = Vector3.ZERO
    var has_cam: bool = false
    if lod_enabled:
        var cam: Camera3D = camera
        if cam == null and is_inside_tree():
            cam = get_viewport().get_camera_3d()
        if cam != null:
            cam_pos = cam.global_position if cam.is_inside_tree() else cam.position
            has_cam = true
    var rebuilt: int = 0
    _deferred = false
    var list: Array[Dictionary] = kit_instances.instances()
    for i in list.size():
        if _sync(i, list[i], has_cam, cam_pos):
            rebuilt += 1
    _apply_focus()
    _dirty = _deferred or (not refresh_progress and _dirty)
    _deferred = false
    last_rebuilds = rebuilt
    total_rebuilds += rebuilt
    last_update_ms = float(Time.get_ticks_usec() - t0) / 1000.0
    return rebuilt


func _mesh_params(inst: Dictionary) -> Dictionary:
    var r: Rect2i = inst["rect"]
    return {
        "footprint_cells": r.size,
        "height_m": inst["height_m"],
        "cell_size_m": _cell_m,
        "layer_fills": inst["layer_fills"],
        "variant": inst["variant"],
        "seed": inst["seed"],
        "present_layers": inst["present_layers"],
        "layer_states": inst["layer_states"],
        "counts": inst["counts"],
        "show_ghost": show_ghost,
    }


func _sync(i: int, inst: Dictionary, has_cam: bool, cam_pos: Vector3) -> bool:
    var node: Dictionary = _nodes[i]
    var mi: MeshInstance3D = node["mi"]
    var mode: String = str(node["mode"])
    var want: String = "full"
    if has_cam:
        var d: float = cam_pos.distance_to(mi.position)
        var lod: float = float(inst["lod_distance"])
        if mode == "collapsed":
            want = "collapsed" if d > lod * LOD_HYSTERESIS else "full"
        else:
            want = "collapsed" if d > lod else "full"
    elif mode == "collapsed":
        want = "collapsed"
    if want == "collapsed":
        var ov: float = float(inst["overall_fill"])
        if mode != "collapsed" or absf(ov - float(node["overall"])) > 0.05:
            if _over_budget():
                return false
            mi.mesh = registry.collapsed_mesh(inst["kit"], _mesh_params(inst), ov)
            node["mode"] = "collapsed"
            node["overall"] = ov
            return true
        return false
    var fills: Dictionary = inst["layer_fills"]
    var changed: bool = mode != "full" or show_ghost != bool(node["ghost"])
    if not changed:
        changed = str(node["present"]) != str(inst["present_layers"]) or node["states"] != inst["layer_states"]
    if not changed:
        var built: Dictionary = node["fills"]
        for k in fills:
            var nf: float = float(fills[k])
            var bf: float = float(built.get(k, -1.0))
            if absf(nf - bf) > REBUILD_DELTA or (nf >= 1.0 and bf < 1.0) or (nf <= 0.0 and bf > 0.0):
                changed = true
                break
    if not changed:
        return false
    if _over_budget():
        return false
    mi.mesh = registry.build(inst["kit"], _mesh_params(inst))
    node["fills"] = fills.duplicate()
    node["states"] = (inst["layer_states"] as Dictionary).duplicate()
    node["present"] = (inst["present_layers"] as Array).duplicate()
    node["ghost"] = show_ghost
    node["mode"] = "full"
    node["overall"] = float(inst["overall_fill"])
    return true


## True (and the update is flagged to continue next frame) when the rebuild budget is used up.
func _over_budget() -> bool:
    if _deadline_us > 0 and Time.get_ticks_usec() > _deadline_us:
        _deferred = true
        return true
    return false


func _apply_focus() -> void:
    if kit_instances == null:
        return
    var list: Array[Dictionary] = kit_instances.instances()
    for i in list.size():
        var above: bool = int(list[i]["storey_index"]) > focus_storey_index
        (_nodes[i]["mi"] as MeshInstance3D).transparency = ABOVE_FOCUS_TRANSPARENCY if above else 0.0


func _process(delta: float) -> void:
    if kit_instances == null:
        return
    _since_update += delta
    _since_lod += delta
    if _dirty and _since_update >= UPDATE_INTERVAL_S:
        _since_update = 0.0
        _since_lod = 0.0
        update(null, true, FRAME_BUDGET_MS)
        if _dirty:
            _since_update = UPDATE_INTERVAL_S  # deferred rebuilds continue next frame
    elif lod_enabled and _since_lod >= LOD_INTERVAL_S:
        _since_lod = 0.0
        update(null, false, FRAME_BUDGET_MS)
