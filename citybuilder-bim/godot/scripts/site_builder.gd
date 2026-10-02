class_name SiteBuilder
extends Node3D
## Kenney builder.gd adapted: place / demolish logistics SiteTiles on the GridMap with costs,
## occupied / blocked / footprint refusals, rotation and palette cycling.
## All rules live in SimState.place_tile; this node is the cursor, input and visual layer.

signal message(text: String)
signal palette_changed(tile_id: String)
signal equipment_armed_changed(equipment_id: String)

var gs: SimState = null
var gridmap: GridMap = null
var view_camera: Camera3D = null
var catalog: SiteTileCatalog = null

var selector: Node3D = null
var container: Node3D = null
var palette: Array[String] = SiteTiles.PLAYER_TILES
var index: int = 0
var orientation: int = 0  # quarter turns
## When false (assign mode) clicks are ignored by the builder.
var active: bool = true
var pending_equipment: String = ""

var _plane: Plane = Plane(Vector3.UP, 0.0)
var cell: Vector2i = Vector2i.ZERO
var _equip_root: Node3D = null
var _last_world_ok: bool = false


func setup(state: SimState, grid: GridMap, cam: Camera3D) -> void:
    gs = state
    gridmap = grid
    view_camera = cam
    catalog = SiteTileCatalog.new()
    gridmap.mesh_library = catalog.library
    selector = Node3D.new()
    selector.name = "Selector"
    add_child(selector)
    var sprite := Sprite3D.new()
    sprite.texture = load("res://sprites/selector.png")
    sprite.rotation_degrees = Vector3(-90, 0, 0)
    sprite.position = Vector3(0, 0.06, 0)
    selector.add_child(sprite)
    container = Node3D.new()
    container.name = "Container"
    selector.add_child(container)
    _equip_root = Node3D.new()
    _equip_root.name = "Equipment"
    add_child(_equip_root)
    gs.tiles_changed.connect(refresh_all)
    gs.level_started.connect(refresh_all)
    _update_preview()
    refresh_all()


func current_tile() -> String:
    return palette[index]


func select_tile(tile_id: String) -> void:
    var i: int = palette.find(tile_id)
    if i >= 0:
        index = i
        _update_preview()
        palette_changed.emit(tile_id)


func arm_equipment(equipment_id: String) -> void:
    pending_equipment = equipment_id
    equipment_armed_changed.emit(equipment_id)
    _update_preview()


# ------------------------------------------------------------------ input

func _process(delta: float) -> void:
    if gs == null or view_camera == null:
        return
    var mp: Vector2 = get_viewport().get_mouse_position()
    var hit: Variant = _plane.intersects_ray(view_camera.project_ray_origin(mp), view_camera.project_ray_normal(mp))
    var over_gui: bool = get_viewport().gui_get_hovered_control() != null
    _last_world_ok = hit != null
    if hit != null:
        var wp: Vector3 = hit
        cell = Vector2i(roundi(wp.x), roundi(wp.z))
        var target := Vector3(cell.x, 0, cell.y)
        selector.position = selector.position.lerp(target, minf(delta * 40.0, 1.0))
    selector.visible = active and hit != null and not over_gui
    selector.rotation.y = lerp_angle(selector.rotation.y, float(orientation) * PI * 0.5, minf(delta * 30.0, 1.0))


func _unhandled_input(event: InputEvent) -> void:
    if gs == null or not active or not _last_world_ok:
        return
    if event.is_action_pressed("build"):
        _build()
    elif event.is_action_pressed("demolish"):
        _demolish()
    elif event.is_action_pressed("rotate"):
        rotate_cursor()
    elif event.is_action_pressed("structure_next"):
        cycle(1)
    elif event.is_action_pressed("structure_previous"):
        cycle(-1)
    elif event.is_action_pressed("cancel") and pending_equipment != "":
        arm_equipment("")


func rotate_cursor() -> void:
    orientation = posmod(orientation + 1, 4)
    _sfx("sounds/rotate.ogg", -30)


