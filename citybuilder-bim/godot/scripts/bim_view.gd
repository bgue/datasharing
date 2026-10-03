class_name BimView
extends Node3D
## Renders BIM elements as procedural stand-ins: one MultiMeshInstance3D per `visual` kind,
## per-instance colour = discipline colour with state alpha (docs/03 section 6).

const DISCIPLINE_COLORS: Dictionary = {
    "general": Color(0.62, 0.62, 0.64),
    "civil": Color(0.55, 0.38, 0.22),
    "structure": Color(0.55, 0.56, 0.58),
    "architecture": Color(0.86, 0.78, 0.6),
    "mechanical": Color(0.25, 0.5, 0.95),
    "electrical": Color(0.98, 0.85, 0.2),
    "plumbing": Color(0.1, 0.65, 0.65),
    "fire": Color(0.9, 0.25, 0.2),
    "process": Color(0.95, 0.5, 0.1),
    "instrumentation": Color(0.6, 0.4, 0.85),
    "medical": Color(0.85, 0.2, 0.7),
    "commissioning": Color(0.35, 0.75, 0.4),
}
const CYLINDER_KINDS: Array[String] = ["column", "pile", "pier", "pipe", "tank", "culvert"]
## Placeholder colours of the virtual-task markers (docs/06 A.3); the kits agent may replace the meshes.
const MARKER_COLORS: Dictionary = {
    "survey": Color(0.98, 0.85, 0.2),
    "dewatering": Color(0.25, 0.5, 0.95),
    "scaffold": Color(0.6, 0.62, 0.66),
    "lift_plan": Color(0.98, 0.55, 0.12),
    "permit": Color(0.95, 0.95, 0.95),
    "test": Color(0.25, 0.8, 0.4),
    "shoring": Color(0.5, 0.33, 0.18),
    "crane": Color(0.98, 0.55, 0.12),
    "generic": Color(0.72, 0.55, 0.9),
}
const MARKER_RADIUS: float = 0.12
const MARKER_HEIGHT: float = 0.4
## Not-started elements are a thin outline box (GHOST_OUTLINE_ALPHA) with no filled volume; only on the focused storey
## a very faint fill (GHOST_ALPHA) is added. Stacked ghost fills used to wash into a white fog over the work.
const GHOST_ALPHA: float = 0.05
const GHOST_OUTLINE_ALPHA: float = 0.35
## In-progress and framed parts stay (almost) opaque so that the work under way is the most prominent thing.
const FRAMED_ALPHA: float = 0.9
const ABOVE_FOCUS_ALPHA: float = 0.07
## Progress visuals (WP-Q). Same tint amounts as the kits (KitBuilder: inspected 0.22, rework 0.45 on average).
const INSPECTED_TINT: Color = Color(0.2, 0.9, 0.35)
const INSPECTED_AMOUNT: float = 0.22
const REWORK_TINT: Color = Color(0.95, 0.15, 0.1)
## A started element shows at least this share of its extent so that it is visible.
const MIN_FILL: float = 0.06
## An instance is rewritten only when its fill moved by more than this (or its state changed).
const FILL_EPS: float = 0.02
## Count kinds (windows, doors...) turn solid at this fill.
const COUNT_THRESHOLD: float = 0.5
const OUTLINE_ALPHA: float = 0.7
## Kinds that grow along an axis of their cell run: pipes, ducts, trays, kerbs, beams, and the flat kinds (a half-poured
## slab reads as half because the pour front runs across the plan; its 0.3 m thickness would not show a height change).
const LENGTH_KINDS: Array[String] = ["duct", "pipe", "cable_tray", "kerb", "beam", "slab", "roof", "deck", "pavement",
        "floor_finish", "ceiling"]
## Linear kinds orient their extent along the dominant axis of their cells; flat kinds keep the size_hint axes.
const LINEAR_KINDS: Array[String] = ["duct", "pipe", "cable_tray", "kerb", "beam"]
## Kinds that appear as a whole: ghost below COUNT_THRESHOLD, solid from there.
const COUNT_KINDS: Array[String] = ["window", "door", "terminal", "equipment", "sign"]
const OUTLINE_NONE: int = 0
const OUTLINE_GHOST: int = 1  # whole extent of a not-started element
const OUTLINE_REMAINING: int = 2  # full extent around the part still to build
const HIGHLIGHT_COLOR: Color = Color(1, 0.9, 0.2)

## Chunked rendering (docs/06 C.3): the non-kit elements are drawn by one MultiMesh per (storey, CHUNK_CELLS x CHUNK_CELLS
## cell chunk, visual kind) plus one outline MultiMesh next to it. Per-element state lives in PackedArrays indexed by the
## element index (`_slots` maps a guid to it). `update_culling()` hides chunks outside the camera frustum (and, on big
## models, above the focused storey) and swaps chunks beyond `lod_far_distance` for per-cell cubes tinted by the done
## share of the cell.
const CHUNK_CELLS: int = 8
const TILE_CHUNKS: int = 4
## Chunks farther than this from the camera (grid cells) draw per-cell cubes instead of their elements.
const LOD_FAR_DISTANCE: float = 100.0
const LOD_HYSTERESIS: float = 0.9
const CULL_INTERVAL_S: float = 0.1
## Models with more elements than this cull the storeys above the focus instead of drawing them faintly, and recolour
## in time slices.
const BIG_MODEL_ELEMENTS: int = 30000
const RECOLOUR_BUDGET_MS: float = 4.0
## Cell-cube MultiMeshes created per culling pass (the first pass over a big model spreads them over a few passes).
const FAR_BUILDS_PER_PASS: int = 96
const FAR_GHOST: Color = Color(0.62, 0.62, 0.66, 0.12)
const GROW_HEIGHT: int = 0
const GROW_LENGTH: int = 1
const GROW_COUNT: int = 2

var gs: SimState = null
## Optional hook for a marker kit: `func(marker: String) -> Mesh`; a null result falls back to the placeholder cylinder.
var marker_mesh_provider: Callable = Callable()
# >>> visual kits (WP-O): elements with a kit (scripts/kits/) are drawn by KitLayer instead of the MultiMesh pass
var use_kits: bool = true
var _kit_layer: KitLayer = null
# <<< visual kits
var show_ghost: bool = true
var focus_storey_index: int = 0
## Distance (grid cells) beyond which a chunk draws cell cubes; `cull_enabled` off keeps every chunk drawn in full.
var lod_far_distance: float = LOD_FAR_DISTANCE
var cull_enabled: bool = true
## -1 automatic (on for big models), 0 draw the storeys above the focus faintly, 1 hide their chunks.
var cull_above_focus: int = -1

# element data (non-kit elements), indexed by element index
var _elems: Array[ElementData] = []
var _slots: Dictionary = {}  # guid -> element index
var _e_kind: PackedInt32Array = PackedInt32Array()
var _e_group: PackedInt32Array = PackedInt32Array()
var _e_slot: PackedInt32Array = PackedInt32Array()
var _e_storey: PackedInt32Array = PackedInt32Array()
var _g_centre: PackedVector3Array = PackedVector3Array()  # full extent: centre and size (grid units)
var _g_scale: PackedVector3Array = PackedVector3Array()
var _g_mode: PackedByteArray = PackedByteArray()  # GROW_HEIGHT / GROW_LENGTH / GROW_COUNT
var _g_axis: PackedByteArray = PackedByteArray()  # 0 = x, 2 = z (length mode)
var _a_fill: PackedFloat32Array = PackedFloat32Array()  # fill the instance was last written with (-1: never)
var _a_vis: PackedByteArray = PackedByteArray()  # SimState.Visual the instance was last written with (255: never)
var _a_omode: PackedByteArray = PackedByteArray()
var _a_col: PackedColorArray = PackedColorArray()  # last written colours (the headless renderer keeps no instance data)
var _a_ocol: PackedColorArray = PackedColorArray()
var _e_cell_off: PackedInt32Array = PackedInt32Array()  # element -> range in _e_cells
var _e_cells: PackedInt32Array = PackedInt32Array()
var _e_tasked: PackedByteArray = PackedByteArray()  # 1 when the element has a non-virtual task
var _kinds: Array[String] = []
var _kind_index: Dictionary = {}
var _kind_mesh: Array = []
var _outline_mesh: Mesh = null
var _far_mesh: BoxMesh = null
var _far_material: StandardMaterial3D = null
# chunks / groups / tiles
var _chunk_root: Node3D = null
var _groups: Array[Dictionary] = []  # {chunk, kind, mmi, omi, elems}
var _chunks: Array[Dictionary] = []
var _chunk_index: Dictionary = {}  # chunk key -> chunk index
var _tiles: Array[Dictionary] = []
var _storey_chunks: Dictionary = {}  # storey index -> Array of chunk indices
# cells (far LOD cubes): per cell the sum of the fills of the elements covering it
var _cell_index: Dictionary = {}
var _c_x: PackedInt32Array = PackedInt32Array()
var _c_z: PackedInt32Array = PackedInt32Array()
var _c_storey: PackedInt32Array = PackedInt32Array()
var _c_sum: PackedFloat32Array = PackedFloat32Array()
var _c_n: PackedInt32Array = PackedInt32Array()  # tasked elements covering the cell
var _c_chunk: PackedInt32Array = PackedInt32Array()  # cell -> chunk drawing its far cube
var _c_slot: PackedInt32Array = PackedInt32Array()  # cell -> instance index in that cube MultiMesh
var _far_dirty: Dictionary = {}  # cell -> true: its far cube colour is out of date
var _storey_index: Dictionary = {}  # guid -> storey index of kit elements (the others use _e_storey)
var _rework: Dictionary = {}  # element index -> true while REWORK (pulses)
var _dirty: bool = true
var _pulse: float = 0.0
var _material: StandardMaterial3D = null
var _marker_root: Node3D = null
var _marker_nodes: Dictionary = {}  # task_id -> MeshInstance3D
var _marker_default_mesh: CylinderMesh = null
var _color_epoch: int = 0
var _recolour_queue: Array[int] = []
var _log_cursor: int = 0
var _log_epoch: int = -1
var _cull_dirty: bool = true
var _cull_timer: float = 0.0
var _last_cam_xf: Transform3D = Transform3D()
var _last_cam_fov: float = -1.0
var _heat: CellHeatOverlay = null
var _hl_root: Node3D = null
var _hl_lines: MultiMeshInstance3D = null
var _hl_fill: MultiMeshInstance3D = null
var _hl_line_mat: StandardMaterial3D = null
var _hl_fill_mat: StandardMaterial3D = null
var _hl_color: Color = HIGHLIGHT_COLOR
var _hl_boxes: Array[Transform3D] = []
var _hl_time: float = 0.0
## Instance transform writes since the view was built (tests: unchanged elements are not rewritten).
var transform_writes: int = 0
## Duration of the last refresh_progress() in ms and the number of instances it rewrote.
var last_refresh_ms: float = 0.0
var last_refresh_writes: int = 0
## Time (ms) BimView spent on progress since the previous refresh_progress(): the task-state signals of the week plus
## the refresh itself (the cost of a week for this view).
var last_week_ms: float = 0.0
var _signal_us: int = 0
## Result of the last update_culling(): {chunks, visible, near, far, hidden, ms}.
var last_cull: Dictionary = {}
## Milliseconds of the last _build() (tests / profiling).
var last_build_ms: float = 0.0
var last_build_phases: Dictionary = {}
# bulk buffers used while building: 16 floats (3x4 transform, colour) per instance, groups at _g_base
var _xbuf: PackedFloat32Array = PackedFloat32Array()
var _obuf: PackedFloat32Array = PackedFloat32Array()
var _bulk: bool = false


