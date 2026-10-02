class_name CoolingTowerKit
extends KitBuilder
## Mechanical-draft cooling tower: basin, one cell per footprint cell (louvred casing), fan decks with
## shroud rings, fan blades.
## Layers: basin (grow height), cells (count), fan_decks (count), fans (count).


func _build() -> void:
    var nx: int = footprint.x
    var nz: int = footprint.y
    var n: int = nx * nz
    var cs: float = cell_size_m
    var pitch: float = cs
    var cell: float = cs * 0.86
    var basin_h: float = height_m * 0.1
    var casing_top: float = height_m * 0.64
    var deck_t: float = height_m * 0.05
    var shroud_h: float = height_m - casing_top - deck_t
    var sr: float = cell * 0.36
    if begin_layer("basin"):
        box_grow(Vector3(-width_m * 0.5 + 0.05, 0, -depth_m * 0.5 + 0.05), Vector3(width_m * 0.5 - 0.05, basin_h, depth_m * 0.5 - 0.05), layer_fill(), 1, CONCRETE)
    if begin_layer("cells"):
        for i in n:
            set_part(i, n)
            var c: Vector3 = _cell_centre(i, nx, pitch)
            box_mm(Vector3(c.x - cell * 0.5, basin_h, c.z - cell * 0.5), Vector3(c.x + cell * 0.5, casing_top, c.z + cell * 0.5), PIPE_BLUE)
            # louvre bands on the +z face
            for k in 3:
                var y: float = basin_h + (casing_top - basin_h) * (0.2 + 0.25 * float(k))
                box_mm(Vector3(c.x - cell * 0.45, y, c.z + cell * 0.5), Vector3(c.x + cell * 0.45, y + 0.12, c.z + cell * 0.5 + 0.05), STEEL_DARK)
    if begin_layer("fan_decks"):
        for i in n:
            set_part(i, n)
            var c2: Vector3 = _cell_centre(i, nx, pitch)
            box_mm(Vector3(c2.x - cell * 0.5, casing_top, c2.z - cell * 0.5), Vector3(c2.x + cell * 0.5, casing_top + deck_t, c2.z + cell * 0.5), STEEL_LIGHT)
            tube(Vector3(c2.x, casing_top + deck_t, c2.z), Vector3(c2.x, casing_top + deck_t + shroud_h, c2.z), sr, sr * 0.88, STEEL, 10)
            # fan ring lip
            tube(Vector3(c2.x, casing_top + deck_t + shroud_h * 0.86, c2.z), Vector3(c2.x, casing_top + deck_t + shroud_h, c2.z), sr * 1.1, sr * 0.8, STEEL_DARK, 10)
    if begin_layer("fans"):
        for i in n:
            set_part(i, n)
            var c3: Vector3 = _cell_centre(i, nx, pitch)
            var fy: float = casing_top + deck_t + shroud_h * 0.55
            for bl in 3:
                push_xf(at(Vector3(c3.x, fy, c3.z), PI * float(bl) / 3.0))
                box(Vector3.ZERO, Vector3(sr * 1.7, 0.05, sr * 0.28), STEEL_DARK)
                pop_xf()
            cone(Vector3(c3.x, fy, c3.z), 0.3, 0.28, STEEL_LIGHT, 8)
            box(Vector3(c3.x, fy - 0.1, c3.z), Vector3(0.25, 0.25, 0.25), PIPE_ORANGE)


func _cell_centre(i: int, nx: int, pitch: float) -> Vector3:
    var ix: int = i % nx
    var iz: int = i / nx
    return Vector3((float(ix) + 0.5 - float(nx) * 0.5) * pitch, 0, (float(iz) + 0.5 - float(footprint.y) * 0.5) * pitch)
