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

## Zone pinned or framed by the UI (`set_highlight_zone`); drawn with a pulsing rim like the hovered zone.
var highlight_zone_id: String = ""

const RIM_COLOR: Color = Color(1.0, 0.93, 0.45)
const RIM_WIDTH: float = 0.14
const RIM_Y: float = 0.1

var _rim_pinned: MeshInstance3D = null
var _rim_hover: MeshInstance3D = null
var _rim_mat_pinned: StandardMaterial3D = null
var _rim_mat_hover: StandardMaterial3D = null
var _rim_ids: Array[String] = ["", ""]  # zone ids the two rim meshes were built for
var _pulse: float = 0.0
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
    gs.level_started.connect(func() -> void:
        _rim_ids = ["", ""]
        _build())


func set_focus_storey(idx: int) -> void:
    focus_storey_index = idx
    _dirty = true


## Pins a pulsing light rim around a zone ("" clears it): the zone selected in the inspector or framed by the camera.
func set_highlight_zone(zone_id: String) -> void:
    highlight_zone_id = zone_id
    _update_rims()


## True while the pinned rim is shown (tests).
func rim_visible() -> bool:
    return _rim_pinned != null and _rim_pinned.visible


func hover_rim_visible() -> bool:
    return _rim_hover != null and _rim_hover.visible


## Boundary of a zone as thin ribbons (quads of RIM_WIDTH) on the edges of its cells that touch no other cell of the zone.
static func rim_mesh(cells: Array[Vector2i], y: float) -> ArrayMesh:
    var set: Dictionary = {}
    for c in cells:
        set[c] = true
    var verts := PackedVector3Array()
    var idx := PackedInt32Array()
    var h: float = 0.5
    var w: float = RIM_WIDTH * 0.5
    for c in cells:
        var x: float = float(c.x)
        var z: float = float(c.y)
        # edge (neighbour offset, ribbon rectangle min/max in x and z)
        var edges: Array = [
            [Vector2i(0, -1), x - h - w, z - h - w, x + h + w, z - h + w],
            [Vector2i(0, 1), x - h - w, z + h - w, x + h + w, z + h + w],
            [Vector2i(-1, 0), x - h - w, z - h - w, x - h + w, z + h + w],
            [Vector2i(1, 0), x + h - w, z - h - w, x + h + w, z + h + w],
        ]
        for e in edges:
            if set.has(c + (e[0] as Vector2i)):
                continue
            var base: int = verts.size()
            verts.append(Vector3(e[1], y, e[2]))
            verts.append(Vector3(e[3], y, e[2]))
            verts.append(Vector3(e[3], y, e[4]))
            verts.append(Vector3(e[1], y, e[4]))
            idx.append_array(PackedInt32Array([base, base + 1, base + 2, base, base + 2, base + 3]))
    var mesh := ArrayMesh.new()
    if verts.is_empty():
        return mesh
    var arrays: Array = []
    arrays.resize(Mesh.ARRAY_MAX)
    arrays[Mesh.ARRAY_VERTEX] = verts
    arrays[Mesh.ARRAY_INDEX] = idx
    mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
    return mesh


func _make_rim_node(node_name: String) -> Array:
    var mat := StandardMaterial3D.new()
    mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
    mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
    mat.cull_mode = BaseMaterial3D.CULL_DISABLED
    mat.no_depth_test = true  # the rim stays visible behind tall elements
    mat.render_priority = 3
    mat.albedo_color = RIM_COLOR
    var mi := MeshInstance3D.new()
    mi.name = node_name
    mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
    mi.material_override = mat
    mi.visible = false
    add_child(mi)
    return [mi, mat]


func _rim_for(zone_id: String, node: MeshInstance3D, slot: int) -> void:
    if zone_id == "" or gs == null or gs.bundle == null or not gs.bundle.zones_by_id.has(zone_id):
        node.visible = false
        _rim_ids[slot] = ""
        return
    if _rim_ids[slot] != zone_id:
        var z: ZoneData = gs.bundle.zones_by_id[zone_id]
        node.mesh = rim_mesh(z.cells, gs.bundle.storey_y(z.storey_id) + RIM_Y)
        _rim_ids[slot] = zone_id
    node.visible = true


func _update_rims() -> void:
    if _rim_pinned == null:
        var a: Array = _make_rim_node("RimPinned")
        _rim_pinned = a[0]
        _rim_mat_pinned = a[1]
        var b: Array = _make_rim_node("RimHover")
        _rim_hover = b[0]
        _rim_mat_hover = b[1]
    _rim_for(highlight_zone_id, _rim_pinned, 0)
    var hv: String = hovered_zone_id if hovered_zone_id != highlight_zone_id else ""
    _rim_for(hv, _rim_hover, 1)
    _pulse_rims()


func _pulse_rims() -> void:
    if _rim_mat_pinned == null:
        return
    var k: float = 0.5 + 0.5 * sin(_pulse)
    var c: Color = RIM_COLOR
    c.a = 0.65 + 0.35 * k
    _rim_mat_pinned.albedo_color = c
    var h: Color = RIM_COLOR
    h.a = 0.55
    _rim_mat_hover.albedo_color = h


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


func _process(delta: float) -> void:
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
        _update_rims()
    if _rim_pinned != null and _rim_pinned.visible:
        _pulse += delta * 4.0
        _pulse_rims()
    if _dirty:
        refresh()


func _unhandled_input(event: InputEvent) -> void:
    if pick_enabled and event.is_action_pressed("build") and hovered_zone_id != "":
        zone_clicked.emit(hovered_zone_id)
