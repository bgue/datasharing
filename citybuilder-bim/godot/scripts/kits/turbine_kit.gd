class_name TurbineKit
extends KitBuilder
## Turbine on a pedestal: pedestal, skid, casing (horizontal cylinder + inlet / diffuser cones), generator
## box with cooler, auxiliaries (oil tank, lube cooler, fuel skid, pipes), exhaust stack, acoustic enclosure.
## Layers: pedestal (grow height), skid, casing (count), generator, auxiliaries (count), exhaust_stack
## (grow height), enclosure (count).

var _rc: float = 1.0
var _ya: float = 2.0
var _hp: float = 0.8
var _ts: float = 0.25
var _wd: float = 4.0


func _build() -> void:
    orient_long()
    _wd = lz * 0.8
    _hp = height_m * 0.14
    _rc = minf(lz * 0.2, height_m * 0.15)
    _ya = _hp + _ts + _rc
    var l: float = lx - 0.8
    if begin_layer("pedestal"):
        box_grow(Vector3(-l * 0.5, 0, -_wd * 0.45), Vector3(l * 0.5, _hp, _wd * 0.45), layer_fill(), 1, CONCRETE)
    if begin_layer("skid"):
        box_mm(Vector3(-l * 0.46, _hp, -_wd * 0.4), Vector3(l * 0.46, _hp + _ts, _wd * 0.4), STEEL_DARK)
        for side: float in [-1.0, 1.0]:
            ibeam(Vector3(-l * 0.46, _hp + _ts * 0.5, side * _wd * 0.3), Vector3(l * 0.46, _hp + _ts * 0.5, side * _wd * 0.3), 0.2, 0.15, STEEL, true)
    if begin_layer("casing"):
        set_part(0, 3)
        cyl(Vector3(-l * 0.30, _ya, 0), Vector3(l * 0.05, _ya, 0), _rc, _rc, STEEL_LIGHT, 12, 3)
        set_part(1, 3)
        cyl(Vector3(-l * 0.43, _ya, 0), Vector3(-l * 0.30, _ya, 0), _rc * 0.45, _rc, PIPE_BLUE, 12, 1)
        set_part(2, 3)
        cyl(Vector3(l * 0.05, _ya, 0), Vector3(l * 0.16, _ya, 0), _rc, _rc * 0.6, STEEL_LIGHT, 12, 2)
        box(Vector3(l * 0.2, _ya, 0), Vector3(l * 0.08, _rc * 1.2, _rc * 1.2), STEEL_DARK)
    if begin_layer("generator"):
        set_part(0, 2)
        box(Vector3(l * 0.34, _ya, 0), Vector3(l * 0.2, _rc * 1.7, _rc * 1.7), PIPE_BLUE)
        set_part(1, 2)
        box(Vector3(l * 0.34, _ya + _rc * 1.0, 0), Vector3(l * 0.14, _rc * 0.35, _rc * 1.2), STEEL)
    if begin_layer("auxiliaries"):
        set_part(0, 4)
        box(Vector3(-l * 0.3, _hp + _ts + 0.4, _wd * 0.33), Vector3(l * 0.12, 0.8, 0.8), PIPE_GREEN)
        set_part(1, 4)
        cyl(Vector3(-l * 0.12, _hp + _ts + 0.5, _wd * 0.33), Vector3(l * 0.0, _hp + _ts + 0.5, _wd * 0.33), 0.35, 0.35, STEEL_LIGHT, 8, 3)
        set_part(2, 4)
        box(Vector3(l * 0.18, _hp + _ts + 0.4, -_wd * 0.33), Vector3(l * 0.14, 0.8, 0.7), ELEC_YELLOW)
        set_part(3, 4)
        pipe([Vector3(-l * 0.3, _hp + _ts + 0.9, _wd * 0.33), Vector3(-l * 0.3, _ya, _wd * 0.33), Vector3(-l * 0.2, _ya, _rc)], 0.07, PIPE_ORANGE)
        pipe([Vector3(l * 0.18, _hp + _ts + 0.8, -_wd * 0.33), Vector3(l * 0.12, _ya - _rc, -_rc)], 0.06, PIPE_ORANGE)
    if begin_layer("exhaust_stack"):
        var y0: float = _ya + _rc * 0.4
        var sr: float = _rc * 0.5
        # elbow duct from the diffuser then the vertical stack
        cyl_grow(Vector3(l * 0.1, y0, 0), Vector3(l * 0.1, height_m, 0), sr, sr * 0.85, STEEL, layer_fill(), 10, 3)
    if begin_layer("enclosure"):
        var top: float = minf(height_m * 0.85, _ya + _rc * 1.5)
        var ex: float = l * 0.47
        var ez: float = _wd * 0.45
        set_part(0, 3)
        box_mm(Vector3(-ex, top, -ez), Vector3(ex, top + 0.15, ez), STEEL_LIGHT)
        set_part(1, 3)
        box_mm(Vector3(-ex, _hp, -ez), Vector3(-ex + 0.12, top, ez), TANK_GREY)
        box_mm(Vector3(ex - 0.12, _hp, -ez), Vector3(ex, top, ez), TANK_GREY)
        set_part(2, 3)
        box_mm(Vector3(-ex, _hp, -ez), Vector3(ex, _hp + (top - _hp) * 0.45, -ez + 0.1), TANK_GREY)
        box_mm(Vector3(-ex, _hp, ez - 0.1), Vector3(ex, _hp + (top - _hp) * 0.45, ez), TANK_GREY)
    end_orient()
