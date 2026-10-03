class_name BuildingShellKit
extends KitBuilder
## Steel-framed building: footing pads, portal frames (appear bay by bay), purlins (grow along the length), wall
## cladding (grows in height) then roof sheets, rooftop units and lighting.
## Layers: foundation, frames (count), purlins (grow length), cladding (grow height), roof (count), mechanical, electrical.

var _bw: float = 10.0
var _eave: float = 5.0
var _ridge: float = 6.5
var _x0: float = 0.0
var _x1: float = 0.0
var _n: int = 2


func _build() -> void:
    orient_long()
    _bw = lz * 0.8
    _n = maxi(footprint.x, footprint.y) + 1
    _eave = height_m * 0.74
    _ridge = height_m * 0.94
    _x0 = -lx * 0.5 + 0.6
    _x1 = lx * 0.5 - 0.6
    if begin_layer("foundation"):
        for i in _n:
            for side: float in [-1.0, 1.0]:
                box(Vector3(_fx(i), 0.15, side * _bw * 0.5), Vector3(0.9, 0.3, 0.9), CONCRETE)
    if begin_layer("frames"):
        for i in _n:
            set_part(i, _n)
            var x: float = _fx(i)
            for side: float in [-1.0, 1.0]:
                box_mm(Vector3(x - 0.1, 0.3, side * _bw * 0.5 - 0.15), Vector3(x + 0.1, _eave, side * _bw * 0.5 + 0.15), STEEL)
                bar(Vector3(x, _eave, side * _bw * 0.5), Vector3(x, _ridge, 0.0), 0.2, 0.25, STEEL_LIGHT)
    if begin_layer("purlins"):
        var f: float = layer_fill()
        for k in 4:
            var t: float = float(k) / 3.0
            for side: float in [-1.0, 1.0]:
                var y: float = lerpf(_eave, _ridge, t)
                var z: float = side * _bw * 0.5 * (1.0 - t)
                cyl_grow(Vector3(_x0, y + 0.12, z), Vector3(_x1, y + 0.12, z), 0.06, 0.06, STEEL_DARK, f, 5, 3)
    if begin_layer("cladding"):
        var f2: float = layer_fill()
        for side: float in [-1.0, 1.0]:
            box_grow(Vector3(_x0 - 0.1, 0.3, side * _bw * 0.5 + (0.0 if side > 0.0 else -0.1)), Vector3(_x1 + 0.1, _eave, side * _bw * 0.5 + (0.1 if side > 0.0 else 0.0)), f2, 1, WHITE)
        box_grow(Vector3(_x0 - 0.1, 0.3, -_bw * 0.5), Vector3(_x0, _eave, _bw * 0.5), f2, 1, TANK_GREY)
        box_grow(Vector3(_x1, 0.3, -_bw * 0.5), Vector3(_x1 + 0.1, _eave, _bw * 0.5), f2, 1, TANK_GREY)
    if begin_layer("roof"):
        for side_i in 2:
            set_part(side_i, 2)
            var side2: float = -1.0 if side_i == 0 else 1.0
            var za: float = side2 * _bw * 0.5
            hexa([Vector3(_x0, _eave, za), Vector3(_x1, _eave, za), Vector3(_x1, _ridge, 0.0), Vector3(_x0, _ridge, 0.0),
                    Vector3(_x0, _eave + 0.12, za), Vector3(_x1, _eave + 0.12, za), Vector3(_x1, _ridge + 0.12, 0.0), Vector3(_x0, _ridge + 0.12, 0.0)], STEEL_LIGHT)
        # gable ends
        set_part(1, 2)
    if begin_layer("mechanical"):
        for i in 2:
            set_part(i, 2)
            var x2: float = lerpf(_x0, _x1, 0.3 + 0.4 * float(i))
            var uh: float = minf(0.6, height_m - _ridge - 0.14)
            if uh > 0.15:
                box_mm(Vector3(x2 - 0.5, _ridge + 0.12, -0.4), Vector3(x2 + 0.5, _ridge + 0.12 + uh, 0.4), PIPE_BLUE)
    if begin_layer("electrical"):
        for i in 3:
            set_part(i, 3)
            var x3: float = lerpf(_x0 + 1.0, _x1 - 1.0, float(i) / 2.0)
            box_mm(Vector3(x3 - 0.4, _eave - 0.3, -0.12), Vector3(x3 + 0.4, _eave - 0.15, 0.12), ELEC_YELLOW)
    end_orient()


func _fx(i: int) -> float:
    return lerpf(_x0, _x1, float(i) / float(maxi(1, _n - 1)))

