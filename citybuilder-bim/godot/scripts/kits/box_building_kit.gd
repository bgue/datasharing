class_name BoxBuildingKit
extends KitBuilder
## Box building (switchroom / substation): slab, walls (grow height), roof slab with parapet, doors,
## louvres, cable trench in front.
## Layers: slab, walls (grow height), roof, doors, louvres, trench (grow length).


func _build() -> void:
    orient_long()
    var bw: float = lx * 0.8
    var bd: float = lz * 0.6
    var slab_h: float = 0.2
    var roof_t: float = minf(0.3, height_m * 0.1)
    var wh: float = height_m - slab_h - roof_t
    var wy0: float = slab_h
    if begin_layer("slab"):
        box_mm(Vector3(-bw * 0.5 - 0.3, 0, -bd * 0.5 - 0.3), Vector3(bw * 0.5 + 0.3, slab_h, bd * 0.5 + 0.3), CONCRETE_DARK)
    if begin_layer("walls"):
        box_grow(Vector3(-bw * 0.5, wy0, -bd * 0.5), Vector3(bw * 0.5, wy0 + wh, bd * 0.5), layer_fill(), 1, BEIGE)
    if begin_layer("roof"):
        var ry: float = wy0 + wh
        box_mm(Vector3(-bw * 0.5 - 0.15, ry, -bd * 0.5 - 0.15), Vector3(bw * 0.5 + 0.15, ry + roof_t, bd * 0.5 + 0.15), STEEL_LIGHT)
    if begin_layer("doors"):
        for i in 2:
            set_part(i, 2)
            var x: float = lerpf(-bw * 0.3, bw * 0.3, float(i))
            box_mm(Vector3(x - 0.6, wy0, bd * 0.5), Vector3(x + 0.6, wy0 + minf(2.2, wh * 0.8), bd * 0.5 + 0.06), PIPE_BLUE)
    if begin_layer("louvres"):
        for i in 4:
            set_part(i, 4)
            var y: float = wy0 + wh * 0.5 + 0.2 * float(i)
            box_mm(Vector3(bw * 0.5, y, -bd * 0.25), Vector3(bw * 0.5 + 0.05, y + 0.1, bd * 0.25), STEEL_DARK)
    if begin_layer("trench"):
        var tz: float = bd * 0.5 + 0.45
        box_grow(Vector3(-bw * 0.4, 0, tz - 0.3), Vector3(bw * 0.4, 0.06, tz + 0.3), layer_fill(), 0, ELEC_YELLOW)
        box_grow(Vector3(-bw * 0.4, 0.06, tz - 0.3), Vector3(bw * 0.4, 0.1, tz - 0.25), layer_fill(), 0, DARK)
    end_orient()