func setup(state: SimState) -> void:
    gs = state
    _heat = CellHeatOverlay.new()
    _heat.name = "CellHeat"
    add_child(_heat)
    _heat.setup(gs)
    _heat.set_focus_storey(focus_storey_index)
    _build()
    gs.task_state_changed.connect(_on_task_state_changed)
    gs.tasks_changed.connect(func() -> void:
        _rebuild_markers()
        refresh_progress())
    gs.level_started.connect(func() -> void: _build())
    gs.week_advanced.connect(func(_w: int) -> void: refresh_progress())
    if gs.api != null:  # the control API drives view.highlight / view.set_heat through this node
        gs.api.bim_view = self


func set_ghost_visible(v: bool) -> void:
    show_ghost = v
    _mark_recolour()
    if _kit_layer != null:  # visual kits (WP-O)
        _kit_layer.set_ghost_visible(v)


func set_focus_storey(idx: int) -> void:
    focus_storey_index = idx
    _mark_recolour()
    _cull_dirty = true
    if _heat != null:
        _heat.set_focus_storey(idx)
    if _kit_layer != null:  # visual kits (WP-O)
        _kit_layer.set_focus_storey(idx)


## Every instance colour depends on the focus / ghost toggle: recolour (chunks recolour lazily when they become visible).
func _mark_recolour() -> void:
    _dirty = true
    _color_epoch += 1


func _on_task_state_changed(task_id: String, _old: int, _new: int) -> void:
    var t: TaskData = gs.bundle.tasks_by_id.get(task_id, null)
    if t == null:
        return
    if t.is_virtual:
        _apply_marker_state(task_id)
        return
    var t0: int = Time.get_ticks_usec()
    for g in t.element_guids:
        var ei: int = int(_slots.get(g, -1))
        if ei >= 0:
            _apply_element(ei)
    _signal_us += Time.get_ticks_usec() - t0


# ------------------------------------------------------------------ virtual task markers

## Mesh of a marker kit. Assign `marker_mesh_provider` (a Callable taking the marker id and returning a Mesh) to
## replace the placeholder cylinder; a provider that returns null keeps the placeholder.
func marker_mesh_for(marker: String) -> Mesh:
    if marker_mesh_provider.is_valid():
        var m: Variant = marker_mesh_provider.call(marker)
        if m is Mesh:
            return m
    if _marker_default_mesh == null:
        _marker_default_mesh = CylinderMesh.new()
        _marker_default_mesh.top_radius = MARKER_RADIUS
        _marker_default_mesh.bottom_radius = MARKER_RADIUS
        _marker_default_mesh.height = MARKER_HEIGHT
        _marker_default_mesh.radial_segments = 10
        _marker_default_mesh.rings = 1
    return _marker_default_mesh


static func marker_colour(marker: String) -> Color:
    return MARKER_COLORS.get(marker, MARKER_COLORS["generic"])


## Marker node of a virtual task (null when it has none): for tests and the UI.
func marker_node(task_id: String) -> MeshInstance3D:
    return _marker_nodes.get(task_id, null)


func marker_count() -> int:
    return _marker_nodes.size()


func marker_position(m: Dictionary, count_in_zone: int) -> Vector3:
    var b: SequenceBundle = gs.bundle
    var cell: Vector2i = m["cell"]
    var idx: int = int(m["index"])
    var cols: int = 4
    var dx: float = (float(idx % cols) - float(mini(count_in_zone, cols) - 1) * 0.5) * 0.3
    var dz: float = float(idx / cols) * 0.3 + 0.25
    var y: float = b.storey_y(str(m["storey_id"])) + MARKER_HEIGHT * 0.5 + 0.02
    return Vector3(float(cell.x) + dx, y, float(cell.y) + dz)


func _rebuild_markers() -> void:
    if _marker_root == null:
        _marker_root = Node3D.new()
        _marker_root.name = "Markers"
        add_child(_marker_root)
    for k in _marker_nodes:
        (_marker_nodes[k] as Node).queue_free()
    _marker_nodes.clear()
    if gs == null or gs.bundle == null:
        return
    var list: Array[Dictionary] = gs.virtual_markers()
    var per_zone: Dictionary = {}
    for m in list:
        per_zone[m["zone_id"]] = int(per_zone.get(m["zone_id"], 0)) + 1
    for m in list:
        var node := MeshInstance3D.new()
        node.name = "Marker_%s" % str(m["task_id"])
        node.mesh = marker_mesh_for(str(m["marker"]))
        if not marker_mesh_provider.is_valid():
            var mat := StandardMaterial3D.new()
            mat.albedo_color = marker_colour(str(m["marker"]))
            mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
            mat.roughness = 0.7
            node.material_override = mat
        node.position = marker_position(m, int(per_zone[m["zone_id"]]))
        node.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
        _marker_root.add_child(node)
        _marker_nodes[str(m["task_id"])] = node
        _apply_marker_state(str(m["task_id"]))


## Not started: faint; in progress: solid; done: dimmed and tinted green.
func _apply_marker_state(task_id: String) -> void:
    var node: MeshInstance3D = _marker_nodes.get(task_id, null)
    if node == null or gs == null:
        return
    var rt: TaskRuntime = gs.runtime.get_rt(task_id)
    var t: TaskData = gs.bundle.tasks_by_id.get(task_id, null)
    if rt == null or t == null:
        return
    var col: Color = marker_colour(gs.marker_of(t))
    var a: float = 0.35
    match rt.state:
        TaskRuntime.State.ACTIVE, TaskRuntime.State.AWAITING_INSPECTION, TaskRuntime.State.REWORK:
            a = 1.0
        TaskRuntime.State.DONE, TaskRuntime.State.INSPECTED:
            col = col.lerp(Color(0.2, 0.9, 0.35), 0.6)
            a = 0.6
    col.a = a
    if node.material_override is StandardMaterial3D:
        (node.material_override as StandardMaterial3D).albedo_color = col
    else:
        node.transparency = 1.0 - a


# ------------------------------------------------------------------ geometry

