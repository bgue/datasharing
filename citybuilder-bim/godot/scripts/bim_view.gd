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

var gs: SimState = null
## Optional hook for a marker kit: `func(marker: String) -> Mesh`; a null result falls back to the placeholder cylinder.
var marker_mesh_provider: Callable = Callable()
# >>> visual kits (WP-O): elements with a kit (scripts/kits/) are drawn by KitLayer instead of the MultiMesh pass
var use_kits: bool = true
var _kit_layer: KitLayer = null
# <<< visual kits
var show_ghost: bool = true
var focus_storey_index: int = 0

var _instances: Dictionary = {}  # kind -> MultiMeshInstance3D
var _slots: Dictionary = {}  # guid -> {kind, slot}
var _kind_elements: Dictionary = {}  # kind -> Array[ElementData]
var _storey_index: Dictionary = {}  # guid -> int
var _rework_guids: Dictionary = {}
var _dirty: bool = true
var _pulse: float = 0.0
var _material: StandardMaterial3D = null
var _marker_root: Node3D = null
var _marker_nodes: Dictionary = {}  # task_id -> MeshInstance3D
var _marker_default_mesh: CylinderMesh = null
# progress visuals (WP-Q)
var _outlines: Dictionary = {}  # kind -> MultiMeshInstance3D (thin box outline of the full extent, same slots)
var _geo: Dictionary = {}  # guid -> {mode, scale, centre, axis}
var _fill_applied: Dictionary = {}  # guid -> fill the instance was last written with
var _vis_applied: Dictionary = {}  # guid -> SimState.Visual the instance was last written with
# CPU-side copies of what was written to the MultiMeshes (the headless renderer keeps no instance data)
var _xf_written: Dictionary = {}  # guid -> Transform3D of the solid part
var _outline_written: Dictionary = {}  # guid -> Transform3D of the outline box (zero scale when hidden)
var _col_written: Dictionary = {}  # guid -> Color of the solid part
var _outline_mode: Dictionary = {}  # guid -> OUTLINE_NONE / OUTLINE_GHOST / OUTLINE_REMAINING
var _outline_col_written: Dictionary = {}  # guid -> Color of the outline box
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
    _dirty = true
    if _kit_layer != null:  # visual kits (WP-O)
        _kit_layer.set_ghost_visible(v)


func set_focus_storey(idx: int) -> void:
    focus_storey_index = idx
    _dirty = true
    if _heat != null:
        _heat.set_focus_storey(idx)
    if _kit_layer != null:  # visual kits (WP-O)
        _kit_layer.set_focus_storey(idx)


func _on_task_state_changed(task_id: String, _old: int, _new: int) -> void:
    var t: TaskData = gs.bundle.tasks_by_id.get(task_id, null)
    if t == null:
        return
    if t.is_virtual:
        _apply_marker_state(task_id)
        return
    var t0: int = Time.get_ticks_usec()
    for g in t.element_guids:
        _apply_element(g)
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
    var rt: TaskRuntime = gs.runtime.get(task_id, null)
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


func element_transform(e: ElementData) -> Transform3D:
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
    # grid cells are centred on integer coordinates (Kenney GridMap, cell_center = false)
    return Transform3D(Basis.from_scale(size_u), Vector3(cx / n, y, cz / n))


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


func _build() -> void:
    for k in _instances:
        (_instances[k] as Node).queue_free()
    _instances.clear()
    for k in _outlines:
        (_outlines[k] as Node).queue_free()
    _outlines.clear()
    _geo.clear()
    _fill_applied.clear()
    _vis_applied.clear()
    _xf_written.clear()
    _outline_written.clear()
    _col_written.clear()
    _outline_mode.clear()
    _outline_col_written.clear()
    clear_highlight()
    _slots.clear()
    _kind_elements.clear()
    _storey_index.clear()
    _rework_guids.clear()
    if gs == null or gs.bundle == null:
        return
    _kits_rebuild()  # visual kits (WP-O)
    for e in gs.bundle.elements:
        if _kit_layer != null and _kit_layer.handles(e.guid):  # visual kits (WP-O): drawn by KitLayer
            _storey_index[e.guid] = int(gs.bundle.storey_index_by_id.get(e.storey_id, 0))
            continue
        if not _kind_elements.has(e.visual):
            _kind_elements[e.visual] = [] as Array[ElementData]
        (_kind_elements[e.visual] as Array[ElementData]).append(e)
        _storey_index[e.guid] = int(gs.bundle.storey_index_by_id.get(e.storey_id, 0))
    for kind in _kind_elements:
        var list: Array[ElementData] = _kind_elements[kind]
        var mm := MultiMesh.new()
        mm.transform_format = MultiMesh.TRANSFORM_3D
        mm.use_colors = true
        mm.mesh = _make_mesh(kind)
        mm.instance_count = list.size()
        for i in list.size():
            mm.set_instance_transform(i, element_transform(list[i]))
            _slots[list[i].guid] = {"kind": kind, "slot": i}
            _geo[list[i].guid] = element_extent(list[i])
        var mmi := MultiMeshInstance3D.new()
        mmi.name = "Kind_%s" % kind
        mmi.multimesh = mm
        mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
        add_child(mmi)
        _instances[kind] = mmi
        _outlines[kind] = _make_outline_instance(kind, list.size())
    _dirty = true
    for guid in _slots:
        _apply_element(guid, true)
    _apply_all()
    _rebuild_markers()


