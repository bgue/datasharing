class_name PipeRackKit
extends KitBuilder
## Pipe rack: bents (2 columns + a beam per tier) along the long axis, longitudinal struts, pipes per
## tier, cable tray + conduit + junction boxes on the top tier, aircooler / platform, cladding.
## Layers: steel (count of bents), piping (count of pipes, grown along their length), ei (tray length),
## mech (parts), insul (cladding length).

const PIPE_RADII: Array[float] = [0.22, 0.14, 0.3, 0.1, 0.18, 0.26, 0.12, 0.2, 0.16, 0.24]
const PIPE_COLOURS: Array[Color] = [PIPE_ORANGE, PIPE_BLUE, PIPE_GREEN, PIPE_ORANGE, PIPE_RED, PIPE_BLUE]

var _tiers: int = 2
var _rw: float = 4.0
var _bents: int = 2
var _x0: float = 0.0
var _x1: float = 0.0
var _col_h: float = 6.0
var _tier_y: Array[float] = []


func _build() -> void:
    orient_long()
    _tiers = clampi(int(kit_params.get("tiers", 2)), 1, 3)
    _rw = minf(lz * 0.7, 4.8)
    var cells_long: int = maxi(footprint.x, footprint.y)
    _bents = cells_long + 1
    _x0 = -lx * 0.5 + 0.3
    _x1 = lx * 0.5 - 0.3
    var has_mech: bool = present_layers.is_empty() or present_layers.has("mech")
    _col_h = height_m * (0.84 if has_mech else 1.0)
    _tier_y.clear()
    for k in _tiers:
        _tier_y.push_back(_col_h * 0.9 * float(k + 1) / float(_tiers))
    if begin_layer("steel"):
        _steel()
    if begin_layer("piping"):
        _piping()
    if begin_layer("ei"):
        _ei()
    if begin_layer("mech"):
        _mech()
    if begin_layer("insul"):
        _insul()
    end_orient()


func _bent_x(i: int) -> float:
    return lerpf(_x0, _x1, float(i) / float(maxi(1, _bents - 1)))


func _steel() -> void:
    var simple: bool = _bents > 8
    for i in _bents:
        set_part(i, _bents)
        var x: float = _bent_x(i)
        for side: float in [-1.0, 1.0]:
            box_mm(Vector3(x - 0.15, 0, side * _rw * 0.5 - 0.15), Vector3(x + 0.15, _col_h, side * _rw * 0.5 + 0.15), STEEL)
        for k in _tiers:
            ibeam(Vector3(x, _tier_y[k], -_rw * 0.5 - 0.15), Vector3(x, _tier_y[k], _rw * 0.5 + 0.15), 0.3, 0.2, STEEL_LIGHT, simple)
        # bays: longitudinal struts to the next bent appear with it
        if i + 1 < _bents:
            set_part(i + 1, _bents)
            var xn: float = _bent_x(i + 1)
            for k in _tiers:
                for side: float in [-1.0, 1.0]:
                    box_mm(Vector3(x + 0.15, _tier_y[k] - 0.12, side * _rw * 0.5 - 0.08),
                            Vector3(xn - 0.15, _tier_y[k] + 0.12, side * _rw * 0.5 + 0.08), STEEL_DARK)
            # one diagonal brace per bay on the top tier
            if i % 2 == 0:
                bar(Vector3(x, _tier_y[0], -_rw * 0.5), Vector3(xn, _tier_y[_tiers - 1] if _tiers > 1 else _col_h * 0.5, -_rw * 0.5), 0.08, 0.08, STEEL_DARK)


func _pipe_count() -> int:
    return count_hint("pipes", 4 + int(lx / 12.0), 2, 10)


