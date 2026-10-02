class_name CellHeatOverlay
extends Node3D
## Per-cell progress heat overlay (WP-Q): a flat quad per cell, coloured by the done share of all (non-virtual) tasks
## whose cells include the cell: grey 0 % -> amber -> green 100 %, red tint when any of them is in rework. Cells of a
## zone that no task touches are drawn hatched and dim. The focused storey is drawn at full strength, the storeys
## below it faintly, the ones above it not at all. BimView owns the node (toggle key H, `set_heat_visible`).

const GREY: Color = Color(0.55, 0.55, 0.6)
const AMBER: Color = Color(0.98, 0.72, 0.12)
const GREEN: Color = Color(0.2, 0.8, 0.3)
const REWORK_TINT: Color = Color(0.95, 0.15, 0.1)
const REWORK_AMOUNT: float = 0.65
const FOCUS_ALPHA: float = 0.6
const OTHER_ALPHA: float = 0.16
const EMPTY_ALPHA: float = 0.35
const QUAD_SIZE: float = 0.92
const Y_OFFSET: float = 0.05

var gs: SimState = null
var heat_visible: bool = false
var focus_storey_index: int = 0
## Number of full recolours since the overlay was built (tests).
var refresh_count: int = 0

var _layers: Dictionary = {}  # storey_id -> {index, cells: Array[Vector2i], task_mmi, task_mat, empty_mmi, empty_mat}
var _cell_tasks: Dictionary = {}  # storey_id -> {Vector2i -> Array[TaskData]}
var _dirty: bool = true
var _hatch: ImageTexture = null
var _colours: Dictionary = {}  # storey_id -> Array[Color] written to the task quads (the headless renderer keeps none)


func setup(state: SimState) -> void:
    gs = state
    visible = false
    _build()
    gs.task_state_changed.connect(func(_a: String, _b: int, _c: int) -> void: _mark_dirty())
    gs.week_advanced.connect(func(_w: int) -> void: _mark_dirty())
    gs.tasks_changed.connect(func() -> void: _build())
    gs.level_started.connect(func() -> void: _build())


func set_heat_visible(v: bool) -> void:
    heat_visible = v
    visible = v
    if v:
        refresh()


func set_focus_storey(idx: int) -> void:
    focus_storey_index = idx
    _apply_alpha()


func _mark_dirty() -> void:
    _dirty = true
    if heat_visible and is_inside_tree():
        return  # refreshed in _process
    if heat_visible:
        refresh()


func _process(_delta: float) -> void:
    if _dirty and heat_visible:
        refresh()


# ------------------------------------------------------------------ data

## Crew-days done on a task (finished tasks count in full), from the simulation runtime.
static func task_done(state: SimState, t: TaskData) -> float:
    var rt: TaskRuntime = state.runtime.get(t.task_id, null)
    if rt == null:
        return 0.0
    match rt.state:
        TaskRuntime.State.AWAITING_INSPECTION, TaskRuntime.State.DONE, TaskRuntime.State.INSPECTED:
            return t.estimated_crew_days
    return minf(rt.progress, t.estimated_crew_days)


## storey_id -> {cell -> Array[TaskData]} over the non-virtual tasks of the bundle.
static func cell_task_map(bundle: SequenceBundle) -> Dictionary:
    var out: Dictionary = {}
    for t in bundle.tasks:
        var task: TaskData = t
        if task.is_virtual:
            continue
        if not out.has(task.storey_id):
            out[task.storey_id] = {}
        var per: Dictionary = out[task.storey_id]
        for c in task.cells:
            if not per.has(c):
                per[c] = [] as Array
            (per[c] as Array).append(task)
    return out


