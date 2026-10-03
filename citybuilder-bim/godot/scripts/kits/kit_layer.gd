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
const FRAME_BUDGET_MS: float = 8.0

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
var selected_index: int = -1
var _sel_mi: MeshInstance3D = null
var _sel_mat: StandardMaterial3D = null
var _deadline_us: int = 0
var _deferred: bool = false
## Instances beyond this count are not built in setup(): the first meshes are built from _process under the frame budget,
## the ones in view first (the first build of a big model must not freeze the game).
const SYNC_BUILD_MAX: int = 300
## Bounding spheres of the instances (frustum culling) and the instances of each task (incremental fill refresh).
var _sph_c: PackedVector3Array = PackedVector3Array()
var _sph_r: PackedFloat32Array = PackedFloat32Array()
var _task_inst: Dictionary = {}  # task id -> Array[int] of instance indices
var _log_cursor: int = -1
var _log_epoch: int = -1
## Culling stats of the last update(): instances inside the frustum / hidden.
var last_visible: int = 0
var last_culled: int = 0
var _first_pending: bool = false
## Instances to look at on the next update when the camera did not move: only those whose fills changed (the others keep
## their mesh, mode and visibility); a camera move, a focus / ghost / LOD change or the first update looks at all of them.
var _dirty_inst: Dictionary = {}
var _all_inst_dirty: bool = true
var _has_last_cam: bool = false
var _last_cam_xf: Transform3D = Transform3D()
var _last_cam_fov: float = -1.0
var _focus_applied: int = -99999
var _lod_applied: bool = true


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
    if _nodes.size() <= SYNC_BUILD_MAX:
        update()
    else:
        kit_instances.refresh(_progress, _task_state)
        _log_cursor = gs.runtime.log_end()
        _log_epoch = gs.runtime.log_epoch
        _first_pending = true
        _dirty = true


## Crew-days done on a task, from the simulation runtime.
func _task_progress(t: TaskData) -> float:
    var rt: TaskRuntime = gs.runtime.get_rt(t.task_id)
    if rt == null:
        return 0.0
    match rt.state:
        TaskRuntime.State.AWAITING_INSPECTION, TaskRuntime.State.DONE, TaskRuntime.State.INSPECTED:
            return t.estimated_crew_days
    return minf(rt.progress, t.estimated_crew_days)


func _task_state_of(t: TaskData) -> int:
    var rt: TaskRuntime = gs.runtime.get_rt(t.task_id)
    return TaskRuntime.State.NOT_STARTED if rt == null else rt.state


func _create_nodes() -> void:
    for n in _nodes:
        (n["mi"] as Node).queue_free()
    _nodes.clear()
    _sph_c = PackedVector3Array()
    _sph_r = PackedFloat32Array()
    _task_inst.clear()
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
        var bb: AABB = instance_aabb(int(inst["id"]))
        _sph_c.append(bb.position + bb.size * 0.5)
        _sph_r.append(bb.size.length() * 0.5)


# ------------------------------------------------------------------ public

func handles(guid: String) -> bool:
    return kit_instances != null and kit_instances.instance_of(guid) >= 0


func instance_count() -> int:
    return _nodes.size()


func instance_node(i: int) -> MeshInstance3D:
    return _nodes[i]["mi"]


func instance_mode(i: int) -> String:
    return str(_nodes[i]["mode"])


## World-space box (grid units) of an instance: its cell rectangle on the storey plane up to its height.
func instance_aabb(i: int) -> AABB:
    var inst: Dictionary = kit_instances.instances()[i]
    var r: Rect2i = inst["rect"]
    var y: float = gs.bundle.storey_y_for_index(int(inst["storey_index"]))
    return AABB(Vector3(float(r.position.x) - 0.5, y, float(r.position.y) - 0.5),
            Vector3(float(r.size.x), float(inst["height_m"]) / _cell_m, float(r.size.y)))


## Outlines instance `i` (-1 clears): a yellow box drawn on top of the scene.
func set_selected(i: int) -> void:
    selected_index = i
    if _sel_mi == null:
        _sel_mi = MeshInstance3D.new()
        _sel_mi.name = "Selection"
        _sel_mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
        var m := StandardMaterial3D.new()
        m.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
        m.albedo_color = Color(1.0, 0.85, 0.2)
        m.no_depth_test = true
        m.render_priority = 10
        _sel_mat = m
        add_child(_sel_mi)
    if i < 0 or i >= _nodes.size():
        _sel_mi.visible = false
        return
    var bb: AABB = instance_aabb(i)
    var im := ImmediateMesh.new()
    im.surface_begin(Mesh.PRIMITIVE_LINES, _sel_mat)
    var c: Array[Vector3] = []
    for k in 8:
        c.append(bb.position + Vector3(bb.size.x * float(k & 1), bb.size.y * float((k >> 1) & 1), bb.size.z * float((k >> 2) & 1)))
    for e in [[0, 1], [2, 3], [4, 5], [6, 7], [0, 2], [1, 3], [4, 6], [5, 7], [0, 4], [1, 5], [2, 6], [3, 7]]:
        im.surface_add_vertex(c[e[0]])
        im.surface_add_vertex(c[e[1]])
    im.surface_end()
    _sel_mi.mesh = im
    _sel_mi.visible = true


func set_focus_storey(idx: int) -> void:
    focus_storey_index = idx
    _apply_focus()


func set_ghost_visible(v: bool) -> void:
    show_ghost = v
    _all_inst_dirty = true
    _dirty = true


func mark_dirty() -> void:
    _all_inst_dirty = true
    _dirty = true