## Default [w, h, d] metres when an element carries no size_hint.
static func default_size(kind: String, cells: Array[Vector2i], cell_m: float, storey_h: float) -> Vector3:
    var minx: int = 1 << 30
    var maxx: int = -(1 << 30)
    var minz: int = 1 << 30
    var maxz: int = -(1 << 30)
    for c in cells:
        minx = mini(minx, c.x)
        maxx = maxi(maxx, c.x)
        minz = mini(minz, c.y)
        maxz = maxi(maxz, c.y)
    if cells.is_empty():
        minx = 0
        maxx = 0
        minz = 0
        maxz = 0
    var w: float = float(maxx - minx + 1) * cell_m
    var d: float = float(maxz - minz + 1) * cell_m
    match kind:
        "slab", "roof", "deck", "pavement", "floor_finish", "ceiling":
            return Vector3(w, 0.3, d)
        "footing":
            return Vector3(1.8, 0.6, 1.8)
        "pile":
            return Vector3(0.5, 3.0, 0.5)
        "column", "pier":
            return Vector3(0.5, storey_h, 0.5)
        "beam":
            return Vector3(w, 0.4, 0.3)
        "wall", "curtain_wall", "barrier":
            return Vector3(w, storey_h, 0.2)
        "duct", "cable_tray":
            return Vector3(w, 0.4, 0.5)
        "pipe":
            return Vector3(w, 0.2, 0.2)
        "door":
            return Vector3(1.0, 2.1, 0.1)
        "window":
            return Vector3(1.5, 1.2, 0.1)
        "stair":
            return Vector3(2.0, storey_h, 4.0)
        "tank":
            return Vector3(3.0, 4.0, 3.0)
        "equipment", "terminal":
            return Vector3(1.5, 1.5, 1.5)
        "earthwork":
            return Vector3(w, 1.0, d)
        "kerb":
            return Vector3(w, 0.2, 0.2)
        "culvert":
            return Vector3(1.5, 1.5, w)
        "sign":
            return Vector3(0.1, 2.5, 0.1)
    return Vector3(w * 0.8, 1.0, d * 0.8)


## Vertical centre (metres above storey floor) for a stand-in of the given size.
static func vertical_centre(kind: String, size: Vector3, storey_h: float) -> float:
    match kind:
        "slab", "deck":
            return -size.y * 0.5
        "roof", "ceiling":
            return storey_h - size.y * 0.5
        "footing", "pile":
            return -size.y * 0.5
        "duct", "cable_tray", "pipe":
            return storey_h * 0.85
        "beam":
            return storey_h - size.y * 0.5
        "window":
            return 1.5
    return size.y * 0.5


## Size (grid units) and centre of the stand-in of an element before progress scaling.
func _base_extent(e: ElementData) -> Array:
    var b: SequenceBundle = gs.bundle
    var size_m: Vector3 = e.size_hint if e.has_size_hint else default_size(e.visual, e.cells, b.cell_size_m, b.storey_height_m)
    var size_u: Vector3 = size_m / b.cell_size_m
    var cx: float = 0.0
    var cz: float = 0.0
    for c in e.cells:
        cx += float(c.x)
        cz += float(c.y)
    var n: float = float(maxi(1, e.cells.size()))
    var storey_idx: int = int(b.storey_index_by_id.get(e.storey_id, 0))
    var y: float = b.storey_y_for_index(storey_idx) + vertical_centre(e.visual, size_m, b.storey_height_m) / b.cell_size_m
    return [size_u, Vector3(cx / n, y, cz / n), size_m]


func element_transform(e: ElementData) -> Transform3D:
    var ext: Array = _base_extent(e)
    # grid cells are centred on integer coordinates (Kenney GridMap, cell_center = false)
    return Transform3D(Basis.from_scale(ext[0]), ext[1])


## [mode (GROW_*), full-extent scale, centre, axis] of a non-kit element: linear kinds are oriented along the dominant axis
## of their cells, flat kinds extend along the longer cell axis.
func _geometry(e: ElementData) -> Array:
    var ext: Array = _base_extent(e)
    var sc: Vector3 = ext[0]
    var centre: Vector3 = ext[1]
    var mode: int = GROW_HEIGHT
    var kind: String = e.visual
    if COUNT_KINDS.has(kind):
        mode = GROW_COUNT
    elif LENGTH_KINDS.has(kind):
        mode = GROW_LENGTH
    if mode != GROW_LENGTH:
        return [mode, sc, centre, 0]
    var b: SequenceBundle = gs.bundle
    var minx: int = 1 << 30
    var maxx: int = -(1 << 30)
    var minz: int = 1 << 30
    var maxz: int = -(1 << 30)
    for c in e.cells:
        minx = mini(minx, c.x)
        maxx = maxi(maxx, c.x)
        minz = mini(minz, c.y)
        maxz = maxi(maxz, c.y)
    var w: int = maxx - minx + 1 if not e.cells.is_empty() else 1
    var d: int = maxz - minz + 1 if not e.cells.is_empty() else 1
    var axis: int = 0
    if d > w:
        axis = 2
    elif d == w and LINEAR_KINDS.has(kind):
        axis = 2 if sc.z > sc.x else 0
    if LINEAR_KINDS.has(kind):
        var size_m: Vector3 = ext[2]
        var len_u: float = maxf(size_m.x, size_m.z) / b.cell_size_m
        var thick_u: float = minf(size_m.x, size_m.z) / b.cell_size_m
        if not e.has_size_hint:  # default sizes carry the x extent only: use the cell run
            len_u = float(maxi(w, d))
            thick_u = size_m.z / b.cell_size_m
        sc = Vector3(len_u, sc.y, thick_u) if axis == 0 else Vector3(thick_u, sc.y, len_u)
    return [mode, sc, centre, axis]


func _get_material() -> StandardMaterial3D:
    if _material == null:
        _material = StandardMaterial3D.new()
        _material.vertex_color_use_as_albedo = true
        # fully opaque fragments write depth (pre-pass), so the outline boxes drawn afterwards are hidden behind them
        _material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA_DEPTH_PRE_PASS
        _material.shading_mode = BaseMaterial3D.SHADING_MODE_PER_PIXEL
        _material.roughness = 0.8
    return _material


func _make_mesh(kind: String) -> Mesh:
    var m: Mesh
    if CYLINDER_KINDS.has(kind):
        var cm := CylinderMesh.new()
        cm.top_radius = 0.5
        cm.bottom_radius = 0.5
        cm.height = 1.0
        cm.radial_segments = 12
        cm.rings = 1
        m = cm
    else:
        var bm := BoxMesh.new()
        bm.size = Vector3.ONE
        m = bm
    m.surface_set_material(0, _get_material())
    return m


## Key of a chunk (storey index, chunk column / row) and of a grid cell, as Dictionary keys.
static func chunk_key(storey: int, cx: int, cz: int) -> int:
    return ((storey + 1024) << 42) | ((cx + 524288) << 21) | (cz + 524288)


static func cell_key(storey: int, x: int, z: int) -> int:
    return ((storey + 1024) << 42) | ((x + 1048576) << 21) | (z + 1048576)


func _chunk_for(storey: int, cx: int, cz: int) -> int:
    var key: int = chunk_key(storey, cx, cz)
    var ci: int = int(_chunk_index.get(key, -1))
    if ci >= 0:
        return ci
    ci = _chunks.size()
    _chunk_index[key] = ci
    _chunks.append({"key": key, "storey": storey, "cx": cx, "cz": cz, "groups": [] as Array[int],
            "lo": Vector3(INF, INF, INF), "hi": Vector3(-INF, -INF, -INF), "centre": Vector3.ZERO, "radius": 0.0,
            "visible": true, "lod": 0, "col_epoch": -1, "far": null, "far_epoch": -1, "cells": [] as Array[int],
            "tile": -1, "node_visible": true})
    if not _storey_chunks.has(storey):
        _storey_chunks[storey] = [] as Array[int]
    (_storey_chunks[storey] as Array[int]).append(ci)
    return ci


func _free_nodes() -> void:
    if _chunk_root != null:
        _chunk_root.queue_free()
        _chunk_root = null
    _groups.clear()
    _chunks.clear()
    _chunk_index.clear()
    _tiles.clear()
    _storey_chunks.clear()
    _cell_index.clear()
    _elems.clear()
    _slots.clear()
    _storey_index.clear()
    _rework.clear()
    _recolour_queue.clear()
    _e_kind = PackedInt32Array()
    _e_group = PackedInt32Array()
    _e_slot = PackedInt32Array()
    _e_storey = PackedInt32Array()
    _g_centre = PackedVector3Array()
    _g_scale = PackedVector3Array()
    _g_mode = PackedByteArray()
    _g_axis = PackedByteArray()
    _a_fill = PackedFloat32Array()
    _a_vis = PackedByteArray()
    _a_omode = PackedByteArray()
    _a_col = PackedColorArray()
    _a_ocol = PackedColorArray()
    _e_cell_off = PackedInt32Array()
    _e_cells = PackedInt32Array()
    _e_tasked = PackedByteArray()
    _c_x = PackedInt32Array()
    _c_z = PackedInt32Array()
    _c_storey = PackedInt32Array()
    _c_sum = PackedFloat32Array()
    _c_n = PackedInt32Array()
    _c_chunk = PackedInt32Array()
    _c_slot = PackedInt32Array()
    _far_dirty.clear()