func cycle(step: int) -> void:
    index = wrapi(index + step, 0, palette.size())
    _update_preview()
    _sfx("sounds/toggle.ogg", -30)
    palette_changed.emit(current_tile())


func _build() -> void:
    if pending_equipment != "":
        if gs.place_equipment(pending_equipment, cell):
            _sfx("sounds/placement-a.ogg", -20)
            arm_equipment("")
        else:
            message.emit(gs.last_error)
        return
    if gs.place_tile(cell, current_tile(), orientation):
        _sfx("sounds/placement-a.ogg, sounds/placement-b.ogg, sounds/placement-c.ogg, sounds/placement-d.ogg", -20)
    else:
        message.emit(gs.last_error)


func _demolish() -> void:
    if gs.remove_tile(cell):
        _sfx("sounds/removal-a.ogg, sounds/removal-b.ogg, sounds/removal-c.ogg, sounds/removal-d.ogg", -20)
    else:
        message.emit(gs.last_error)


func _sfx(path: String, volume_db: float) -> void:
    var a: Node = get_node_or_null("/root/Audio")
    if a != null:
        a.call("play", path, volume_db)


# ------------------------------------------------------------------ visuals

func _update_preview() -> void:
    if container == null:
        return
    for n in container.get_children():
        container.remove_child(n)
        n.queue_free()
    if pending_equipment != "":
        container.add_child(_make_crane_visual(1.0))
    else:
        container.add_child(catalog.make_preview(current_tile()))


func refresh_all() -> void:
    if gridmap == null or gs == null or gs.bundle == null:
        return
    gridmap.clear()
    for c in gs.tiles:
        var cell_v: Vector2i = c
        var td: Dictionary = gs.tiles[c]
        var r: Dictionary = catalog.resolve(gs.tiles, cell_v, str(td["tile"]), int(td["orientation"]))
        gridmap.set_cell_item(Vector3i(cell_v.x, 0, cell_v.y), int(r["item"]),
                gridmap.get_orthogonal_index_from_basis(r["basis"]))
    _refresh_equipment()


func _make_crane_visual(reach_cells: float) -> Node3D:
    var root := Node3D.new()
    var mast := MeshInstance3D.new()
    var cm := CylinderMesh.new()
    cm.top_radius = 0.06
    cm.bottom_radius = 0.08
    cm.height = 1.6
    var mat := StandardMaterial3D.new()
    mat.albedo_color = Color(0.95, 0.6, 0.1)
    cm.material = mat
    mast.mesh = cm
    mast.position = Vector3(0, 0.8, 0)
    root.add_child(mast)
    var jib := MeshInstance3D.new()
    var bm := BoxMesh.new()
    bm.size = Vector3(1.4, 0.06, 0.06)
    bm.material = mat
    jib.mesh = bm
    jib.position = Vector3(0.4, 1.6, 0)
    root.add_child(jib)
    if reach_cells > 1.0:
        var disk := MeshInstance3D.new()
        var dm := CylinderMesh.new()
        dm.top_radius = reach_cells
        dm.bottom_radius = reach_cells
        dm.height = 0.01
        var dmat := StandardMaterial3D.new()
        dmat.albedo_color = Color(1.0, 0.85, 0.2, 0.18)
        dmat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
        dm.material = dmat
        disk.mesh = dm
        disk.position = Vector3(0, 0.03, 0)
        root.add_child(disk)
    return root


func _refresh_equipment() -> void:
    for n in _equip_root.get_children():
        _equip_root.remove_child(n)
        n.queue_free()
    for e in gs.equipment_placed:
        var def: EquipmentDef = gs.equipment_def(str(e["id"]))
        var reach: float = float(def.reach_cells) if def != null else 0.0
        var v: Node3D = _make_crane_visual(reach)
        var c: Vector2i = e["cell"]
        v.position = Vector3(c.x, 0.05, c.y)
        _equip_root.add_child(v)
