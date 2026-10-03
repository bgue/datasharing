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
## Cells per chunk edge; chunks match BimView's, so the culling of the view decides which quads exist and show.
const CHUNK_CELLS: int = 8
## Per-frame budget (ms) of the incremental update when it runs from _process.
const FRAME_BUDGET_MS: float = 4.0

# The data is built the first time the overlay is shown or queried (it walks every cell of every task) and then kept
# current from the TaskStore change log: only the cells of the tasks that changed are touched, and only the chunks
# that are drawn are recoloured.
var _built: bool = false
var _layers: Dictionary = {}  # storey_id -> {index, root, cells (sorted), empty_cells (sorted), cid, task_mat, empty_mat}
var _layer_order: Array[String] = []
var _chunks: Array[Dictionary] = []  # {storey_id, key, cells_idx, empty_idx, task_mmi, empty_mmi, dirty}
var _chunk_by_key: Dictionary = {}  # "storey|chunk key" -> chunk index
var _near: Dictionary = {}  # chunk key (BimView.chunk_key) -> bool: the chunk is drawn in full (not culled, not far)
var _h_done: PackedFloat64Array = PackedFloat64Array()
var _h_total: PackedFloat64Array = PackedFloat64Array()
var _h_rework: PackedInt32Array = PackedInt32Array()
var _h_chunk: PackedInt32Array = PackedInt32Array()  # global cell id -> chunk
var _h_slot: PackedInt32Array = PackedInt32Array()  # global cell id -> quad index in the chunk
var _t_cells: Array = []  # task slot -> PackedInt32Array of global cell ids
var _t_done: PackedFloat64Array = PackedFloat64Array()  # contribution of the task to its cells as last applied
var _t_rw: PackedByteArray = PackedByteArray()
var _pending: Array[int] = []  # task slots waiting to be applied
var _cursor: int = 0
var _epoch: int = -1
var _dirty: bool = true
var _hatch: ImageTexture = null
var _y_offset_seen: float = 0.0


func setup(state: SimState) -> void:
    gs = state
    visible = false
    gs.task_state_changed.connect(func(_a: String, _b: int, _c: int) -> void: _mark_dirty())
    gs.week_advanced.connect(func(_w: int) -> void: _mark_dirty())
    gs.tasks_changed.connect(func() -> void: _invalidate())
    gs.level_started.connect(func() -> void: _invalidate())


func set_heat_visible(v: bool) -> void:
    heat_visible = v
    visible = v
    if v:
        refresh()


func set_focus_storey(idx: int) -> void:
    focus_storey_index = idx
    if _built:
        _apply_alpha()


func _mark_dirty() -> void:
    _dirty = true
    if heat_visible and not is_inside_tree():
        refresh()  # no _process without a tree (tests)


## Drops the built data (the task list changed): the next use rebuilds it.
func _invalidate() -> void:
    for k in _layers:
        ((_layers[k] as Dictionary)["root"] as Node).queue_free()
    _layers.clear()
    _layer_order.clear()
    _chunks.clear()
    _chunk_by_key.clear()
    _t_cells = []
    _pending.clear()
    _built = false
    _dirty = true
    if heat_visible:
        refresh()


func _process(_delta: float) -> void:
    if heat_visible and (_dirty or not _pending.is_empty()):
        refresh(FRAME_BUDGET_MS)


# ------------------------------------------------------------------ data

## Crew-days done on a task (finished tasks count in full), from the simulation runtime.
static func task_done(state: SimState, t: TaskData) -> float:
    var rt: TaskRuntime = state.runtime.get_rt(t.task_id)
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
                var rt: TaskRuntime = state.runtime.get_rt(task.task_id)
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


# ------------------------------------------------------------------ nodes

func _hatch_texture() -> ImageTexture:
    if _hatch == null:
        var img := Image.create(16, 16, false, Image.FORMAT_RGBA8)
        for y in 16:
            for x in 16:
                img.set_pixel(x, y, Color(1, 1, 1, 1.0 if (x + y) % 8 < 2 else 0.0))
        _hatch = ImageTexture.create_from_image(img)
    return _hatch


func _cell_less(a: Vector2i, b: Vector2i) -> bool:
    return a.y < b.y or (a.y == b.y and a.x < b.x)