func _build() -> void:
    var t0: int = Time.get_ticks_usec()
    _free_nodes()
    clear_highlight()
    if gs == null or gs.bundle == null:
        return
    _kits_rebuild()  # visual kits (WP-O)
    var t_kits: int = Time.get_ticks_usec()
    var b: SequenceBundle = gs.bundle
    # pass 1: geometry, chunk and group of every non-kit element
    var group_of: Dictionary = {}  # chunk * 256 + kind -> group index
    var group_lists: Array = []  # per group: Array of element indices
    var elems_with_cells: int = 0
    _e_cell_off.append(0)
    for e in b.elements:
        var storey: int = int(b.storey_index_by_id.get(e.storey_id, 0))
        if _kit_layer != null and _kit_layer.handles(e.guid):  # visual kits (WP-O): drawn by KitLayer
            _storey_index[e.guid] = storey
            continue
        var ei: int = _elems.size()
        _elems.append(e)
        _slots[e.guid] = ei
        var kind: int = int(_kind_index.get(e.visual, -1))
        if kind < 0:
            kind = _kinds.size()
            _kind_index[e.visual] = kind
            _kinds.append(e.visual)
            _kind_mesh.append(_make_mesh(e.visual))
        _e_kind.append(kind)
        _e_storey.append(storey)
        var geo: Array = _geometry(e)
        var centre: Vector3 = geo[2]
        var scale: Vector3 = geo[1]
        _g_mode.append(int(geo[0]))
        _g_scale.append(scale)
        _g_centre.append(centre)
        _g_axis.append(int(geo[3]))
        var ci: int = _chunk_for(storey, roundi(centre.x) >> 3, roundi(centre.z) >> 3)
        var ch: Dictionary = _chunks[ci]
        ch["lo"] = (ch["lo"] as Vector3).min(centre - scale * 0.5)
        ch["hi"] = (ch["hi"] as Vector3).max(centre + scale * 0.5)
        var gk: int = ci * 256 + kind
        var gi: int = int(group_of.get(gk, -1))
        if gi < 0:
            gi = _groups.size()
            group_of[gk] = gi
            _groups.append({"chunk": ci, "kind": kind, "mmi": null, "omi": null, "elems": [] as Array[int]})
            (ch["groups"] as Array[int]).append(gi)
        var g: Dictionary = _groups[gi]
        _e_group.append(gi)
        _e_slot.append((g["elems"] as Array[int]).size())
        (g["elems"] as Array[int]).append(ei)
        _a_fill.append(-1.0)
        _a_vis.append(255)
        _a_omode.append(0)
        _a_col.append(Color(1, 1, 1, 1))
        _a_ocol.append(Color(1, 1, 1, 0))
        # cells covered (far LOD cubes): the shade of a cell is the mean fill of the tasked elements covering it
        var tasked: bool = _has_tasks(e.guid)
        _e_tasked.append(1 if tasked else 0)
        for c in e.cells:
            var ck: int = cell_key(storey, c.x, c.y)
            var cid: int = int(_cell_index.get(ck, -1))
            if cid < 0:
                cid = _c_x.size()
                _cell_index[ck] = cid
                _c_x.append(c.x)
                _c_z.append(c.y)
                _c_storey.append(storey)
                _c_sum.append(0.0)
                _c_n.append(0)
                var cc: int = _chunk_for(storey, c.x >> 3, c.y >> 3)
                var carr: Array[int] = _chunks[cc]["cells"]
                _c_chunk.append(cc)
                _c_slot.append(carr.size())
                carr.append(cid)
            _e_cells.append(cid)
            if tasked:
                _c_n[cid] += 1
        elems_with_cells += e.cells.size()
        _e_cell_off.append(_e_cells.size())
    var tp1: int = Time.get_ticks_usec()
    # pass 2: nodes (the instance data is written in bulk below)
    _chunk_root = Node3D.new()
    _chunk_root.name = "Chunks"
    add_child(_chunk_root)
    var total: int = 0
    for gi in _groups.size():
        var g2: Dictionary = _groups[gi]
        var ch2: Dictionary = _chunks[int(g2["chunk"])]
        var list: Array[int] = g2["elems"]
        var kind2: int = int(g2["kind"])
        var mm := MultiMesh.new()
        mm.transform_format = MultiMesh.TRANSFORM_3D
        mm.use_colors = true
        mm.mesh = _kind_mesh[kind2]
        mm.instance_count = list.size()
        var mmi := MultiMeshInstance3D.new()
        mmi.name = "C%d_%d_%d_%s" % [int(ch2["storey"]), int(ch2["cx"]), int(ch2["cz"]), _kinds[kind2]]
        mmi.multimesh = mm
        mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
        _chunk_root.add_child(mmi)
        g2["mmi"] = mmi
        g2["omi"] = _make_outline_instance(mmi.name, list.size())
        _chunk_root.add_child(g2["omi"])
        g2["base"] = total
        total += list.size()
    _xbuf.resize(total * 16)
    _obuf.resize(total * 16)
    _bulk = true
    var tp2: int = Time.get_ticks_usec()
    _finish_chunks()
    _a_cell_reset()
    _dirty = true
    _color_epoch += 1
    _log_cursor = gs.runtime.log_end()
    _log_epoch = gs.runtime.log_epoch
    if _is_fresh():
        _fast_init()
    else:
        for ei3 in _elems.size():
            _apply_element(ei3, true)
    _bulk = false
    for g3 in _groups:
        var n3: int = (g3["elems"] as Array[int]).size()
        var b3: int = int(g3["base"]) * 16
        (g3["mmi"] as MultiMeshInstance3D).multimesh.buffer = _xbuf.slice(b3, b3 + n3 * 16)
        (g3["omi"] as MultiMeshInstance3D).multimesh.buffer = _obuf.slice(b3, b3 + n3 * 16)
    _xbuf = PackedFloat32Array()
    _obuf = PackedFloat32Array()
    for ch3 in _chunks:
        ch3["col_epoch"] = _color_epoch
    var tp3: int = Time.get_ticks_usec()
    _far_dirty.clear()
    _dirty = false
    _rebuild_markers()
    _cull_dirty = true
    last_build_ms = float(Time.get_ticks_usec() - t0) / 1000.0
    last_build_phases = {"kits": float(t_kits - t0) / 1000.0, "pass1": float(tp1 - t_kits) / 1000.0, "nodes": float(tp2 - tp1) / 1000.0, "apply": float(tp3 - tp2) / 1000.0}


## True while no task has started: every element is a ghost at full extent (the common start of a level).
func _is_fresh() -> bool:
    var c: PackedInt32Array = gs.runtime.counts
    return c[TaskRuntime.State.ACTIVE] + c[TaskRuntime.State.AWAITING_INSPECTION] + c[TaskRuntime.State.REWORK] \
            + c[TaskRuntime.State.DONE] + c[TaskRuntime.State.INSPECTED] == 0


## _apply_element(force) for a fresh model without the per-element lookups: ghost transforms and the colours cached per
## (discipline, storey).
func _fast_init() -> void:
    var b: SequenceBundle = gs.bundle
    var col_cache: Dictionary = {}
    var disc_cache: Dictionary = {}  # step id -> discipline
    for ei in _elems.size():
        var e: ElementData = _elems[ei]
        var storey: int = _e_storey[ei]
        var list: Array = b.tasks_by_element.get(e.guid, [])
        var disc: String = "general"
        if not list.is_empty():
            var step_id: String = (list[0] as TaskData).step_id
            if not disc_cache.has(step_id):
                var st: StepDef = b.steps_by_id.get(step_id, null)
                disc_cache[step_id] = st.discipline if st != null else "general"
            disc = disc_cache[step_id]
        var key: String = "%s|%d" % [disc, storey]
        if not col_cache.has(key):
            var base: Color = DISCIPLINE_COLORS.get(disc, DISCIPLINE_COLORS["general"])
            col_cache[key] = [_colour_from(base, SimState.Visual.GHOST, -1, storey, 1.0),
                    _outline_col_from(base, SimState.Visual.GHOST, -1, storey, OUTLINE_GHOST)]
        var cols: Array = col_cache[key]
        var g: Dictionary = _groups[_e_group[ei]]
        var o: int = (int(g["base"]) + _e_slot[ei]) * 16
        var xf := Transform3D(Basis.from_scale(_g_scale[ei]), _g_centre[ei])
        _put_xf(_xbuf, o, xf)
        _put_xf(_obuf, o, xf)
        var col: Color = cols[0]
        var oc: Color = cols[1]
        _a_col[ei] = col
        _a_ocol[ei] = oc
        _xbuf[o + 12] = col.r
        _xbuf[o + 13] = col.g
        _xbuf[o + 14] = col.b
        _xbuf[o + 15] = col.a
        _obuf[o + 12] = oc.r
        _obuf[o + 13] = oc.g
        _obuf[o + 14] = oc.b
        _obuf[o + 15] = oc.a
        _a_vis[ei] = SimState.Visual.GHOST
        _a_fill[ei] = 0.0
        _a_omode[ei] = OUTLINE_GHOST
    transform_writes += _elems.size()


func _a_cell_reset() -> void:
    _c_sum.fill(0.0)


func _has_tasks(guid: String) -> bool:
    for t in gs.bundle.tasks_by_element.get(guid, []):
        if not (t as TaskData).is_virtual:
            return true
    return false


