class_name ChillerKit
extends KitBuilder
## Water-cooled chiller: skid, condenser (lower) and evaporator (upper) shells, compressor with motor,
## piping, control panel.
## Layers: skid, evaporator, condenser, compressor, piping (mechanical group), controls.


func _build() -> void:
    orient_long()
    var ln: float = eq_len(2.2)
    var wd: float = eq_wid(1.1)
    var sk: float = height_m * 0.12
    var r: float = minf(wd * 0.22, (height_m * 0.85 - sk) * 0.25)
    var yc0: float = sk + r
    var yc1: float = sk + 3.0 * r + 0.05
    var l: float = ln * 0.62
    if begin_layer("skid"):
        box_mm(Vector3(-ln * 0.5, 0, -wd * 0.5), Vector3(ln * 0.5, sk, wd * 0.5), STEEL_DARK)
        for side: float in [-1.0, 1.0]:
            ibeam(Vector3(-ln * 0.5, sk * 0.5, side * wd * 0.5), Vector3(ln * 0.5, sk * 0.5, side * wd * 0.5), sk, 0.15, STEEL, true)
    if begin_layer("condenser"):
        cyl_grow(Vector3(-ln * 0.45, yc0, 0), Vector3(-ln * 0.45 + l, yc0, 0), r, r, PIPE_BLUE, layer_fill(), 10, 3)
    if begin_layer("evaporator"):
        cyl_grow(Vector3(-ln * 0.45, yc1, 0), Vector3(-ln * 0.45 + l, yc1, 0), r, r, STEEL_LIGHT, layer_fill(), 10, 3)
    if begin_layer("compressor"):
        set_part(0, 2)
        box_mm(Vector3(ln * 0.2, sk, -wd * 0.25), Vector3(ln * 0.42, sk + 2.6 * r, wd * 0.25), PIPE_BLUE)
        set_part(1, 2)
        cyl(Vector3(ln * 0.31, sk + 2.6 * r, 0), Vector3(ln * 0.31, sk + 3.4 * r, 0), r * 0.7, r * 0.7, ELEC_YELLOW, 10, 3)
    if begin_layer("piping"):
        set_part(0, 2)
        pipe([Vector3(-ln * 0.45 + l, yc1, 0), Vector3(ln * 0.2, yc1, 0)], r * 0.18, PIPE_ORANGE, -1.0, 6)
        set_part(1, 2)
        pipe([Vector3(-ln * 0.45 + l, yc0, 0), Vector3(ln * 0.2, yc0, 0)], r * 0.18, PIPE_GREEN, -1.0, 6)
    if begin_layer("controls"):
        box_mm(Vector3(ln * 0.45, sk, -wd * 0.3), Vector3(ln * 0.5, sk + 1.6 * r, wd * 0.0), ELEC_YELLOW)
    end_orient()