## Builds the cell data of every storey: the cells tasks touch (sorted, with their ids), the zone cells no task touches,
## the chunks they fall in and the cells of every non-virtual task. No nodes yet (see _ensure_chunk).
func _ensure_built() -> void:
    if _built or gs == null or gs.bundle == null:
        return
    _built = true
    var b: SequenceBundle = gs.bundle
    var st: TaskStore = gs.runtime
    _h_done = PackedFloat64Array()
    _h_total = PackedFloat64Array()
    _h_rework = PackedInt32Array()
    _t_cells = []
    _t_cells.resize(st.slot_count())
    _t_done = PackedFloat64Array()
    _t_done.resize(st.slot_count())
    _t_rw = PackedByteArray()
    _t_rw.resize(st.slot_count())
    var cell_id: Dictionary = {}  # BimView.cell_key -> global id
    var per_storey: Dictionary = {}  # storey_id -> Array of [cell, id]
    var task_cells: Dictionary = {}
    for sid in b.storeys:
        per_storey[sid.id] = []
    for t in b.tasks:
        if t.is_virtual:
            continue
        var slot: int = st.idx_of(t.task_id)
        if slot < 0:
            continue
        var sidx: int = int(b.storey_index_by_id.get(t.storey_id, 0))
        var ids := PackedInt32Array()
        var est: float = maxf(t.estimated_crew_days, 0.01)
        for c in t.cells:
            var key: int = BimView.cell_key(sidx, c.x, c.y)
            var cid: int = int(cell_id.get(key, -1))
            if cid < 0:
                cid = _h_total.size()
                cell_id[key] = cid
                _h_done.append(0.0)
                _h_total.append(0.0)
                _h_rework.append(0)
                if not per_storey.has(t.storey_id):
                    per_storey[t.storey_id] = []
                (per_storey[t.storey_id] as Array).append([c, cid])
            _h_total[cid] += est
            ids.append(cid)
        _t_cells[slot] = ids
    var zone_cells: Dictionary = {}  # storey_id -> {cell -> true}
    for z in b.zones:
        if not zone_cells.has(z.storey_id):
            zone_cells[z.storey_id] = {}
        for c in z.cells:
            zone_cells[z.storey_id][c] = true
    _h_chunk = PackedInt32Array()
    _h_chunk.resize(_h_total.size())
    _h_slot = PackedInt32Array()
    _h_slot.resize(_h_total.size())
    for s in b.storeys:
        var entries: Array = per_storey.get(s.id, [])
        entries.sort_custom(func(a: Array, c: Array) -> bool: return _cell_less(a[0], c[0]))
        var cells: Array = []
        var cids: Array = []
        var present: Dictionary = {}
        for e in entries:
            cells.append(e[0])
            cids.append(e[1])
            present[e[0]] = true
        var empty: Array = []
        for c in (zone_cells.get(s.id, {}) as Dictionary):
            if not present.has(c):
                empty.append(c)
        empty.sort_custom(_cell_less)
        var layer: Dictionary = {"index": s.index, "root": null, "cells": cells, "empty_cells": empty, "cid": cids,
                "task_mat": null, "empty_mat": null}
        layer["root"] = _make_layer_root(s.id, layer)
        _layers[s.id] = layer
        _layer_order.append(s.id)
        for i in cells.size():
            var cell: Vector2i = cells[i]
            var ci: int = _chunk_of(s.id, s.index, cell)
            var ch: Dictionary = _chunks[ci]
            _h_chunk[cids[i]] = ci
            _h_slot[cids[i]] = (ch["cells_idx"] as Array[int]).size()
            (ch["cells_idx"] as Array[int]).append(i)
        for i in empty.size():
            var ci2: int = _chunk_of(s.id, s.index, empty[i])
            (_chunks[ci2]["empty_idx"] as Array[int]).append(i)
    _apply_alpha()
    for slot2 in st.slot_count():  # initial contributions
        if _t_cells[slot2] != null:
            _apply_task(slot2)
    _cursor = st.log_end()
    _epoch = st.log_epoch
    _pending.clear()
    for ch2 in _chunks:
        ch2["dirty"] = true


func _chunk_of(storey_id: String, storey_idx: int, cell: Vector2i) -> int:
    var key: int = BimView.chunk_key(storey_idx, cell.x >> 3, cell.y >> 3)
    var k: String = "%s|%d" % [storey_id, key]
    var ci: int = int(_chunk_by_key.get(k, -1))
    if ci >= 0:
        return ci
    ci = _chunks.size()
    _chunk_by_key[k] = ci
    _chunks.append({"storey_id": storey_id, "key": key, "cells_idx": [] as Array[int], "empty_idx": [] as Array[int],
            "task_mmi": null, "empty_mmi": null, "dirty": true})
    return ci


func _make_layer_root(storey_id: String, layer: Dictionary) -> Node3D:
    var root := Node3D.new()
    root.name = "HeatLayer_%s" % storey_id
    add_child(root)
    var tm := StandardMaterial3D.new()
    tm.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
    tm.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
    tm.cull_mode = BaseMaterial3D.CULL_DISABLED
    tm.vertex_color_use_as_albedo = true
    var em := StandardMaterial3D.new()
    em.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
    em.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
    em.cull_mode = BaseMaterial3D.CULL_DISABLED
    em.albedo_texture = _hatch_texture()
    em.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
    em.albedo_color = Color(0.78, 0.8, 0.85, EMPTY_ALPHA)
    layer["task_mat"] = tm
    layer["empty_mat"] = em
    return root


