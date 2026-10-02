class_name ZoneOverlay
extends Node3D
## Translucent quads per zone cell, coloured by zone status; hover and click picking.
## green = ready work but no crew, blue = active, red = congested, grey = done,
## yellow = blocked (gate / procurement / access / crane / pause), none = idle.

signal zone_hovered(zone_id: String)
signal zone_clicked(zone_id: String)

const COLORS: Dictionary = {
    "ready": Color(0.2, 0.85, 0.3, 0.32),
    "active": Color(0.25, 0.5, 1.0, 0.34),
    "congested": Color(0.95, 0.2, 0.2, 0.40),
    "done": Color(0.6, 0.6, 0.62, 0.22),
    "blocked": Color(0.98, 0.85, 0.15, 0.34),
    "idle": Color(1, 1, 1, 0.08),
}

var gs: SimState = null
var view_camera: Camera3D = null
var focus_storey_index: int = 0
var hovered_zone_id: String = ""
## When true, left click emits zone_clicked.
var pick_enabled: bool = false

var _nodes: Dictionary = {}  # zone_id -> MultiMeshInstance3D
var _materials: Dictionary = {}  # zone_id -> StandardMaterial3D
var _dirty: bool = true


func setup(state: SimState, cam: Camera3D) -> void:
    gs = state
    view_camera = cam
    _build()
    gs.task_state_changed.connect(func(_a: String, _b: int, _c: int) -> void: _dirty = true)
    gs.crews_changed.connect(func() -> void: _dirty = true)
    gs.tiles_changed.connect(func() -> void: _dirty = true)
    gs.week_advanced.connect(func(_w: int) -> void: _dirty = true)
    gs.level_started.connect(func() -> void: _build())


func set_focus_storey(idx: int) -> void:
    focus_storey_index = idx
    _dirty = true


func _build() -> void:
    for k in _nodes:
        (_nodes[k] as Node).queue_free()
    _nodes.clear()
    _materials.clear()
    if gs == null or gs.bundle == null:
        return
    for z in gs.bundle.zones:
        var quad := QuadMesh.new()
        quad.size = Vector2(0.94, 0.94)
        quad.orientation = PlaneMesh.FACE_Y
        var mat := StandardMaterial3D.new()
        mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
        mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
        mat.cull_mode = BaseMaterial3D.CULL_DISABLED
        mat.no_depth_test = false
        quad.material = mat
        var mm := MultiMesh.new()
        mm.transform_format = MultiMesh.TRANSFORM_3D
        mm.mesh = quad
        mm.instance_count = z.cells.size()
        var y: float = gs.bundle.storey_y(z.storey_id) + 0.07
        for i in z.cells.size():
            mm.set_instance_transform(i, Transform3D(Basis(), Vector3(z.cells[i].x, y, z.cells[i].y)))
        var mmi := MultiMeshInstance3D.new()
        mmi.name = "Zone_%s" % z.id
        mmi.multimesh = mm
        mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
        add_child(mmi)
        _nodes[z.id] = mmi
        _materials[z.id] = mat
    _dirty = true


func zone_color_key(zone_id: String) -> String:
    return str(gs.zone_status(zone_id)["color"])


func refresh() -> void:
    for z in gs.bundle.zones:
        var node: MultiMeshInstance3D = _nodes[z.id]
        var storey_idx: int = int(gs.bundle.storey_index_by_id.get(z.storey_id, 0))
        node.visible = storey_idx == focus_storey_index
        var c: Color = COLORS[zone_color_key(z.id)]
        if z.id == hovered_zone_id:
            c.a = minf(1.0, c.a + 0.25)
        (_materials[z.id] as StandardMaterial3D).albedo_color = c
    _dirty = false


func zone_at_cell(cell: Vector2i) -> String:
    var focus_id: String = ""
    for s in gs.bundle.storeys:
        if s.index == focus_storey_index:
            focus_id = s.id
    if focus_id == "":
        return ""
    var zs: Array[ZoneData] = gs.bundle.zones_covering(focus_id, cell)
    return zs[0].id if not zs.is_empty() else ""


func _process(_delta: float) -> void:
    if gs == null or view_camera == null:
        return
    var plane := Plane(Vector3.UP, gs.bundle.storey_y_for_index(focus_storey_index))
    var mp: Vector2 = get_viewport().get_mouse_position()
    var hit: Variant = plane.intersects_ray(view_camera.project_ray_origin(mp), view_camera.project_ray_normal(mp))
    var zid: String = ""
    if hit != null and get_viewport().gui_get_hovered_control() == null:
        var wp: Vector3 = hit
        zid = zone_at_cell(Vector2i(roundi(wp.x), roundi(wp.z)))
    if zid != hovered_zone_id:
        hovered_zone_id = zid
        _dirty = true
        zone_hovered.emit(zid)
    if _dirty:
        refresh()


func _unhandled_input(event: InputEvent) -> void:
    if pick_enabled and event.is_action_pressed("build") and hovered_zone_id != "":
        zone_clicked.emit(hovered_zone_id)
