class_name ModuleKit
extends KitBuilder
## Prefabricated process module: steel box frame (members appear one by one), deck, internal equipment
## (vertical vessel, horizontal exchanger, pump skids) and pipe runs.
## Layers: frame (count of members), equipment (count), piping (grown along the pipes).

var _fw: float = 10.0
var _fd: float = 4.5
var _nx: int = 3


func _build() -> void:
    orient_long()
    _fw = lx * 0.9
    _fd = lz * 0.75
    _nx = clampi(int(roundf(_fw / 3.0)), 1, 8)
    var mid: float = height_m * 0.45
    if begin_layer("frame"):
        _frame(mid)
    if begin_layer("equipment"):
        _equipment(mid)
    if begin_layer("piping"):
        _piping(mid)
    end_orient()


func _frame(mid: float) -> void:
    var members: Array = []  # [p0, p1, w, kind]
    var half_w: float = _fw * 0.5
    var half_d: float = _fd * 0.5
    for i in range(_nx + 1):
        var x: float = lerpf(-half_w, half_w, float(i) / float(_nx))
        for side: float in [-1.0, 1.0]:
            members.push_back([Vector3(x, 0, side * half_d), Vector3(x, height_m, side * half_d), 0.22, 0])
    for y: float in [0.1, mid, height_m - 0.11]:
        for side: float in [-1.0, 1.0]:
            members.push_back([Vector3(-half_w, y, side * half_d), Vector3(half_w, y, side * half_d), 0.2, 1])
        for i in range(_nx + 1):
            var x2: float = lerpf(-half_w, half_w, float(i) / float(_nx))
            members.push_back([Vector3(x2, y, -half_d), Vector3(x2, y, half_d), 0.18, 1])
    for i in _nx:
        var xa: float = lerpf(-half_w, half_w, float(i) / float(_nx))
        var xb: float = lerpf(-half_w, half_w, float(i + 1) / float(_nx))
        members.push_back([Vector3(xa, 0.2, half_d), Vector3(xb, height_m - 0.2, half_d), 0.1, 2])
    var n: int = members.size()
    for i in n:
        set_part(i, n)
        var m: Array = members[i]
        var col: Color = STEEL if int(m[3]) == 0 else (STEEL_LIGHT if int(m[3]) == 1 else STEEL_DARK)
        bar(m[0], m[1], float(m[2]), float(m[2]), col)


func _equipment(mid: float) -> void:
    var half_w: float = _fw * 0.5
    set_part(0, 4)
    box_mm(Vector3(-half_w * 0.95, mid, -_fd * 0.5), Vector3(half_w * 0.95, mid + 0.08, _fd * 0.5), STEEL_DARK)
    set_part(1, 4)
    var vr: float = minf(_fd * 0.22, height_m * 0.12)
    vcyl(Vector3(-half_w * 0.45, 0.1, 0), mid - 0.3, vr, STEEL_LIGHT, 10)
    dome(Vector3(-half_w * 0.45, mid - 0.2, 0), Vector3.UP, vr, vr * 0.5, STEEL, 10, 2)
    set_part(2, 4)
    var er: float = minf(_fd * 0.16, (height_m - mid) * 0.35)
    cyl(Vector3(half_w * 0.1, mid + 0.08 + er + 0.1, 0), Vector3(half_w * 0.7, mid + 0.08 + er + 0.1, 0), er, er, PIPE_BLUE, 10, 3)
    set_part(3, 4)
    for i in 2:
        var px: float = half_w * (0.2 + 0.4 * float(i))
        box(Vector3(px, 0.4, -_fd * 0.25), Vector3(0.9, 0.5, 0.6), PIPE_BLUE)
        cyl(Vector3(px + 0.5, 0.45, -_fd * 0.25), Vector3(px + 0.9, 0.45, -_fd * 0.25), 0.2, 0.2, ELEC_YELLOW, 8, 3)


func _piping(mid: float) -> void:
    var half_w: float = _fw * 0.5
    var lines: Array = [
        [Vector3(-half_w * 0.9, mid * 0.5, _fd * 0.3), Vector3(-half_w * 0.45, mid * 0.5, _fd * 0.3), Vector3(-half_w * 0.45, mid - 0.2, _fd * 0.3)],
        [Vector3(-half_w * 0.45, mid * 0.3, 0), Vector3(half_w * 0.2, mid * 0.3, 0), Vector3(half_w * 0.2, mid + 0.6, 0)],
        [Vector3(half_w * 0.2, 0.45, -_fd * 0.25), Vector3(half_w * 0.9, 0.45, -_fd * 0.25), Vector3(half_w * 0.9, mid * 0.5, _fd * 0.2)],
        [Vector3(-half_w * 0.9, height_m - 0.5, -_fd * 0.3), Vector3(half_w * 0.9, height_m - 0.5, -_fd * 0.3)],
    ]
    var n: int = lines.size()
    for i in n:
        pipe(lines[i], 0.09 + 0.02 * float(i % 2), PIPE_ORANGE if i % 2 == 0 else PIPE_GREEN, part_fill(i, n), 6)