func _piping() -> void:
    var n: int = _pipe_count()
    var per_tier: Array[int] = []
    for k in _tiers:
        per_tier.push_back(0)
    for i in n:
        per_tier[i % _tiers] += 1
    var slot: Array[int] = []
    for k in _tiers:
        slot.push_back(0)
    for i in n:
        var k: int = i % _tiers
        var r: float = PIPE_RADII[i % PIPE_RADII.size()]
        var zmin: float = -_rw * 0.5 + 0.3
        var zmax: float = _rw * 0.5 - 0.3
        if k == _tiers - 1:
            zmin += 0.7  # leave the cable tray side of the top tier free
        var z: float = lerpf(zmin, zmax, (float(slot[k]) + 0.5) / float(maxi(1, per_tier[k])))
        slot[k] += 1
        var y: float = _tier_y[k] + 0.15 + r
        var f: float = part_fill(i, n)
        cyl_grow(Vector3(_x0 - 0.1, y, z), Vector3(_x1 + 0.1, y, z), r, r, PIPE_COLOURS[i % PIPE_COLOURS.size()], f, 6)


func _ei() -> void:
    var top: float = _tier_y[_tiers - 1]
    var z: float = -_rw * 0.5 + 0.4
    var y: float = top + 0.14
    var f: float = layer_fill()
    box_grow(Vector3(_x0, y, z - 0.25), Vector3(_x1, y + 0.06, z + 0.25), f, 0, ELEC_YELLOW)
    box_grow(Vector3(_x0, y + 0.06, z - 0.25), Vector3(_x1, y + 0.2, z - 0.21), f, 0, ELEC_YELLOW)
    box_grow(Vector3(_x0, y + 0.06, z + 0.21), Vector3(_x1, y + 0.2, z + 0.25), f, 0, ELEC_YELLOW)
    # conduit bundle beside the tray
    cyl_grow(Vector3(_x0, y + 0.12, z + 0.45), Vector3(_x1, y + 0.12, z + 0.45), 0.05, 0.05, INSTR_PURPLE, f, 5)
    # junction boxes every other bay
    var jn: int = maxi(1, (_bents - 1) / 2)
    for j in jn:
        var t: float = (float(j) + 0.5) / float(jn)
        set_solid(reached(t))
        var x: float = lerpf(_x0, _x1, t)
        box(Vector3(x, y + 0.2, z + 0.62), Vector3(0.3, 0.3, 0.2), ELEC_YELLOW)


func _mech() -> void:
    # platform + ladder along one side, aircooler (box with two fans) on top
    set_part(0, 3)
    var px: float = lerpf(_x0, _x1, 0.25)
    var y: float = _tier_y[0] + 0.9
    platform(Vector3(px - 1.5, y, _rw * 0.5 - 1.0), Vector3(px + 1.5, y + 0.1, _rw * 0.5 + 0.1), STEEL_LIGHT)
    ladder(Vector3(px + 1.5, 0, _rw * 0.5 + 0.1), y, STEEL_LIGHT, 0.0)
    set_part(1, 3)
    var cl: float = clampf(lx * 0.3, 2.5, 10.0)
    var cx: float = lerpf(_x0, _x1, 0.6)
    var cy0: float = _col_h
    var ch: float = minf(0.45, height_m - cy0)
    if ch > 0.1:
        box_mm(Vector3(cx - cl * 0.5, cy0, -_rw * 0.5), Vector3(cx + cl * 0.5, cy0 + ch, _rw * 0.5), PIPE_BLUE)
    set_part(2, 3)
    var fans: int = maxi(1, int(cl / 2.5))
    for j in fans:
        var fx: float = cx - cl * 0.5 + (float(j) + 0.5) * cl / float(fans)
        var fy: float = cy0 + ch
        var fr: float = minf(_rw * 0.4, cl / float(fans) * 0.4)
        if height_m - fy > 0.05:
            cyl(Vector3(fx, fy, 0), Vector3(fx, minf(height_m, fy + 0.15), 0), fr, fr, STEEL_DARK, 8, 3)


func _insul() -> void:
    # cladding panels along the sides of the lowest pipe tier
    var y0: float = _tier_y[0] + 0.1
    var f: float = layer_fill()
    box_grow(Vector3(_x0, y0, -_rw * 0.5 + 0.12), Vector3(_x1, y0 + 0.9, -_rw * 0.5 + 0.18), f, 0, TANK_GREY)
    box_grow(Vector3(_x0, y0, _rw * 0.5 - 0.18), Vector3(_x1, y0 + 0.9, _rw * 0.5 - 0.12), f, 0, TANK_GREY)