## Per-cell progress of a storey: {Vector2i -> {share, done, total, tasks, rework}} for every cell a task touches.
## `share` is crew-days done / estimated crew-days over those tasks. Pass a prebuilt `cell_tasks` (storey map) to skip
## the scan.
static func shares(state: SimState, storey_id: String, cell_tasks: Dictionary = {}) -> Dictionary:
    var per: Dictionary = cell_tasks
    if per.is_empty():
        per = cell_task_map(state.bundle).get(storey_id, {})
    var per_task: Dictionary = {}
    var out: Dictionary = {}
    for c in per:
        var done: float = 0.0
        var total: float = 0.0
        var rework: bool = false
        var list: Array = per[c]
        for t in list:
            var task: TaskData = t
            var tv: Variant = per_task.get(task.task_id, null)
            if tv == null:
                var rt: TaskRuntime = state.runtime.get(task.task_id, null)
                tv = [task_done(state, task), maxf(task.estimated_crew_days, 0.01),
                        rt != null and rt.state == TaskRuntime.State.REWORK]
                per_task[task.task_id] = tv
            done += float(tv[0])
            total += float(tv[1])
            rework = rework or bool(tv[2])
        out[c] = {"share": clampf(done / total, 0.0, 1.0) if total > 0.0 else 0.0, "done": done, "total": total,
                "tasks": list.size(), "rework": rework}
    return out


## Heat colour (alpha 1) of a share: grey -> amber at 50 % -> green, red tint on rework.
static func heat_colour(share: float, rework: bool = false) -> Color:
    var s: float = clampf(share, 0.0, 1.0)
    var col: Color = GREY.lerp(AMBER, s * 2.0) if s < 0.5 else AMBER.lerp(GREEN, (s - 0.5) * 2.0)
    if rework:
        col = col.lerp(REWORK_TINT, REWORK_AMOUNT)
    return col


## Colour of one cell as drawn (before the storey alpha); Color(0,0,0,0) for cells no task touches.
func cell_colour(storey_id: String, cell: Vector2i) -> Color:
    var per: Dictionary = _cell_tasks.get(storey_id, {})
    if not per.has(cell):
        return Color(0, 0, 0, 0)
    var d: Dictionary = shares(gs, storey_id, {cell: per[cell]})[cell]
    return heat_colour(float(d["share"]), bool(d["rework"]))


func layer_count() -> int:
    return _layers.size()


## Number of coloured (task) cells and hatched (task-less) cells on a storey.
func cell_counts(storey_id: String) -> Vector2i:
    if not _layers.has(storey_id):
        return Vector2i.ZERO
    var l: Dictionary = _layers[storey_id]
    return Vector2i((l["cells"] as Array).size(), (l["empty_cells"] as Array).size())


## Colour written to quad `i` (sorted cell order) of a storey's task layer; grey before the first refresh.
func quad_colour(storey_id: String, i: int) -> Color:
    var cols: Array = _colours.get(storey_id, [])
    return cols[i] if i < cols.size() else GREY


func layer_node(storey_id: String) -> MultiMeshInstance3D:
    return (_layers[storey_id] as Dictionary)["task_mmi"] if _layers.has(storey_id) else null


func empty_node(storey_id: String) -> MultiMeshInstance3D:
    return (_layers[storey_id] as Dictionary)["empty_mmi"] if _layers.has(storey_id) else null


# ------------------------------------------------------------------ nodes

func _hatch_texture() -> ImageTexture:
    if _hatch == null:
        var img := Image.create(16, 16, false, Image.FORMAT_RGBA8)
        for y in 16:
            for x in 16:
                img.set_pixel(x, y, Color(1, 1, 1, 1.0 if (x + y) % 8 < 2 else 0.0))
        _hatch = ImageTexture.create_from_image(img)
    return _hatch