# ------------------------------------------------------------------ colours

func base_colour(guid: String) -> Color:
    var disc: String = gs.bundle.element_discipline(guid)
    return DISCIPLINE_COLORS.get(disc, DISCIPLINE_COLORS["general"])


## Final instance colour (alpha included) for an element.
func compute_colour(guid: String, pulse: float = 1.0) -> Color:
    var col: Color = base_colour(guid)
    var vis: int = gs.element_visual(guid)
    var a: float = 1.0
    match vis:
        SimState.Visual.GHOST:
            a = GHOST_ALPHA if int(_storey_index.get(guid, 0)) == focus_storey_index else 0.0
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
    if (vis == SimState.Visual.FRAMED or vis == SimState.Visual.SOLID) and _geo.has(guid) \
            and str((_geo[guid] as Dictionary)["mode"]) == "count":
        a = (GHOST_ALPHA if int(_storey_index.get(guid, 0)) == focus_storey_index else 0.0) \
                if float(_fill_applied.get(guid, 0.0)) < COUNT_THRESHOLD else 1.0
    if int(_storey_index.get(guid, 0)) > focus_storey_index:
        a = minf(a, ABOVE_FOCUS_ALPHA)
    if vis == SimState.Visual.GHOST and not show_ghost:
        a = 0.0
    col.a = a
    return col


func _apply_color(guid: String) -> void:
    if not _slots.has(guid):
        return
    var s: Dictionary = _slots[guid]
    var mm: MultiMesh = (_instances[s["kind"]] as MultiMeshInstance3D).multimesh
    var col: Color = compute_colour(guid, 0.5 + 0.5 * sin(_pulse))
    _col_written[guid] = col
    mm.set_instance_color(int(s["slot"]), col)
    if _outlines.has(s["kind"]):
        var oc: Color = outline_colour(guid)
        _outline_col_written[guid] = oc
        (_outlines[s["kind"]] as MultiMeshInstance3D).multimesh.set_instance_color(int(s["slot"]), oc)
    if gs.element_visual(guid) == SimState.Visual.REWORK:
        _rework_guids[guid] = true
    else:
        _rework_guids.erase(guid)


func _apply_all() -> void:
    if gs == null or gs.bundle == null:
        return
    for guid in _slots:
        _apply_color(guid)
    _dirty = false


func _process(delta: float) -> void:
    if _dirty:
        _apply_all()
    if not _hl_boxes.is_empty():
        _hl_time += delta
        _pulse_highlight()
    if not _rework_guids.is_empty():
        _pulse += delta * 5.0
        for guid in _rework_guids:
            var s: Dictionary = _slots[guid]
            (_instances[s["kind"]] as MultiMeshInstance3D).multimesh.set_instance_color(
                    int(s["slot"]), compute_colour(guid, 0.5 + 0.5 * sin(_pulse)))


## For tests / UI: kind -> instance count.
func instance_counts() -> Dictionary:
    var out: Dictionary = {}
    for k in _instances:
        out[k] = (_instances[k] as MultiMeshInstance3D).multimesh.instance_count
    return out


func instance_colour(guid: String) -> Color:
    return _col_written.get(guid, Color(1, 1, 1, 1))


# ------------------------------------------------------------------ progress visuals (WP-Q)

## "height" (grows upward from its base), "length" (grows along its cell run from the first cell) or "count"
## (appears as a whole at COUNT_THRESHOLD) for a `visual` kind.
static func grow_mode(kind: String) -> String:
    if COUNT_KINDS.has(kind):
        return "count"
    if LENGTH_KINDS.has(kind):
        return "length"
    return "height"


