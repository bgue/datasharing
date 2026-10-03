class_name BundWallKit
extends KitBuilder
## Bund: floor slab with a sump, the containment wall around the footprint (grows in height) and a pair of access steps.
## Layers: floor, wall (grow height), access.


func _build() -> void:
    var hw: float = width_m * 0.5 - 0.25
    var hd: float = depth_m * 0.5 - 0.25
    var t: float = 0.35
    var floor_t: float = 0.12
    var wall_h: float = height_m
    if begin_layer("floor"):
        box_mm(Vector3(-hw, 0, -hd), Vector3(hw, floor_t, hd), CONCRETE_DARK)
        box_mm(Vector3(hw - 1.0, floor_t, -hd + 0.2), Vector3(hw - 0.2, floor_t + 0.05, -hd + 1.0), DARK)  # sump grating
    if begin_layer("wall"):
        var f: float = layer_fill()
        box_grow(Vector3(-hw, 0, -hd), Vector3(hw, wall_h, -hd + t), f, 1, CONCRETE)
        box_grow(Vector3(-hw, 0, hd - t), Vector3(hw, wall_h, hd), f, 1, CONCRETE)
        box_grow(Vector3(-hw, 0, -hd + t), Vector3(-hw + t, wall_h, hd - t), f, 1, CONCRETE)
        box_grow(Vector3(hw - t, 0, -hd + t), Vector3(hw, wall_h, hd - t), f, 1, CONCRETE)
    if begin_layer("access"):
        # steps over the wall on the +z side, with a drain pipe and valve through it
        set_part(0, 2)
        var sx: float = -hw * 0.5
        stair_straight(Vector3(sx, 0, hd - t - 0.9), PI * 1.5, 1.0, wall_h * 0.9, 0.9, 4, STEEL_LIGHT)
        set_part(1, 2)
        cyl(Vector3(hw * 0.4, wall_h * 0.25, hd - t - 0.4), Vector3(hw * 0.4, wall_h * 0.25, hd + 0.2), 0.12, 0.12, PIPE_ORANGE, 8, 3)
        box(Vector3(hw * 0.4, wall_h * 0.25 + 0.25, hd - t - 0.4), Vector3(0.25, 0.25, 0.25), PIPE_RED)