## Bounds, bounding spheres and tiles of the chunks.
func _finish_chunks() -> void:
    var h_grid: float = gs.bundle.storey_height_m / gs.bundle.cell_size_m
    var tile_index: Dictionary = {}
    for ci in _chunks.size():
        var ch: Dictionary = _chunks[ci]
        var lo: Vector3 = ch["lo"]
        var hi: Vector3 = ch["hi"]
        if lo.x > hi.x:  # a chunk of far cubes only: its cell rectangle and one storey of height
            var y0: float = gs.bundle.storey_y_for_index(int(ch["storey"]))
            lo = Vector3(float(int(ch["cx"]) * CHUNK_CELLS) - 0.5, y0, float(int(ch["cz"]) * CHUNK_CELLS) - 0.5)
            hi = Vector3(lo.x + CHUNK_CELLS, y0 + h_grid, lo.z + CHUNK_CELLS)
        else:
            # far cubes of the chunk's cells extend the box
            var cells: Array[int] = ch["cells"]
            for cid in cells:
                lo = lo.min(Vector3(float(_c_x[cid]) - 0.5, lo.y, float(_c_z[cid]) - 0.5))
                hi = hi.max(Vector3(float(_c_x[cid]) + 0.5, hi.y, float(_c_z[cid]) + 0.5))
            hi.y = maxf(hi.y, gs.bundle.storey_y_for_index(int(ch["storey"])) + h_grid)
        ch["lo"] = lo
        ch["hi"] = hi
        ch["centre"] = (lo + hi) * 0.5
        ch["radius"] = (hi - lo).length() * 0.5
        var tk: int = chunk_key(int(ch["storey"]), int(ch["cx"]) >> 2, int(ch["cz"]) >> 2)
        var ti: int = int(tile_index.get(tk, -1))
        if ti < 0:
            ti = _tiles.size()
            tile_index[tk] = ti
            _tiles.append({"chunks": [] as Array[int], "lo": lo, "hi": hi, "centre": Vector3.ZERO, "radius": 0.0})
        var tile: Dictionary = _tiles[ti]
        (tile["chunks"] as Array[int]).append(ci)
        tile["lo"] = (tile["lo"] as Vector3).min(lo)
        tile["hi"] = (tile["hi"] as Vector3).max(hi)
        ch["tile"] = ti
    for tile in _tiles:
        tile["centre"] = ((tile["lo"] as Vector3) + (tile["hi"] as Vector3)) * 0.5
        tile["radius"] = ((tile["hi"] as Vector3) - (tile["lo"] as Vector3)).length() * 0.5


# ------------------------------------------------------------------ colours

func base_colour(guid: String) -> Color:
    var disc: String = gs.bundle.element_discipline(guid)
    return DISCIPLINE_COLORS.get(disc, DISCIPLINE_COLORS["general"])


func _storey_of(guid: String) -> int:
    var ei: int = int(_slots.get(guid, -1))
    if ei >= 0:
        return _e_storey[ei]
    return int(_storey_index.get(guid, 0))


## Final instance colour (alpha included) for an element.
func compute_colour(guid: String, pulse: float = 1.0) -> Color:
    var ei: int = int(_slots.get(guid, -1))
    return _colour(guid, ei, _storey_of(guid), pulse)


func _colour(guid: String, ei: int, storey: int, pulse: float) -> Color:
    return _colour_from(base_colour(guid), gs.element_visual(guid), ei, storey, pulse)


func _colour_from(base: Color, vis: int, ei: int, storey: int, pulse: float) -> Color:
    var col: Color = base
    var a: float = 1.0
    match vis:
        SimState.Visual.GHOST:
            a = GHOST_ALPHA if storey == focus_storey_index else 0.0
        SimState.Visual.FRAMED:
            a = FRAMED_ALPHA
        SimState.Visual.SOLID:
            a = 1.0
        SimState.Visual.INSPECTED:
            a = 1.0
            col = col.lerp(INSPECTED_TINT, INSPECTED_AMOUNT)
        SimState.Visual.REWORK:
            a = 1.0
            col = col.lerp(REWORK_TINT, 0.3 + 0.3 * pulse)
    # count kinds (windows, doors, equipment...) are ghosts until half of their work is done, then solid
    if (vis == SimState.Visual.FRAMED or vis == SimState.Visual.SOLID) and ei >= 0 and _g_mode[ei] == GROW_COUNT:
        a = (GHOST_ALPHA if storey == focus_storey_index else 0.0) if maxf(_a_fill[ei], 0.0) < COUNT_THRESHOLD else 1.0
    if storey > focus_storey_index:
        a = minf(a, ABOVE_FOCUS_ALPHA)
    if vis == SimState.Visual.GHOST and not show_ghost:
        a = 0.0
    col.a = a
    return col


## Writes the colour (and outline colour) of one element to its MultiMeshes.
func _apply_color(ei: int) -> void:
    var guid: String = _elems[ei].guid
    var g: Dictionary = _groups[_e_group[ei]]
    var slot: int = _e_slot[ei]
    var base: Color = base_colour(guid)
    var vis: int = gs.element_visual(guid)
    var col: Color = _colour_from(base, vis, ei, _e_storey[ei], 0.5 + 0.5 * sin(_pulse))
    _a_col[ei] = col
    var oc: Color = _outline_col_from(base, vis, ei, _e_storey[ei])
    _a_ocol[ei] = oc
    if _bulk:
        var o: int = (int(g["base"]) + slot) * 16 + 12
        _xbuf[o] = col.r
        _xbuf[o + 1] = col.g
        _xbuf[o + 2] = col.b
        _xbuf[o + 3] = col.a
        _obuf[o] = oc.r
        _obuf[o + 1] = oc.g
        _obuf[o + 2] = oc.b
        _obuf[o + 3] = oc.a
    else:
        (g["mmi"] as MultiMeshInstance3D).multimesh.set_instance_color(slot, col)
        (g["omi"] as MultiMeshInstance3D).multimesh.set_instance_color(slot, oc)
    if vis == SimState.Visual.REWORK:
        _rework[ei] = true
    else:
        _rework.erase(ei)


func _is_big() -> bool:
    return _elems.size() > BIG_MODEL_ELEMENTS


func _recolour_chunk(ci: int) -> void:
    var ch: Dictionary = _chunks[ci]
    for gi in ch["groups"]:
        for ei in (_groups[gi]["elems"] as Array[int]):
            _apply_color(ei)
    ch["col_epoch"] = _color_epoch


## Recolours the chunks (all of them on small models; on big ones the chunks being drawn now, over the next frames:
## the others recolour when they become visible).
func _apply_all() -> void:
    if gs == null or gs.bundle == null:
        return
    _dirty = false
    if _is_big():
        _recolour_queue.clear()
        for ci in _chunks.size():
            var ch: Dictionary = _chunks[ci]
            if (ch["groups"] as Array).is_empty() or int(ch["col_epoch"]) == _color_epoch:
                continue
            if bool(ch["visible"]) and int(ch["lod"]) == 0:
                _recolour_queue.append(ci)
        return
    for ci in _chunks.size():
        if not (_chunks[ci]["groups"] as Array).is_empty():
            _recolour_chunk(ci)


## Recolours queued chunks until the time budget is used up.
func _run_recolour_queue(budget_ms: float) -> void:
    var t0: int = Time.get_ticks_usec()
    while not _recolour_queue.is_empty():
        var ci: int = _recolour_queue.pop_back()
        if int(_chunks[ci]["col_epoch"]) != _color_epoch:
            _recolour_chunk(ci)
        if float(Time.get_ticks_usec() - t0) / 1000.0 > budget_ms:
            break


func _process(delta: float) -> void:
    if _dirty:
        _apply_all()
    if not _recolour_queue.is_empty():
        _run_recolour_queue(RECOLOUR_BUDGET_MS)
    if cull_enabled:
        _cull_timer += delta
        if _cull_timer >= CULL_INTERVAL_S:
            _cull_timer = 0.0
            update_culling()
    if not _hl_boxes.is_empty():
        _hl_time += delta
        _pulse_highlight()
    if not _rework.is_empty():
        _pulse += delta * 5.0
        for ei in _rework:
            var g: Dictionary = _groups[_e_group[ei]]
            (g["mmi"] as MultiMeshInstance3D).multimesh.set_instance_color(_e_slot[ei],
                    _colour(_elems[ei].guid, ei, _e_storey[ei], 0.5 + 0.5 * sin(_pulse)))


## For tests / UI: kind -> instance count (over all chunks).
func instance_counts() -> Dictionary:
    var out: Dictionary = {}
    for g in _groups:
        var k: String = _kinds[int(g["kind"])]
        out[k] = int(out.get(k, 0)) + (g["elems"] as Array[int]).size()
    return out


func instance_colour(guid: String) -> Color:
    var ei: int = int(_slots.get(guid, -1))
    return _a_col[ei] if ei >= 0 else Color(1, 1, 1, 1)


# ------------------------------------------------------------------ progress visuals (WP-Q)

## "height" (grows upward from its base), "length" (grows along its cell run from the first cell) or "count"
## (appears as a whole at COUNT_THRESHOLD) for a `visual` kind.
static func grow_mode(kind: String) -> String:
    if COUNT_KINDS.has(kind):
        return "count"
    if LENGTH_KINDS.has(kind):
        return "length"
    return "height"


static func _mode_name(m: int) -> String:
    return "count" if m == GROW_COUNT else ("length" if m == GROW_LENGTH else "height")


## Done share of an element: crew-days done / estimated crew-days over its (non-virtual) tasks, 0..1. Finished tasks
## count in full (like the kits' layer fills).
func element_fill(guid: String) -> float:
    var done: float = 0.0
    var total: float = 0.0
    var st: TaskStore = gs.runtime
    for t in gs.bundle.tasks_by_element.get(guid, []):
        var task: TaskData = t
        if task.is_virtual:
            continue
        var est: float = maxf(task.estimated_crew_days, 0.01)
        total += est
        var i: int = int(st.index.get(task.task_id, -1))
        if i < 0:
            continue
        var s: int = st.state[i]
        if s == TaskRuntime.State.AWAITING_INSPECTION or s == TaskRuntime.State.DONE or s == TaskRuntime.State.INSPECTED:
            done += task.estimated_crew_days
        else:
            done += minf(st.progress[i], task.estimated_crew_days)
    return clampf(done / total, 0.0, 1.0) if total > 0.0 else 0.0