## Done share of an element: crew-days done / estimated crew-days over its (non-virtual) tasks, 0..1. Finished tasks
## count in full (like the kits' layer fills).
func element_fill(guid: String) -> float:
    var done: float = 0.0
    var total: float = 0.0
    for t in gs.bundle.tasks_by_element.get(guid, []):
        var task: TaskData = t
        if task.is_virtual:
            continue
        var est: float = maxf(task.estimated_crew_days, 0.01)
        total += est
        done += CellHeatOverlay.task_done(gs, task)
    return clampf(done / total, 0.0, 1.0) if total > 0.0 else 0.0


## Full extent of a non-kit element: {mode, scale (grid units), centre (world), axis (0 = x, 2 = z; length mode)}.
## Linear kinds are oriented along the dominant axis of their cells, flat kinds extend along the longer cell axis.
func element_extent(e: ElementData) -> Dictionary:
    var base: Transform3D = element_transform(e)
    var mode: String = grow_mode(e.visual)
    var out: Dictionary = {"mode": mode, "scale": base.basis.get_scale(), "centre": base.origin, "axis": 0}
    if mode != "length":
        return out
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
    var sc: Vector3 = out["scale"]
    var axis: int = 0
    if d > w:
        axis = 2
    elif d == w and LINEAR_KINDS.has(e.visual):
        axis = 2 if sc.z > sc.x else 0
    if LINEAR_KINDS.has(e.visual):
        var size_m: Vector3 = e.size_hint if e.has_size_hint else default_size(e.visual, e.cells, b.cell_size_m, b.storey_height_m)
        var len_u: float = maxf(size_m.x, size_m.z) / b.cell_size_m
        var thick_u: float = minf(size_m.x, size_m.z) / b.cell_size_m
        if not e.has_size_hint:  # default sizes carry the x extent only: use the cell run
            len_u = float(maxi(w, d))
            thick_u = size_m.z / b.cell_size_m
        sc = Vector3(len_u, sc.y, thick_u) if axis == 0 else Vector3(thick_u, sc.y, len_u)
    out["scale"] = sc
    out["axis"] = axis
    return out


static func _fill_transform(geo: Dictionary, f: float) -> Transform3D:
    var sc: Vector3 = geo["scale"]
    var c: Vector3 = geo["centre"]
    match str(geo["mode"]):
        "height":
            var base_y: float = c.y - sc.y * 0.5
            sc.y *= f
            c.y = base_y + sc.y * 0.5
        "length":
            var ax: int = int(geo["axis"])
            var start: float = c[ax] - sc[ax] * 0.5
            sc[ax] *= f
            c[ax] = start + sc[ax] * 0.5
    return Transform3D(Basis.from_scale(sc), c)


## Fill an instance is drawn with: the full extent as a ghost when not started, else the clamped fill.
static func drawn_fill(fill: float, vis: int) -> float:
    if vis == SimState.Visual.GHOST:
        return 1.0
    return clampf(maxf(fill, MIN_FILL), MIN_FILL, 1.0)


## Transform of the solid part of a non-kit element at `fill` for a visual state (null-safe for unknown guids).
func progress_transform(guid: String, fill: float, vis: int) -> Transform3D:
    var geo: Dictionary = _geo[guid]
    if str(geo["mode"]) == "count":
        return _fill_transform(geo, 1.0)
    return _fill_transform(geo, drawn_fill(fill, vis))


## Transform of the instance of `guid` as last written (tests / UI); identity for unknown guids.
func instance_transform(guid: String) -> Transform3D:
    return _xf_written.get(guid, Transform3D())


func outline_transform(guid: String) -> Transform3D:
    return _outline_written.get(guid, Transform3D(Basis.from_scale(Vector3.ZERO), Vector3.ZERO))


## True while the thin outline box of the full extent is shown for an element.
func outline_visible(guid: String) -> bool:
    return outline_transform(guid).basis.get_scale().x > 0.0


func applied_fill(guid: String) -> float:
    return float(_fill_applied.get(guid, 0.0))


