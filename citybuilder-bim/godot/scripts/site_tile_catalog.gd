class_name SiteTileCatalog
extends RefCounted
## Builds the SiteTile resources and the GridMap MeshLibrary dynamically (as the Kenney kit does).

const MODEL_DIR: String = "res://models/"

## tile id -> SiteTile
var tiles: Dictionary = {}
## "tile:variant" -> MeshLibrary item id
var item_ids: Dictionary = {}
var library: MeshLibrary = MeshLibrary.new()
## Mesh of every item, kept for building cursor previews: item key -> {mesh, offset}
var _meshes: Dictionary = {}


func _init() -> void:
    _define_tiles()
    _build_library()


func _glb(name: String) -> PackedScene:
    return load(MODEL_DIR + name + ".glb")


func _tile(id: String, model_name: String) -> SiteTile:
    var t := SiteTile.new()
    t.tile_id = id
    t.display_name = str(SiteTiles.DISPLAY_NAMES.get(id, id))
    if model_name != "":
        t.model = _glb(model_name)
    tiles[id] = t
    return t


func _box(id: String, size: Vector3, color: Color, alpha: float = 1.0) -> void:
    var t := _tile(id, "")
    t.box_size = size
    t.box_color = color
    t.box_alpha = alpha


func _define_tiles() -> void:
    var road := _tile("haul_road", "road-straight")
    road.variant_models = {
        "corner": _glb("road-corner"), "intersection": _glb("road-intersection"), "split": _glb("road-split"),
    }
    var eroad := _tile("existing_road", "road-straight")
    eroad.variant_models = road.variant_models
    _tile("gate", "road-straight-lightposts")
    _tile("laydown", "pavement")
    _tile("crane_pad", "pavement-fountain")
    _tile("welfare", "building-garage")
    _box("hoarding", Vector3(0.95, 0.7, 0.12), Color(0.16, 0.16, 0.18))
    _box("icra_barrier", Vector3(0.95, 0.8, 0.08), Color(1, 1, 1), 0.55)
    _box("traffic_cones", Vector3(0.25, 0.35, 0.25), Color(1.0, 0.45, 0.05))
    var eb := _tile("existing_building", "building-small-a")
    eb.variant_models = {"1": _glb("building-small-b"), "2": _glb("building-small-c"), "3": _glb("building-small-d")}
    _tile("trees", "grass-trees")
    _tile("grass", "grass")


func _mesh_of(ps: PackedScene) -> Mesh:
    var inst: Node = ps.instantiate()
    var found: Mesh = null
    var stack: Array[Node] = [inst]
    while not stack.is_empty() and found == null:
        var n: Node = stack.pop_back()
        if n is MeshInstance3D and (n as MeshInstance3D).mesh != null:
            found = (n as MeshInstance3D).mesh.duplicate()
        for c in n.get_children():
            stack.append(c)
    inst.free()
    return found


func _add_item(key: String, mesh: Mesh, offset: Vector3 = Vector3.ZERO) -> void:
    var id: int = library.get_last_unused_item_id()
    library.create_item(id)
    library.set_item_mesh(id, mesh)
    library.set_item_mesh_transform(id, Transform3D(Basis(), offset))
    item_ids[key] = id
    _meshes[key] = {"mesh": mesh, "offset": offset}


func _build_library() -> void:
    for tile_id in tiles:
        var t: SiteTile = tiles[tile_id]
        if tile_id == "existing_road":
            continue  # shares the haul_road items
        if t.model != null:
            _add_item("%s:default" % tile_id, _mesh_of(t.model))
            for v in t.variant_models:
                _add_item("%s:%s" % [tile_id, v], _mesh_of(t.variant_models[v]))
        else:
            var bm := BoxMesh.new()
            bm.size = t.box_size
            var mat := StandardMaterial3D.new()
            mat.albedo_color = Color(t.box_color, t.box_alpha)
            if t.box_alpha < 1.0:
                mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
            bm.material = mat
            _add_item("%s:default" % tile_id, bm, Vector3(0, t.box_size.y * 0.5, 0))


## Cursor preview node for a tile (caller frees it).
func make_preview(tile_id: String) -> Node3D:
    var key: String = "%s:default" % tile_id
    var root := Node3D.new()
    if _meshes.has(key):
        var mi := MeshInstance3D.new()
        mi.mesh = _meshes[key]["mesh"]
        mi.position = _meshes[key]["offset"] + Vector3(0, 0.25, 0)
        root.add_child(mi)
    return root


## Picks the MeshLibrary item and GridMap orientation index for a tile at a cell.
## `tiles` is SimState.tiles. Returns {"item": int, "basis": Basis}.
func resolve(tiles_layer: Dictionary, cell: Vector2i, tile_id: String, orientation: int) -> Dictionary:
    var variant: String = "default"
    var k: int = orientation
    if tile_id == "haul_road" or tile_id == "existing_road" or tile_id == "gate":
        var neigh: Array[Vector2i] = []
        for d in Logistics.DIRS4:
            if SiteTiles.is_road(Logistics.tile_at(tiles_layer, cell + d)):
                neigh.append(d)
        var pick: Dictionary = RoadAutotile.choose(neigh)
        if neigh.is_empty():
            pick["k"] = orientation  # isolated piece: honour the player's rotation
        if tile_id == "gate":
            variant = "default"
            k = int(pick["k"]) if str(pick["variant"]) == "straight" else orientation
        else:
            variant = "default" if pick["variant"] == "straight" else str(pick["variant"])
            k = int(pick["k"])
    elif tile_id == "existing_building":
        var h: int = posmod(cell.x * 7 + cell.y * 13, 4)
        variant = "default" if h == 0 else str(h)
    var key_tile: String = "haul_road" if tile_id == "existing_road" else tile_id
    var key: String = "%s:%s" % [key_tile, variant]
    if not item_ids.has(key):
        key = "%s:default" % key_tile
    return {"item": int(item_ids[key]), "basis": Basis(Vector3.UP, float(k) * PI * 0.5)}