## Full extent of a non-kit element: {mode, scale (grid units), centre (world), axis (0 = x, 2 = z; length mode)}.
func element_extent(e: ElementData) -> Dictionary:
    var geo: Array = _geometry(e)
    return {"mode": _mode_name(int(geo[0])), "scale": geo[1], "centre": geo[2], "axis": int(geo[3])}


static func _fill_xf(mode: int, centre: Vector3, scale: Vector3, axis: int, f: float) -> Transform3D:
    var sc: Vector3 = scale
    var c: Vector3 = centre
    if mode == GROW_HEIGHT:
        var base_y: float = c.y - sc.y * 0.5
        sc.y *= f
        c.y = base_y + sc.y * 0.5
    elif mode == GROW_LENGTH:
        var start: float = c[axis] - sc[axis] * 0.5
        sc[axis] *= f
        c[axis] = start + sc[axis] * 0.5
    return Transform3D(Basis.from_scale(sc), c)


static func _fill_transform(geo: Dictionary, f: float) -> Transform3D:
    var mode: int = GROW_COUNT if str(geo["mode"]) == "count" else (GROW_LENGTH if str(geo["mode"]) == "length" else GROW_HEIGHT)
    return _fill_xf(mode, geo["centre"], geo["scale"], int(geo["axis"]), f)


## Fill an instance is drawn with: the full extent as a ghost when not started, else the clamped fill.
static func drawn_fill(fill: float, vis: int) -> float:
    if vis == SimState.Visual.GHOST:
        return 1.0
    return clampf(maxf(fill, MIN_FILL), MIN_FILL, 1.0)


func _progress_xf(ei: int, fill: float, vis: int) -> Transform3D:
    if _g_mode[ei] == GROW_COUNT:
        return _fill_xf(GROW_COUNT, _g_centre[ei], _g_scale[ei], _g_axis[ei], 1.0)
    return _fill_xf(_g_mode[ei], _g_centre[ei], _g_scale[ei], _g_axis[ei], drawn_fill(fill, vis))


## Transform of the solid part of a non-kit element at `fill` for a visual state.
func progress_transform(guid: String, fill: float, vis: int) -> Transform3D:
    return _progress_xf(int(_slots[guid]), fill, vis)


## Transform of the instance of `guid` as last written (tests / UI); identity for unknown guids.
func instance_transform(guid: String) -> Transform3D:
    var ei: int = int(_slots.get(guid, -1))
    if ei < 0 or _a_vis[ei] == 255:
        return Transform3D()
    return _progress_xf(ei, _a_fill[ei], _a_vis[ei])


func outline_transform(guid: String) -> Transform3D:
    var ei: int = int(_slots.get(guid, -1))
    if ei < 0:
        return Transform3D(Basis.from_scale(Vector3.ZERO), Vector3.ZERO)
    return _outline_xf(ei)


func _outline_xf(ei: int) -> Transform3D:
    if _a_omode[ei] != OUTLINE_NONE:
        return _fill_xf(_g_mode[ei], _g_centre[ei], _g_scale[ei], _g_axis[ei], 1.0)
    return Transform3D(Basis.from_scale(Vector3.ZERO), _g_centre[ei])


## True while the thin outline box of the full extent is shown for an element.
func outline_visible(guid: String) -> bool:
    return outline_transform(guid).basis.get_scale().x > 0.0


func applied_fill(guid: String) -> float:
    var ei: int = int(_slots.get(guid, -1))
    return maxf(_a_fill[ei], 0.0) if ei >= 0 else 0.0


## Colour of the outline box: discipline colour at GHOST_OUTLINE_ALPHA for not-started elements, OUTLINE_ALPHA around
## the remainder of a part-built one; transparent when the ghost toggle hides ghosts or the outline is off.
func outline_colour(guid: String) -> Color:
    var ei: int = int(_slots.get(guid, -1))
    return _outline_col(guid, ei, _storey_of(guid))


func _outline_col(guid: String, ei: int, storey: int) -> Color:
    return _outline_col_from(base_colour(guid), gs.element_visual(guid), ei, storey)


func _outline_col_from(base: Color, vis: int, ei: int, storey: int, mode_override: int = -1) -> Color:
    var mode: int = mode_override if mode_override >= 0 else (int(_a_omode[ei]) if ei >= 0 else OUTLINE_NONE)
    var col: Color = base
    if vis == SimState.Visual.REWORK:
        col = col.lerp(REWORK_TINT, 0.45)
    if mode == OUTLINE_NONE:
        col.a = 0.0
    elif mode == OUTLINE_GHOST:
        col.a = GHOST_OUTLINE_ALPHA if show_ghost else 0.0
    else:
        col.a = OUTLINE_ALPHA
    if storey > focus_storey_index:
        col.a = minf(col.a, ABOVE_FOCUS_ALPHA)
    return col


## Outline written for an element (tests): the colour including the ghost toggle and the storey fade.
func applied_outline_colour(guid: String) -> Color:
    var ei: int = int(_slots.get(guid, -1))
    return _a_ocol[ei] if ei >= 0 else Color(1, 1, 1, 0)


func _make_outline_instance(node_name: String, count: int) -> MultiMeshInstance3D:
    if _outline_mesh == null:
        var mesh: ArrayMesh = line_box_mesh().duplicate() as ArrayMesh
        var omat: StandardMaterial3D = _line_material(true)
        omat.render_priority = 1  # after the solid parts; alpha materials write no depth, so the lines never occlude them
        mesh.surface_set_material(0, omat)
        _outline_mesh = mesh
    var mm := MultiMesh.new()
    mm.transform_format = MultiMesh.TRANSFORM_3D
    mm.use_colors = true
    mm.mesh = _outline_mesh
    mm.instance_count = count
    var mmi := MultiMeshInstance3D.new()
    mmi.name = "O" + node_name
    mmi.multimesh = mm
    mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
    return mmi


## Outline MultiMeshInstance3D of the first chunk drawing `kind` (tests: material checks); null when none.
func outline_node(kind: String) -> MultiMeshInstance3D:
    for g in _groups:
        if _kinds[int(g["kind"])] == kind:
            return g["omi"]
    return null


## Writes the transform and colour of one non-kit element when its fill moved by more than FILL_EPS or its state
## changed (or `force`). Returns true when the instance was rewritten.
func _apply_element(ei: int, force: bool = false) -> bool:
    var guid: String = _elems[ei].guid
    var vis: int = gs.element_visual(guid)
    var f: float = _fill_of(ei)
    var old_vis: int = _a_vis[ei] if _a_vis[ei] != 255 else -1
    var old_f: float = _a_fill[ei]
    var moved: bool = absf(f - old_f) > FILL_EPS or (f >= 1.0 and old_f < 1.0) or (f <= 0.0 and old_f > 0.0)
    if not force and vis == old_vis and not moved:
        return false
    _a_vis[ei] = vis
    _a_fill[ei] = f
    var g: Dictionary = _groups[_e_group[ei]]
    var slot: int = _e_slot[ei]
    var xf: Transform3D = _progress_xf(ei, f, vis)
    if not _bulk:
        (g["mmi"] as MultiMeshInstance3D).multimesh.set_instance_transform(slot, xf)
    transform_writes += 1
    var omode: int = OUTLINE_NONE
    if vis == SimState.Visual.GHOST:
        omode = OUTLINE_GHOST
    elif _g_mode[ei] == GROW_COUNT:
        omode = OUTLINE_GHOST if f < COUNT_THRESHOLD else OUTLINE_NONE
    elif f < 1.0 - 0.001:
        omode = OUTLINE_REMAINING
    _a_omode[ei] = omode
    var oxf: Transform3D = _outline_xf(ei)
    if _bulk:
        var ob: int = (int(g["base"]) + slot) * 16
        _put_xf(_xbuf, ob, xf)
        _put_xf(_obuf, ob, oxf)
    else:
        (g["omi"] as MultiMeshInstance3D).multimesh.set_instance_transform(slot, oxf)
    _apply_color(ei)
    # far LOD: the cells it covers carry the change
    if _e_tasked[ei] != 0:
        var delta: float = f - maxf(old_f, 0.0)
        if delta != 0.0:
            for k in range(_e_cell_off[ei], _e_cell_off[ei + 1]):
                var cid: int = _e_cells[k]
                _c_sum[cid] += delta
                _far_dirty[cid] = true
    return true


## 3x4 row-major transform floats of a MultiMesh buffer.
static func _put_xf(buf: PackedFloat32Array, o: int, xf: Transform3D) -> void:
    var b: Basis = xf.basis
    buf[o] = b.x.x
    buf[o + 1] = b.y.x
    buf[o + 2] = b.z.x
    buf[o + 3] = xf.origin.x
    buf[o + 4] = b.x.y
    buf[o + 5] = b.y.y
    buf[o + 6] = b.z.y
    buf[o + 7] = xf.origin.y
    buf[o + 8] = b.x.z
    buf[o + 9] = b.y.z
    buf[o + 10] = b.z.z
    buf[o + 11] = xf.origin.z


