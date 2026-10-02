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
const GHOST_ALPHA: float = 0.15
const FRAMED_ALPHA: float = 0.5
const ABOVE_FOCUS_ALPHA: float = 0.07

var gs: SimState = null
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


func setup(state: SimState) -> void:
    gs = state
    _build()
    gs.task_state_changed.connect(_on_task_state_changed)
    gs.level_started.connect(func() -> void: _build())
    gs.week_advanced.connect(func(_w: int) -> void: _dirty = true)


func set_ghost_visible(v: bool) -> void:
    show_ghost = v
    _dirty = true


func set_focus_storey(idx: int) -> void:
    focus_storey_index = idx
    _dirty = true


func _on_task_state_changed(task_id: String, _old: int, _new: int) -> void:
    var t: TaskData = gs.bundle.tasks_by_id[task_id]
    _apply_color(t.element_guid)


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
        _material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
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
    _slots.clear()
    _kind_elements.clear()
    _storey_index.clear()
    _rework_guids.clear()
    if gs == null or gs.bundle == null:
        return
    for e in gs.bundle.elements:
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
        var mmi := MultiMeshInstance3D.new()
        mmi.name = "Kind_%s" % kind
        mmi.multimesh = mm
        mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
        add_child(mmi)
        _instances[kind] = mmi
    _dirty = true
    _apply_all()


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
            a = GHOST_ALPHA
        SimState.Visual.FRAMED:
            a = FRAMED_ALPHA
        SimState.Visual.SOLID:
            a = 1.0
        SimState.Visual.INSPECTED:
            a = 1.0
            col = col.lerp(Color(0.2, 0.9, 0.35), 0.3)
        SimState.Visual.REWORK:
            a = 1.0
            col = col.lerp(Color(0.95, 0.15, 0.1), 0.35 + 0.35 * pulse)
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
    mm.set_instance_color(int(s["slot"]), compute_colour(guid, 0.5 + 0.5 * sin(_pulse)))
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
    var s: Dictionary = _slots[guid]
    return (_instances[s["kind"]] as MultiMeshInstance3D).multimesh.get_instance_color(int(s["slot"]))