## Refreshes fills from the simulation and rebuilds the instances that changed. Returns the number of
## meshes rebuilt. `camera` drives the LOD collapse (the viewport camera when null).
func update(camera: Camera3D = null, refresh_progress: bool = true, budget_ms: float = 0.0) -> int:
    var t0: int = Time.get_ticks_usec()
    _deadline_us = t0 + int(budget_ms * 1000.0) if budget_ms > 0.0 else 0
    if refresh_progress:
        _refresh_fills()
    var cam_pos: Vector3 = Vector3.ZERO
    var has_cam: bool = false
    var planes: Array = []
    var cam: Camera3D = camera
    if cam == null and is_inside_tree():
        cam = get_viewport().get_camera_3d()
    if cam != null:
        has_cam = true
        cam_pos = cam.global_position if cam.is_inside_tree() else cam.position
        if cam.is_inside_tree():
            planes = cam.get_frustum()
    var rebuilt: int = 0
    _deferred = false
    var list: Array[Dictionary] = kit_instances.instances()
    var visible_n: int = 0
    var cam_changed: bool = false
    if has_cam:
        var xf: Transform3D = cam.global_transform if cam.is_inside_tree() else cam.transform
        cam_changed = not _has_last_cam or not xf.is_equal_approx(_last_cam_xf) or not is_equal_approx(cam.fov, _last_cam_fov)
        _last_cam_xf = xf
        _last_cam_fov = cam.fov
    _has_last_cam = has_cam
    if lod_enabled != _lod_applied:
        _lod_applied = lod_enabled
        _all_inst_dirty = true
    var full_pass: bool = _all_inst_dirty or cam_changed or not has_cam
    var todo: Array = []
    if full_pass:
        todo = range(list.size())
    else:
        todo = _dirty_inst.keys()
        todo.sort()
    for i in todo:
        var mi: MeshInstance3D = _nodes[i]["mi"]
        var in_view: bool = true
        if not planes.is_empty():  # frustum culling: instances outside it keep their mesh but are not drawn or rebuilt
            in_view = _in_frustum(i, planes)
        if bool(list[i].get("hidden", false)):  # active-only equipment kits (piling rig) while idle
            in_view = false
        if mi.visible != in_view:
            mi.visible = in_view
        if in_view:
            visible_n += 1
        if _sync(i, list[i], has_cam and lod_enabled, cam_pos, not in_view):
            rebuilt += 1
        if _deferred:
            _dirty_inst[i] = true  # the frame budget ran out: it is looked at again next time
        else:
            _dirty_inst.erase(i)
    if full_pass:
        _all_inst_dirty = false  # what the budget skipped is in _dirty_inst
        last_visible = visible_n
    last_culled = list.size() - last_visible
    if focus_storey_index != _focus_applied:
        _apply_focus()
    _dirty = _deferred or (not refresh_progress and _dirty)
    _deferred = false
    if _first_pending and not _dirty:
        _first_pending = false
    last_rebuilds = rebuilt
    total_rebuilds += rebuilt
    last_update_ms = float(Time.get_ticks_usec() - t0) / 1000.0
    return rebuilt


## Sphere test of an instance against the camera frustum planes (outward normals).
func _in_frustum(i: int, planes: Array) -> bool:
    var c: Vector3 = _sph_c[i]
    var r: float = _sph_r[i]
    for p in planes:
        if (p as Plane).distance_to(c) > r:
            return false
    return true


## Recomputes the layer fills of the instances whose tasks changed since the last call (the TaskStore change log),
## all of them for the first call or when the log was cut.
func _refresh_fills() -> void:
    var st: TaskStore = gs.runtime
    var changed: Variant = null
    if _log_cursor >= 0 and st.log_epoch == _log_epoch:
        changed = st.changed_set_since(_log_cursor)
    _log_cursor = st.log_end()
    _log_epoch = st.log_epoch
    if changed == null:
        kit_instances.refresh(_progress, _task_state)
        _all_inst_dirty = true
        return
    if _task_inst.is_empty():
        var list: Array[Dictionary] = kit_instances.instances()
        for i in list.size():
            for t in kit_instances.tasks_of(i):
                var tid: String = (t as TaskData).task_id
                if not _task_inst.has(tid):
                    _task_inst[tid] = [] as Array[int]
                (_task_inst[tid] as Array[int]).append(i)
    var dirty: Dictionary = {}
    for slot in (changed as Dictionary):
        var task: TaskData = st.task_refs[slot]
        if task != null and _task_inst.has(task.task_id):
            for i in (_task_inst[task.task_id] as Array[int]):
                dirty[i] = true
    for i in dirty:
        kit_instances.refresh_one(i, _progress, _task_state)
        _dirty_inst[i] = true


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


## `culled`: outside the frustum. The LOD mode still follows the distance (cheap boxes), but a full mesh that is out of
## view is not rebuilt for changed fills: it catches up when it comes back into view.
func _sync(i: int, inst: Dictionary, has_cam: bool, cam_pos: Vector3, culled: bool = false) -> bool:
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
    if culled and mode == "full":
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
    _focus_applied = focus_storey_index
    var list: Array[Dictionary] = kit_instances.instances()
    for i in list.size():
        var above: bool = int(list[i]["storey_index"]) > focus_storey_index
        (_nodes[i]["mi"] as MeshInstance3D).transparency = ABOVE_FOCUS_TRANSPARENCY if above else 0.0


func _process(delta: float) -> void:
    if kit_instances == null:
        return
    if _first_pending:
        update(null, false, FRAME_BUDGET_MS)
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