## Done share of a non-kit element by element index (see element_fill).
func _fill_of(ei: int) -> float:
    return element_fill(_elems[ei].guid)


## Recomputes the fill of the elements whose tasks changed since the last call (the TaskStore change log) and rewrites
## the instances that moved (called every week and after a task-list change). Returns the number of instances rewritten.
func refresh_progress() -> int:
    if gs == null or gs.bundle == null:
        return 0
    var t0: int = Time.get_ticks_usec()
    var n: int = 0
    var st: TaskStore = gs.runtime
    var changed: Variant = null
    if st.log_epoch == _log_epoch:
        changed = st.changed_set_since(_log_cursor)
    _log_cursor = st.log_end()
    _log_epoch = st.log_epoch
    if changed == null:  # the log was cut or the store reset: look at every element
        for ei in _elems.size():
            if _apply_element(ei):
                n += 1
    else:
        var dirty: Dictionary = {}
        for slot in (changed as Dictionary):
            var task: TaskData = st.task_refs[slot]
            if task == null or task.is_virtual:
                continue
            for g in task.element_guids:
                var ei2: int = int(_slots.get(g, -1))
                if ei2 >= 0:
                    dirty[ei2] = true
        for ei3 in dirty:
            if _apply_element(ei3):
                n += 1
    _flush_far()
    last_refresh_writes = n
    last_refresh_ms = float(Time.get_ticks_usec() - t0) / 1000.0
    last_week_ms = last_refresh_ms + float(_signal_us) / 1000.0
    _signal_us = 0
    return n


static var _line_box: ArrayMesh = null


## 12 edges of the unit cube.
static func line_box_mesh() -> ArrayMesh:
    if _line_box != null:
        return _line_box
    var p: PackedVector3Array = PackedVector3Array()
    for i in 8:
        p.append(Vector3(-0.5 if (i & 1) == 0 else 0.5, -0.5 if (i & 2) == 0 else 0.5, -0.5 if (i & 4) == 0 else 0.5))
    var verts: PackedVector3Array = PackedVector3Array()
    for a in 8:
        for bit in [1, 2, 4]:
            var b: int = a | bit
            if b != a:
                verts.append(p[a])
                verts.append(p[b])
    var arrays: Array = []
    arrays.resize(Mesh.ARRAY_MAX)
    arrays[Mesh.ARRAY_VERTEX] = verts
    _line_box = ArrayMesh.new()
    _line_box.add_surface_from_arrays(Mesh.PRIMITIVE_LINES, arrays)
    return _line_box


func _line_material(vertex_colours: bool) -> StandardMaterial3D:
    var m := StandardMaterial3D.new()
    m.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
    m.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
    m.vertex_color_use_as_albedo = vertex_colours
    return m


# ------------------------------------------------------------------ far LOD cubes

func _far_box() -> BoxMesh:
    if _far_mesh == null:
        _far_mesh = BoxMesh.new()
        _far_mesh.size = Vector3.ONE
        _far_material = StandardMaterial3D.new()
        _far_material.vertex_color_use_as_albedo = true
        _far_material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA_DEPTH_PRE_PASS
        _far_material.shading_mode = BaseMaterial3D.SHADING_MODE_PER_PIXEL
        _far_material.roughness = 0.9
        _far_mesh.surface_set_material(0, _far_material)
    return _far_mesh


## Tint of a cell cube: the mean fill of the tasked elements on the cell through the heat palette (grey 0 %, amber, green
## 100 %); cells without tasked elements stay a faint ghost.
func far_cell_colour(cid: int) -> Color:
    var n: int = _c_n[cid]
    if n <= 0:
        var g: Color = FAR_GHOST
        if not show_ghost:
            g.a = 0.0
        return g
    var share: float = clampf(_c_sum[cid] / float(n), 0.0, 1.0)
    var col: Color = CellHeatOverlay.heat_colour(share)
    col.a = lerpf(0.35, 0.95, share)
    if share <= 0.0 and not show_ghost:
        col.a = 0.0
    return col


func _ensure_far(ci: int) -> void:
    var ch: Dictionary = _chunks[ci]
    if ch["far"] != null:
        return
    var cells: Array[int] = ch["cells"]
    var mm := MultiMesh.new()
    mm.transform_format = MultiMesh.TRANSFORM_3D
    mm.use_colors = true
    mm.mesh = _far_box()
    mm.instance_count = cells.size()
    var h: float = gs.bundle.storey_height_m / gs.bundle.cell_size_m * 0.9
    var y0: float = gs.bundle.storey_y_for_index(int(ch["storey"]))
    for k in cells.size():
        var cid: int = cells[k]
        mm.set_instance_transform(k, Transform3D(Basis.from_scale(Vector3(0.94, h, 0.94)),
                Vector3(float(_c_x[cid]), y0 + h * 0.5, float(_c_z[cid]))))
    var mmi := MultiMeshInstance3D.new()
    mmi.name = "Far_%d_%d_%d" % [int(ch["storey"]), int(ch["cx"]), int(ch["cz"])]
    mmi.multimesh = mm
    mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
    mmi.visible = false
    _chunk_root.add_child(mmi)
    ch["far"] = mmi
    ch["far_epoch"] = -1


func _recolour_far(ci: int) -> void:
    var ch: Dictionary = _chunks[ci]
    var mm: MultiMesh = (ch["far"] as MultiMeshInstance3D).multimesh
    var cells: Array[int] = ch["cells"]
    for k in cells.size():
        mm.set_instance_color(k, far_cell_colour(cells[k]))
    ch["far_epoch"] = _color_epoch


## Pushes the cells whose share changed to the cube MultiMeshes that exist (chunks without one catch up when drawn).
func _flush_far() -> void:
    if _far_dirty.is_empty():
        return
    for cid in _far_dirty:
        var ch: Dictionary = _chunks[_c_chunk[cid]]
        if ch["far"] != null and int(ch["far_epoch"]) == _color_epoch:
            ((ch["far"] as MultiMeshInstance3D).multimesh).set_instance_color(_c_slot[cid], far_cell_colour(cid))
        else:
            ch["far_epoch"] = -1
    _far_dirty.clear()


# ------------------------------------------------------------------ culling and LOD

## Number of chunks / MultiMesh groups / far cube sets built.
func chunk_count() -> int:
    return _chunks.size()


func group_count() -> int:
    return _groups.size()


func tile_count() -> int:
    return _tiles.size()


func far_node_count() -> int:
    var n: int = 0
    for ch in _chunks:
        if ch["far"] != null:
            n += 1
    return n


## State of a chunk: {visible, far (bool), storey, instances}.
func chunk_state(ci: int) -> Dictionary:
    var ch: Dictionary = _chunks[ci]
    var n: int = 0
    for gi in ch["groups"]:
        n += (_groups[gi]["elems"] as Array[int]).size()
    return {"visible": bool(ch["visible"]), "far": int(ch["lod"]) == 1, "storey": int(ch["storey"]), "instances": n,
            "cells": (ch["cells"] as Array[int]).size()}


## Index of the chunk holding the element (-1 for kit elements and unknown guids).
func chunk_of(guid: String) -> int:
    var ei: int = int(_slots.get(guid, -1))
    return int(_groups[_e_group[ei]]["chunk"]) if ei >= 0 else -1


func _set_chunk(ci: int, visible: bool, lod: int) -> void:
    var ch: Dictionary = _chunks[ci]
    var was_visible: bool = bool(ch["visible"])
    var was_lod: int = int(ch["lod"])
    if was_visible == visible and was_lod == lod:
        return
    ch["visible"] = visible
    ch["lod"] = lod
    var near: bool = visible and lod == 0
    var far: bool = visible and lod == 1
    if near != (was_visible and was_lod == 0):
        if _heat != null:
            _heat.chunk_changed(int(ch["storey"]), int(ch["cx"]), int(ch["cz"]), near)
        for gi in ch["groups"]:
            var g: Dictionary = _groups[gi]
            (g["mmi"] as MultiMeshInstance3D).visible = near
            (g["omi"] as MultiMeshInstance3D).visible = near
    if near and int(ch["col_epoch"]) != _color_epoch and not (ch["groups"] as Array).is_empty():
        if _is_big():
            _recolour_queue.append(ci)
        else:
            _recolour_chunk(ci)
    if far:
        _ensure_far(ci)
        if int(ch["far_epoch"]) != _color_epoch:
            _recolour_far(ci)
        (ch["far"] as MultiMeshInstance3D).visible = true
    elif ch["far"] != null and was_visible and was_lod == 1:
        (ch["far"] as MultiMeshInstance3D).visible = false


