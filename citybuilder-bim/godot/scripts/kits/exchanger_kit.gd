class_name ExchangerKit
extends KitBuilder
## Shell-and-tube exchanger: saddles, shell (grows along its length), channel head + rear head, nozzles.
## Layers: saddles, shell (grow length), channel_heads, nozzles.


func _build() -> void:
    orient_long()
    var hs: float = maxf(0.4, height_m * 0.2)
    var r: float = minf(lz * 0.25, (height_m - hs - 0.5) * 0.5)
    r = maxf(r, 0.2)
    var yc: float = hs + r
    var ls: float = lx * 0.65
    var hdl: float = minf(lx * 0.12, 1.0)
    if begin_layer("saddles"):
        for sx: float in [-0.3, 0.3]:
            box_mm(Vector3(sx * ls - 0.2, 0, -r * 0.8), Vector3(sx * ls + 0.2, yc - r * 0.5, r * 0.8), CONCRETE)
            box_mm(Vector3(sx * ls - 0.2, yc - r * 0.5, -r * 0.5), Vector3(sx * ls + 0.2, yc - r * 0.3, r * 0.5), STEEL_DARK)
    if begin_layer("shell"):
        cyl_grow(Vector3(-ls * 0.5, yc, 0), Vector3(ls * 0.5, yc, 0), r, r, STEEL_LIGHT, layer_fill(), 12, 0)
        # flange rings at both ends
        var f: float = layer_fill()
        set_solid(f > 0.0)
        cyl(Vector3(-ls * 0.5, yc, 0), Vector3(-ls * 0.5 + 0.1, yc, 0), r * 1.08, r * 1.08, STEEL_DARK, 12, 2)
        set_solid(f >= 1.0 - EPS)
        cyl(Vector3(ls * 0.5 - 0.1, yc, 0), Vector3(ls * 0.5, yc, 0), r * 1.08, r * 1.08, STEEL_DARK, 12, 2)
    if begin_layer("channel_heads"):
        set_part(0, 2)
        cyl(Vector3(-ls * 0.5 - hdl, yc, 0), Vector3(-ls * 0.5, yc, 0), r * 0.95, r * 0.95, PIPE_BLUE, 12, 1)
        set_part(1, 2)
        dome(Vector3(ls * 0.5, yc, 0), Vector3.RIGHT, r, minf(hdl, r * 0.6), PIPE_BLUE, 12, 3)
    if begin_layer("nozzles"):
        var top: float = minf(height_m, yc + r + 0.45)
        set_part(0, 4)
        cyl(Vector3(-ls * 0.3, yc + r - 0.05, 0), Vector3(-ls * 0.3, top, 0), 0.15, 0.15, PIPE_ORANGE, 8, 3)
        set_part(1, 4)
        cyl(Vector3(ls * 0.3, yc + r - 0.05, 0), Vector3(ls * 0.3, top, 0), 0.15, 0.15, PIPE_ORANGE, 8, 3)
        set_part(2, 4)
        cyl(Vector3(-ls * 0.5 - hdl * 0.6, yc + r * 0.9, 0), Vector3(-ls * 0.5 - hdl * 0.6, top, 0), 0.13, 0.13, PIPE_BLUE, 8, 3)
        set_part(3, 4)
        cyl(Vector3(-ls * 0.5 - hdl * 0.6, yc - r * 0.9, 0), Vector3(-ls * 0.5 - hdl * 0.6, maxf(0.05, yc - r - 0.3), 0), 0.13, 0.13, PIPE_BLUE, 8, 3)
    end_orient()
