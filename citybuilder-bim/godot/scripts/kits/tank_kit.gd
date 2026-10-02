class_name TankKit
extends KitBuilder
## Vertical storage tank: ring foundation, shell in courses (grows with height), cone roof, spiral stair,
## nozzle manifold, insulation cladding.
## Layers: ring_foundation, shell (grow height), roof, stair, nozzles (count), insulation (height).

var _r: float = 2.0
var _base_h: float = 0.35
var _roof_h: float = 1.0
var _shell_h: float = 10.0
var _avail: float = 0.8


func _build() -> void:
    var s: float = minf(width_m, depth_m)
    _r = maxf(s * 0.28, minf(s * 0.5 - 0.85, s * 0.4))
    _avail = maxf(0.1, s * 0.5 - _r)
    _base_h = clampf(height_m * 0.03, 0.2, 0.5)
    _roof_h = clampf(_r * 0.2, 0.3, 1.6)
    _shell_h = maxf(0.5, height_m - _base_h - maxf(_roof_h, 0.9))
    if begin_layer("ring_foundation"):
        ring(Vector3.ZERO, _r * 0.7, _r * 1.12, _base_h, CONCRETE, 16)
    if begin_layer("shell"):
        _shell()
    if begin_layer("roof"):
        var top: float = _base_h + _shell_h
        cyl(Vector3(0, top, 0), Vector3(0, top + _roof_h, 0), _r * 1.02, 0.0, STEEL_LIGHT, 16, 1)
        vcyl(Vector3(0, top + _roof_h, 0), minf(0.4, height_m - top - _roof_h), 0.18, STEEL_DARK, 6)
        # roof handrail around the rim
        ring_rail(Vector3(0, top, 0), _r * 0.98, STEEL_LIGHT, minf(0.9, height_m - top) - 0.03, 14)
        # rim ring
        cyl(Vector3(0, top - 0.12, 0), Vector3(0, top, 0), _r * 1.04, _r * 1.04, STEEL_DARK, 16, 0)
    if begin_layer("stair"):
        var steps: int = clampi(int(_shell_h / 0.3), 8, 44)
        var sw: float = minf(0.7, _avail * 0.6)
        spiral_stair(Vector3.ZERO, _r + _avail * 0.35, _base_h, _base_h + _shell_h - 1.0, PI * 1.6, sw, STEEL_LIGHT, steps, 0.4)
    if begin_layer("nozzles"):
        _nozzles()
    if begin_layer("insulation"):
        var f: float = layer_fill()
        cyl_grow(Vector3(0, _base_h, 0), Vector3(0, _base_h + _shell_h, 0), _r * 1.04, _r * 1.04, WHITE, f, 16, 0)


func _shell() -> void:
    var courses: int = 4
    var ch: float = _shell_h / float(courses)
    for i in courses:
        var f: float = part_fill(i, courses)
        var y0: float = _base_h + ch * float(i)
        var c: Color = TANK_GREY if i % 2 == 0 else shade(TANK_GREY, 0.9)
        cyl_grow(Vector3(0, y0, 0), Vector3(0, y0 + ch, 0), _r, _r, c, f, 16, 0)
    # floor plate under the shell
    set_solid(part_fill(0, courses) > 0.0)
    cyl(Vector3(0, _base_h, 0), Vector3(0, _base_h + 0.02, 0), _r, _r, STEEL_DARK, 16, 2)


func _nozzles() -> void:
    var reach: float = minf(0.7, _avail * 0.8)
    for i in 3:
        set_part(i, 3)
        var ang: float = PI + 0.7 * float(i)  # opposite side from the stair start
        var d := Vector3(cos(ang), 0, sin(ang))
        var y: float = _base_h + 0.8 + 0.5 * float(i)
        cyl(d * (_r - 0.05) + Vector3(0, y, 0), d * (_r + reach) + Vector3(0, y, 0), 0.16, 0.16, PIPE_ORANGE, 8, 3)
    # manhole cover on the lowest course
    set_part(0, 3)
    var md := Vector3(cos(PI * 1.5), 0, sin(PI * 1.5))
    cyl(md * (_r - 0.05) + Vector3(0, _base_h + 1.0, 0), md * (_r + reach * 0.6) + Vector3(0, _base_h + 1.0, 0), 0.35, 0.35, STEEL_DARK, 8, 3)