## Nodes of a chunk (one MultiMesh of coloured quads, one of hatched quads), created when the chunk is first drawn.
func _ensure_chunk(ci: int) -> void:
    var ch: Dictionary = _chunks[ci]
    if ch["task_mmi"] != null:
        return
    var layer: Dictionary = _layers[ch["storey_id"]]
    var y: float = gs.bundle.storey_y_for_index(int(layer["index"])) + Y_OFFSET
    var quad := QuadMesh.new()
    quad.size = Vector2(QUAD_SIZE, QUAD_SIZE)
    quad.orientation = PlaneMesh.FACE_Y
    for which in ["task", "empty"]:
        var idx: Array[int] = ch["cells_idx"] if which == "task" else ch["empty_idx"]
        var cells: Array = layer["cells"] if which == "task" else layer["empty_cells"]
        var mm := MultiMesh.new()
        mm.transform_format = MultiMesh.TRANSFORM_3D
        mm.use_colors = which == "task"
        var q: QuadMesh = quad.duplicate()
        q.material = layer["task_mat"] if which == "task" else layer["empty_mat"]
        mm.mesh = q
        mm.instance_count = idx.size()
        for k in idx.size():
            var c: Vector2i = cells[idx[k]]
            mm.set_instance_transform(k, Transform3D(Basis(), Vector3(c.x, y, c.y)))
        var mmi := MultiMeshInstance3D.new()
        mmi.name = "Heat%s_%s_%d" % [which.capitalize(), ch["storey_id"], ci]
        mmi.multimesh = mm
        mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
        mmi.visible = bool(_near.get(int(ch["key"]), true))
        (layer["root"] as Node3D).add_child(mmi)
        ch["%s_mmi" % which] = mmi
    ch["dirty"] = true


## Called by BimView when a chunk starts / stops being drawn in full (frustum, storey, far LOD): the quads follow.
func chunk_changed(storey_idx: int, cx: int, cz: int, near: bool) -> void:
    var key: int = BimView.chunk_key(storey_idx, cx, cz)
    _near[key] = near
    if not _built:
        return
    for sid in _layer_order:
        var ci: int = int(_chunk_by_key.get("%s|%d" % [sid, key], -1))
        if ci < 0:
            continue
        var ch: Dictionary = _chunks[ci]
        if ch["task_mmi"] != null:
            (ch["task_mmi"] as Node3D).visible = near
            (ch["empty_mmi"] as Node3D).visible = near
        if near and heat_visible and bool(ch["dirty"]):
            _dirty = true


func _storey_alpha(index: int) -> float:
    if index == focus_storey_index:
        return FOCUS_ALPHA
    return OTHER_ALPHA if index < focus_storey_index else 0.0


func _apply_alpha() -> void:
    for k in _layers:
        var l: Dictionary = _layers[k]
        var a: float = _storey_alpha(int(l["index"]))
        (l["task_mat"] as StandardMaterial3D).albedo_color = Color(1, 1, 1, a)
        (l["empty_mat"] as StandardMaterial3D).albedo_color = Color(0.78, 0.8, 0.85, EMPTY_ALPHA * (a / FOCUS_ALPHA))
        (l["root"] as Node3D).visible = a > 0.0


# ------------------------------------------------------------------ incremental update

## Moves the contribution of one task to its cells to its current progress.
func _apply_task(slot: int) -> void:
    var ids: Variant = _t_cells[slot]
    if ids == null:
        return
    var st: TaskStore = gs.runtime
    var t: TaskData = st.task_refs[slot]
    if t == null:
        return
    var s: int = st.state[slot]
    var done: float
    if s == TaskRuntime.State.AWAITING_INSPECTION or s == TaskRuntime.State.DONE or s == TaskRuntime.State.INSPECTED:
        done = t.estimated_crew_days
    else:
        done = minf(st.progress[slot], t.estimated_crew_days)
    var delta: float = done - _t_done[slot]
    var rw: int = 1 if s == TaskRuntime.State.REWORK else 0
    var rw_delta: int = rw - int(_t_rw[slot])
    if delta == 0.0 and rw_delta == 0:
        return
    _t_done[slot] = done
    _t_rw[slot] = rw
    for cid in (ids as PackedInt32Array):
        _h_done[cid] += delta
        _h_rework[cid] += rw_delta
        (_chunks[_h_chunk[cid]])["dirty"] = true


## Colour of a global cell id.
func _colour_of(cid: int) -> Color:
    var total: float = _h_total[cid]
    var share: float = clampf(_h_done[cid] / total, 0.0, 1.0) if total > 0.0 else 0.0
    return heat_colour(share, _h_rework[cid] > 0)