## Show / hide and LOD pass over the chunks for the camera: hides chunks outside the frustum (a two-level test: tiles of
## TILE_CHUNKS x TILE_CHUNKS chunks first), the storeys above the focus on big models, and swaps chunks beyond
## `lod_far_distance` for cell cubes. Does nothing when the camera did not move and nothing else changed unless `force`.
## Returns / stores {chunks, visible, near, far, hidden, ms}.
func update_culling(cam: Camera3D = null, force: bool = false) -> Dictionary:
    if _chunks.is_empty():
        return {}
    if cam == null and is_inside_tree():
        cam = get_viewport().get_camera_3d()
    if cam == null:
        return {}
    var t0: int = Time.get_ticks_usec()
    var in_tree: bool = cam.is_inside_tree()
    var xf: Transform3D = cam.global_transform if in_tree else cam.transform
    if not cull_enabled:
        if force or _cull_dirty:
            for ci in _chunks.size():
                _set_chunk(ci, true, 0)
            _cull_dirty = false
        return last_cull
    if not force and not _cull_dirty and xf.is_equal_approx(_last_cam_xf) and is_equal_approx(cam.fov, _last_cam_fov):
        return last_cull
    _last_cam_xf = xf
    _last_cam_fov = cam.fov
    _cull_dirty = false
    var planes: Array = cam.get_frustum() if in_tree else []
    var pos: Vector3 = xf.origin
    var hide_above: bool = cull_above_focus == 1 or (cull_above_focus == -1 and _is_big())
    var far_d: float = lod_far_distance
    var visible_n: int = 0
    var near_n: int = 0
    var far_n: int = 0
    var far_builds: int = 0
    for tile in _tiles:
        var tc: Vector3 = tile["centre"]
        var tr: float = float(tile["radius"])
        var state: int = _sphere_vs_frustum(planes, tc, tr)  # 0 outside, 1 inside, 2 partial
        var list: Array[int] = tile["chunks"]
        if state == 0:
            for ci in list:
                _set_chunk(ci, false, int(_chunks[ci]["lod"]))
            continue
        for ci in list:
            var ch: Dictionary = _chunks[ci]
            var vis: bool = true
            if hide_above and int(ch["storey"]) > focus_storey_index:
                vis = false
            elif state == 2 and _sphere_vs_frustum(planes, ch["centre"], float(ch["radius"])) == 0:
                vis = false
            var lod: int = int(ch["lod"])
            if vis:
                var d: float = pos.distance_to(ch["centre"])
                if lod == 1:
                    lod = 1 if d > far_d * LOD_HYSTERESIS else 0
                else:
                    lod = 1 if d > far_d else 0
                if lod == 1 and ch["far"] == null:
                    if far_builds >= FAR_BUILDS_PER_PASS:
                        lod = 0  # keep drawing the elements until the cubes are built (next pass)
                        _cull_dirty = true
                    else:
                        far_builds += 1
                visible_n += 1
                if lod == 1:
                    far_n += 1
                else:
                    near_n += 1
            _set_chunk(ci, vis, lod)
    last_cull = {"chunks": _chunks.size(), "visible": visible_n, "near": near_n, "far": far_n,
            "hidden": _chunks.size() - visible_n, "ms": float(Time.get_ticks_usec() - t0) / 1000.0}
    return last_cull


## 0 = the sphere is outside the frustum, 1 = fully inside, 2 = straddles a plane (no planes: inside).
static func _sphere_vs_frustum(planes: Array, centre: Vector3, radius: float) -> int:
    var inside: bool = true
    for p in planes:
        var d: float = (p as Plane).distance_to(centre)
        if d > radius:
            return 0
        if d > -radius:
            inside = false
    return 1 if inside else 2


# ------------------------------------------------------------------ heat overlay

func heat_overlay() -> CellHeatOverlay:
    return _heat


## Shows / hides the per-cell progress heat overlay (key H, API view.set_heat).
func set_heat_visible(v: bool) -> void:
    if _heat != null:
        _heat.set_heat_visible(v)


func is_heat_visible() -> bool:
    return _heat != null and _heat.heat_visible


func _unhandled_input(event: InputEvent) -> void:
    if InputMap.has_action("heat_toggle") and event.is_action_pressed("heat_toggle"):
        set_heat_visible(not is_heat_visible())


# ------------------------------------------------------------------ highlight

## World-space box (centre, size in grid units) around an element, or around the kit instance that draws it.
## Returns an empty dictionary for an unknown element. `key` identifies the box (kit instances are shared).
func element_box(guid: String) -> Dictionary:
    if _slots.has(guid):
        var gi: int = int(_slots[guid])
        return {"key": guid, "centre": _g_centre[gi], "size": _g_scale[gi]}
    if _kit_layer != null and _kit_layer.handles(guid):
        var ki: KitInstances = _kit_layer.kit_instances
        var i: int = ki.instance_of(guid)
        var inst: Dictionary = ki.instances()[i]
        var r: Rect2i = inst["rect"]
        var cell_m: float = gs.bundle.cell_size_m
        var h: float = float(inst["height_m"]) / cell_m
        var y0: float = gs.bundle.storey_y_for_index(int(inst["storey_index"]))
        return {
            "key": "kit%d" % i,
            "centre": Vector3(float(r.position.x) + float(r.size.x - 1) * 0.5, y0 + h * 0.5, float(r.position.y) + float(r.size.y - 1) * 0.5),
            "size": Vector3(float(r.size.x), h, float(r.size.y)),
        }
    if gs != null and gs.bundle != null and gs.bundle.elements_by_guid.has(guid):  # kit element without an instance
        var e: ElementData = gs.bundle.elements_by_guid[guid]
        if not e.cells.is_empty():
            var ex: Dictionary = element_extent(e)
            return {"key": guid, "centre": ex["centre"], "size": ex["scale"]}
    return {}


## Draws a pulsing outline box around each of the elements (or kit instances containing them). Replaces the previous
## highlight. Returns the number of boxes drawn.
func highlight_elements(guids: Array, color: Color = HIGHLIGHT_COLOR) -> int:
    clear_highlight()
    if gs == null or gs.bundle == null:
        return 0
    _hl_color = color
    var seen: Dictionary = {}
    for g in guids:
        var box: Dictionary = element_box(str(g))
        if box.is_empty() or seen.has(box["key"]):
            continue
        seen[box["key"]] = true
        var sz: Vector3 = (box["size"] as Vector3) + Vector3(0.06, 0.06, 0.06)
        sz = sz.max(Vector3(0.2, 0.2, 0.2))
        _hl_boxes.append(Transform3D(Basis.from_scale(sz), box["centre"]))
    if _hl_boxes.is_empty():
        return 0
    if _hl_root == null:
        _hl_root = Node3D.new()
        _hl_root.name = "Highlight"
        add_child(_hl_root)
        _hl_line_mat = _line_material(false)
        _hl_line_mat.no_depth_test = true
        _hl_fill_mat = _line_material(false)
        _hl_fill_mat.cull_mode = BaseMaterial3D.CULL_DISABLED
    _hl_lines = _make_highlight_instance("HighlightLines", line_box_mesh().duplicate() as Mesh, _hl_line_mat)
    var box_mesh := BoxMesh.new()
    box_mesh.size = Vector3.ONE
    _hl_fill = _make_highlight_instance("HighlightFill", box_mesh, _hl_fill_mat)
    _hl_time = 0.0
    _pulse_highlight()
    return _hl_boxes.size()


func _make_highlight_instance(node_name: String, mesh: Mesh, mat: StandardMaterial3D) -> MultiMeshInstance3D:
    mesh.surface_set_material(0, mat)
    var mm := MultiMesh.new()
    mm.transform_format = MultiMesh.TRANSFORM_3D
    mm.mesh = mesh
    mm.instance_count = _hl_boxes.size()
    for i in _hl_boxes.size():
        mm.set_instance_transform(i, _hl_boxes[i])
    var mmi := MultiMeshInstance3D.new()
    mmi.name = node_name
    mmi.multimesh = mm
    mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
    _hl_root.add_child(mmi)
    return mmi


func _pulse_highlight() -> void:
    if _hl_line_mat == null:
        return
    var k: float = 0.5 + 0.5 * sin(_hl_time * 5.0)
    var c: Color = _hl_color
    c.a = 0.55 + 0.45 * k
    _hl_line_mat.albedo_color = c
    c.a = 0.08 + 0.2 * k
    _hl_fill_mat.albedo_color = c


func clear_highlight() -> void:
    _hl_boxes.clear()
    for n in [_hl_lines, _hl_fill]:
        if n != null:
            (n as Node).queue_free()
    _hl_lines = null
    _hl_fill = null


## Number of highlight boxes drawn (the highlight instance count).
func highlight_count() -> int:
    return _hl_boxes.size()


func highlight_instance_count() -> int:
    return _hl_lines.multimesh.instance_count if _hl_lines != null and is_instance_valid(_hl_lines) else 0


# >>> visual kits (WP-O) ------------------------------------------------------------------------
## The KitLayer drawing the kit instances (null when use_kits is off): picking, installations panel, API.
func kit_layer() -> KitLayer:
    return _kit_layer


## (Re)creates the KitLayer for the current bundle and installs the marker mesh provider. With use_kits
## off every element goes through the generic MultiMesh pass again.
func _kits_rebuild() -> void:
    if _kit_layer != null:
        _kit_layer.queue_free()
        _kit_layer = null
    if not use_kits:
        return
    var reg := KitRegistry.new()
    if not reg.load_manifest():
        push_warning("visual kits disabled: %s" % ", ".join(reg.errors))
        return
    _kit_layer = KitLayer.new()
    _kit_layer.name = "KitLayer"
    add_child(_kit_layer)
    _kit_layer.setup(gs, reg)
    _kit_layer.set_focus_storey(focus_storey_index)
    _kit_layer.set_ghost_visible(show_ghost)
    # marker kit meshes for virtual tasks, unless another provider is already installed
    if not marker_mesh_provider.is_valid():
        marker_mesh_provider = Callable(reg, "marker_mesh")
# <<< visual kits (WP-O)
