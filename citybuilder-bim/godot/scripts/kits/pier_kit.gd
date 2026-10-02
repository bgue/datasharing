class_name PierKit
extends KitBuilder
## Bridge pier line: pile cap, one stem per cell along the long axis (each rises with height), pier head
## cross-beam, bearings.
## Layers: pile_cap, stems (count, grow height), pier_head, bearings.


func _build() -> void:
    orient_long()
    var n: int = maxi(footprint.x, footprint.y)
    var cap_h: float = height_m * 0.1
    var head_h: float = height_m * 0.12
    var cap_w: float = lz * 0.5
    var stem_r: float = minf(lz * 0.13, 0.9)
    var stem_top: float = height_m - head_h - 0.15
    if begin_layer("pile_cap"):
        box_mm(Vector3(-lx * 0.46, 0, -cap_w * 0.5), Vector3(lx * 0.46, cap_h, cap_w * 0.5), CONCRETE_DARK)
    if begin_layer("stems"):
        for i in n:
            var x: float = _stem_x(i, n)
            cyl_grow(Vector3(x, cap_h, 0), Vector3(x, stem_top, 0), stem_r, stem_r * 0.85, CONCRETE, part_fill(i, n), 8, 2)
    if begin_layer("pier_head"):
        box_grow(Vector3(-lx * 0.46, stem_top, -cap_w * 0.4), Vector3(lx * 0.46, height_m - 0.15, cap_w * 0.4), layer_fill(), 0, CONCRETE)
    if begin_layer("bearings"):
        for i in n:
            set_part(i, n)
            var x: float = _stem_x(i, n)
            for dz: float in [-0.5, 0.5]:
                box(Vector3(x, height_m - 0.075, dz * cap_w * 0.55), Vector3(0.5, 0.15, 0.5), PIPE_RED)
    end_orient()


func _stem_x(i: int, n: int) -> float:
    return (float(i) + 0.5 - float(n) * 0.5) * (lx * 0.92 / float(n)) * 1.0