func _recolour_chunk(ci: int) -> void:
    var ch: Dictionary = _chunks[ci]
    var layer: Dictionary = _layers[ch["storey_id"]]
    var cids: Array = layer["cid"]
    var mm: MultiMesh = (ch["task_mmi"] as MultiMeshInstance3D).multimesh
    var idx: Array[int] = ch["cells_idx"]
    for k in idx.size():
        mm.set_instance_color(k, _colour_of(int(cids[idx[k]])))
    ch["dirty"] = false


## Brings the overlay up to date: applies the tasks that changed since the last call (the TaskStore change log), then
## recolours the dirty chunks that are drawn. `budget_ms` > 0 stops after that many milliseconds (the rest follows next
## frame from _process). Returns the number of chunks recoloured.
func refresh(budget_ms: float = 0.0) -> int:
    if gs == null or gs.bundle == null:
        return 0
    var t0: int = Time.get_ticks_usec()
    if not _built:
        _ensure_built()
    else:
        var st: TaskStore = gs.runtime
        var changed: Variant = null
        if st.log_epoch == _epoch:
            changed = st.changed_set_since(_cursor)
        _cursor = st.log_end()
        _epoch = st.log_epoch
        if changed == null:  # the log was cut: recompute every task
            for slot in st.slot_count():
                _pending.append(slot)
        else:
            for slot in (changed as Dictionary):
                _pending.append(slot)
    while not _pending.is_empty():
        _apply_task(_pending.pop_back())
        if budget_ms > 0.0 and _pending.size() % 64 == 0 and float(Time.get_ticks_usec() - t0) / 1000.0 > budget_ms:
            return 0
    var n: int = 0
    for ci in _chunks.size():
        var ch: Dictionary = _chunks[ci]
        if not bool(ch["dirty"]) or not bool(_near.get(int(ch["key"]), true)):
            continue
        _ensure_chunk(ci)
        _recolour_chunk(ci)
        n += 1
        if budget_ms > 0.0 and float(Time.get_ticks_usec() - t0) / 1000.0 > budget_ms:
            return n
    _dirty = false
    refresh_count += 1
    return n


# ------------------------------------------------------------------ queries (tests, UI)

func layer_count() -> int:
    _ensure_built()
    return _layers.size()


## Number of coloured (task) cells and hatched (task-less) cells on a storey.
func cell_counts(storey_id: String) -> Vector2i:
    _ensure_built()
    if not _layers.has(storey_id):
        return Vector2i.ZERO
    var l: Dictionary = _layers[storey_id]
    return Vector2i((l["cells"] as Array).size(), (l["empty_cells"] as Array).size())


## Colour of quad `i` (sorted cell order) of a storey's task layer as of the last refresh.
func quad_colour(storey_id: String, i: int) -> Color:
    _ensure_built()
    if not _layers.has(storey_id):
        return GREY
    var cids: Array = (_layers[storey_id] as Dictionary)["cid"]
    return _colour_of(int(cids[i])) if i < cids.size() else GREY


## Root node of a storey's chunks (visibility follows the storey focus).
func layer_node(storey_id: String) -> Node3D:
    _ensure_built()
    return (_layers[storey_id] as Dictionary)["root"] if _layers.has(storey_id) else null


## Number of quads created so far for a storey (chunks create theirs when first drawn).
func quad_count(storey_id: String) -> int:
    _ensure_built()
    if not _layers.has(storey_id):
        return 0
    var n: int = 0
    for ch in _chunks:
        if ch["storey_id"] == storey_id and ch["task_mmi"] != null:
            n += ((ch["task_mmi"] as MultiMeshInstance3D).multimesh.instance_count)
    return n


func chunk_count() -> int:
    _ensure_built()
    return _chunks.size()


## Chunks whose quads exist / are shown (tests, profiling).
func node_chunk_count() -> int:
    var n: int = 0
    for ch in _chunks:
        if ch["task_mmi"] != null:
            n += 1
    return n


## Colour of one cell as drawn (before the storey alpha); Color(0,0,0,0) for cells no task touches.
func cell_colour(storey_id: String, cell: Vector2i) -> Color:
    _ensure_built()
    if not _layers.has(storey_id):
        return Color(0, 0, 0, 0)
    var l: Dictionary = _layers[storey_id]
    var cells: Array = l["cells"]
    var lo: int = 0
    var hi: int = cells.size()
    while lo < hi:  # cells are sorted
        var mid: int = (lo + hi) >> 1
        if _cell_less(cells[mid], cell):
            lo = mid + 1
        else:
            hi = mid
    if lo < cells.size() and cells[lo] == cell:
        return _colour_of(int((l["cid"] as Array)[lo]))
    return Color(0, 0, 0, 0)