func _make_layer(storey_id: String, index: int, cells: Array, empty: Array) -> Dictionary:
    var y: float = gs.bundle.storey_y(storey_id) + Y_OFFSET
    var out: Dictionary = {"index": index, "cells": cells, "empty_cells": empty}
    for which in ["task", "empty"]:
        var list: Array = cells if which == "task" else empty
        var quad := QuadMesh.new()
        quad.size = Vector2(QUAD_SIZE, QUAD_SIZE)
        quad.orientation = PlaneMesh.FACE_Y
        var mat := StandardMaterial3D.new()
        mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
        mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
        mat.cull_mode = BaseMaterial3D.CULL_DISABLED
        if which == "task":
            mat.vertex_color_use_as_albedo = true
        else:
            mat.albedo_texture = _hatch_texture()
            mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
            mat.albedo_color = Color(0.78, 0.8, 0.85, EMPTY_ALPHA)
        quad.material = mat
        var mm := MultiMesh.new()
        mm.transform_format = MultiMesh.TRANSFORM_3D
        mm.use_colors = which == "task"
        mm.mesh = quad
        mm.instance_count = list.size()
        for i in list.size():
            var c: Vector2i = list[i]
            mm.set_instance_transform(i, Transform3D(Basis(), Vector3(c.x, y, c.y)))
        var mmi := MultiMeshInstance3D.new()
        mmi.name = "Heat%s_%s" % [which.capitalize(), storey_id]
        mmi.multimesh = mm
        mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
        add_child(mmi)
        out["%s_mmi" % which] = mmi
        out["%s_mat" % which] = mat
    return out


func _build() -> void:
    for k in _layers:
        ((_layers[k] as Dictionary)["task_mmi"] as Node).queue_free()
        ((_layers[k] as Dictionary)["empty_mmi"] as Node).queue_free()
    _layers.clear()
    _cell_tasks.clear()
    _colours.clear()
    if gs == null or gs.bundle == null:
        return
    _cell_tasks = cell_task_map(gs.bundle)
    var zone_cells: Dictionary = {}  # storey_id -> {cell -> true}
    for z in gs.bundle.zones:
        if not zone_cells.has(z.storey_id):
            zone_cells[z.storey_id] = {}
        for c in z.cells:
            zone_cells[z.storey_id][c] = true
    for s in gs.bundle.storeys:
        var per: Dictionary = _cell_tasks.get(s.id, {})
        var cells: Array = per.keys()
        cells.sort_custom(func(a: Vector2i, b: Vector2i) -> bool: return a.y < b.y or (a.y == b.y and a.x < b.x))
        var empty: Array = []
        for c in (zone_cells.get(s.id, {}) as Dictionary):
            if not per.has(c):
                empty.append(c)
        empty.sort_custom(func(a: Vector2i, b: Vector2i) -> bool: return a.y < b.y or (a.y == b.y and a.x < b.x))
        _layers[s.id] = _make_layer(s.id, s.index, cells, empty)
    _dirty = true
    _apply_alpha()
    if heat_visible:
        refresh()


func _storey_alpha(index: int) -> float:
    if index == focus_storey_index:
        return FOCUS_ALPHA
    return OTHER_ALPHA if index < focus_storey_index else 0.0


func _apply_alpha() -> void:
    for k in _layers:
        var l: Dictionary = _layers[k]
        var a: float = _storey_alpha(int(l["index"]))
        var tm: StandardMaterial3D = l["task_mat"]
        tm.albedo_color = Color(1, 1, 1, a)
        var em: StandardMaterial3D = l["empty_mat"]
        em.albedo_color = Color(0.78, 0.8, 0.85, EMPTY_ALPHA * (a / FOCUS_ALPHA))
        (l["task_mmi"] as Node3D).visible = a > 0.0
        (l["empty_mmi"] as Node3D).visible = a > 0.0


## Recolours every cell from the simulation.
func refresh() -> void:
    if gs == null or gs.bundle == null:
        return
    for k in _layers:
        var l: Dictionary = _layers[k]
        var cells: Array = l["cells"]
        var mm: MultiMesh = (l["task_mmi"] as MultiMeshInstance3D).multimesh
        var sh: Dictionary = shares(gs, str(k), _cell_tasks.get(k, {}))
        var cols: Array[Color] = []
        for i in cells.size():
            var d: Dictionary = sh[cells[i]]
            var col: Color = heat_colour(float(d["share"]), bool(d["rework"]))
            cols.append(col)
            mm.set_instance_color(i, col)
        _colours[k] = cols
    _dirty = false
    refresh_count += 1