## Colour of the outline box: discipline colour at GHOST_OUTLINE_ALPHA for not-started elements, OUTLINE_ALPHA around
## the remainder of a part-built one; transparent when the ghost toggle hides ghosts or the outline is off.
func outline_colour(guid: String) -> Color:
    var mode: int = int(_outline_mode.get(guid, OUTLINE_NONE))
    var col: Color = base_colour(guid)
    if gs.element_visual(guid) == SimState.Visual.REWORK:
        col = col.lerp(REWORK_TINT, 0.45)
    if mode == OUTLINE_NONE:
        col.a = 0.0
    elif mode == OUTLINE_GHOST:
        col.a = GHOST_OUTLINE_ALPHA if show_ghost else 0.0
    else:
        col.a = OUTLINE_ALPHA
    if int(_storey_index.get(guid, 0)) > focus_storey_index:
        col.a = minf(col.a, ABOVE_FOCUS_ALPHA)
    return col


## Outline written for an element (tests): the colour including the ghost toggle and the storey fade.
func applied_outline_colour(guid: String) -> Color:
    return _outline_col_written.get(guid, Color(1, 1, 1, 0))


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


func _make_outline_instance(kind: String, count: int) -> MultiMeshInstance3D:
    var mm := MultiMesh.new()
    mm.transform_format = MultiMesh.TRANSFORM_3D
    mm.use_colors = true
    var mesh: ArrayMesh = line_box_mesh().duplicate() as ArrayMesh
    var omat: StandardMaterial3D = _line_material(true)
    omat.render_priority = 1  # after the solid parts; alpha materials write no depth, so the lines never occlude them
    mesh.surface_set_material(0, omat)
    mm.mesh = mesh
    mm.instance_count = count
    var mmi := MultiMeshInstance3D.new()
    mmi.name = "Outline_%s" % kind
    mmi.multimesh = mm
    mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
    add_child(mmi)
    return mmi


## Writes the transform and colour of one non-kit element when its fill moved by more than FILL_EPS or its state
## changed (or `force`). Returns true when the instance was rewritten.
func _apply_element(guid: String, force: bool = false) -> bool:
    if not _slots.has(guid):
        return false
    var vis: int = gs.element_visual(guid)
    var f: float = element_fill(guid)
    var old_vis: int = int(_vis_applied.get(guid, -1))
    var old_f: float = float(_fill_applied.get(guid, -1.0))
    var moved: bool = absf(f - old_f) > FILL_EPS or (f >= 1.0 and old_f < 1.0) or (f <= 0.0 and old_f > 0.0)
    if not force and vis == old_vis and not moved:
        return false
    _vis_applied[guid] = vis
    _fill_applied[guid] = f
    var s: Dictionary = _slots[guid]
    var slot: int = int(s["slot"])
    var mm: MultiMesh = (_instances[s["kind"]] as MultiMeshInstance3D).multimesh
    var xf: Transform3D = progress_transform(guid, f, vis)
    _xf_written[guid] = xf
    mm.set_instance_transform(slot, xf)
    transform_writes += 1
    if _outlines.has(s["kind"]):
        var omm: MultiMesh = (_outlines[s["kind"]] as MultiMeshInstance3D).multimesh
        var geo: Dictionary = _geo[guid]
        var omode: int = OUTLINE_NONE
        if vis == SimState.Visual.GHOST:
            omode = OUTLINE_GHOST
        elif str(geo["mode"]) == "count":
            omode = OUTLINE_GHOST if f < COUNT_THRESHOLD else OUTLINE_NONE
        elif f < 1.0 - 0.001:
            omode = OUTLINE_REMAINING
        _outline_mode[guid] = omode
        var oxf: Transform3D
        if omode != OUTLINE_NONE:
            oxf = _fill_transform(geo, 1.0)
        else:
            oxf = Transform3D(Basis.from_scale(Vector3.ZERO), (geo["centre"] as Vector3))
        _outline_written[guid] = oxf
        omm.set_instance_transform(slot, oxf)
    _apply_color(guid)
    return true


## Recomputes the fill of every non-kit element and rewrites the instances that changed (called every week, after
## a task or task-list change). Returns the number of instances rewritten.
func refresh_progress() -> int:
    if gs == null or gs.bundle == null:
        return 0
    var t0: int = Time.get_ticks_usec()
    var n: int = 0
    for guid in _slots:
        if _apply_element(guid):
            n += 1
    last_refresh_writes = n
    last_refresh_ms = float(Time.get_ticks_usec() - t0) / 1000.0
    last_week_ms = last_refresh_ms + float(_signal_us) / 1000.0
    _signal_us = 0
    return n


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
    if _geo.has(guid):
        var g: Dictionary = _geo[guid]
        var sc: Vector3 = g["scale"]
        return {"key": guid, "centre": g["centre"], "size": sc}
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
