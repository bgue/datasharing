class_name KitBuilder
extends RefCounted
## Base class of the procedural visual-kit builders (docs/06 Track B).
##
## A builder turns {footprint_cells, height_m, cell_size_m, layer_fills, variant, seed, ...} into one
## ArrayMesh in METRES, local origin at the footprint centre on the floor (y = 0 is the storey floor),
## x/z spanning footprint * cell_size_m and y spanning height_m.
##
## Rendering approach (follows BimView): flat-shaded triangles with per-vertex colour used as albedo by a
## shared StandardMaterial3D. Solid parts go to surface "solid" (opaque), unfilled parts to surface
## "ghost" (same colour, alpha GHOST_ALPHA, alpha-blended material). Geometry is built straight into
## packed arrays (no SurfaceTool) so the same params always give a byte-identical mesh.
##
## Subclasses override `_build()` and use `begin_layer(id)` / `set_part(i, n)` / the primitives below.

const GHOST_ALPHA: float = 0.15
## Fills are quantised to 1/FILL_STEPS so equal (quantised) params give equal meshes and cache keys.
const FILL_STEPS: float = 50.0
const EPS: float = 0.001
const INSPECTED_TINT: Color = Color(0.2, 0.9, 0.35)
const REWORK_TINT: Color = Color(0.95, 0.15, 0.1)

# Flat Kenney-like palette
const STEEL: Color = Color(0.55, 0.57, 0.61)
const STEEL_DARK: Color = Color(0.34, 0.36, 0.4)
const STEEL_LIGHT: Color = Color(0.72, 0.74, 0.78)
const CONCRETE: Color = Color(0.74, 0.73, 0.7)
const CONCRETE_DARK: Color = Color(0.58, 0.57, 0.55)
const PIPE_ORANGE: Color = Color(0.95, 0.5, 0.1)
const PIPE_BLUE: Color = Color(0.25, 0.5, 0.95)
const PIPE_GREEN: Color = Color(0.3, 0.7, 0.4)
const PIPE_RED: Color = Color(0.85, 0.25, 0.2)
const ELEC_YELLOW: Color = Color(0.98, 0.85, 0.2)
const INSTR_PURPLE: Color = Color(0.6, 0.4, 0.85)
const WHITE: Color = Color(0.92, 0.92, 0.94)
const TANK_GREY: Color = Color(0.8, 0.82, 0.86)
const BRICK: Color = Color(0.72, 0.5, 0.38)
const BEIGE: Color = Color(0.86, 0.78, 0.6)
const DARK: Color = Color(0.2, 0.21, 0.24)
const PAINT_RED: Color = Color(0.82, 0.2, 0.18)
const MED_MAGENTA: Color = Color(0.85, 0.2, 0.7)

static var _solid_material: StandardMaterial3D = null
static var _ghost_material: StandardMaterial3D = null

# ---- inputs (set by _configure)
var kit_id: String = ""
var footprint: Vector2i = Vector2i(1, 1)
var cell_size_m: float = 6.0
var width_m: float = 6.0  ## footprint extent along x
var depth_m: float = 6.0  ## footprint extent along z
var height_m: float = 4.0
var fills: Dictionary = {}  ## layer id -> quantised fill 0..1 (missing = 1.0)
var variant: String = ""
var seed_value: int = 0
var kit_params: Dictionary = {}  ## manifest "params" of the kit (tiers, orientation, ...)
var counts: Dictionary = {}  ## optional quantity hints, e.g. {"pipes": 6}
var layer_states: Dictionary = {}  ## layer id -> "inspected" | "rework" | ""
var present_layers: Dictionary = {}  ## layer id -> true; empty = every layer is drawn
var show_ghost: bool = true
var rng: RandomNumberGenerator = RandomNumberGenerator.new()

# ---- orientation helpers (see orient_long)
var lx: float = 6.0  ## extent along the build axis (the longer footprint side after orient_long)
var lz: float = 6.0  ## extent across it

# ---- build state
var _fill: float = 1.0
var _solid: bool = true
var _tint_amt: float = 0.0
var _tint_col: Color = Color.WHITE
var _layer: String = ""
var _xf: Transform3D = Transform3D.IDENTITY
var _has_xf: bool = false
var _xf_stack: Array[Transform3D] = []
var _orient_pushed: bool = false
var _sv: PackedVector3Array = PackedVector3Array()
var _sn: PackedVector3Array = PackedVector3Array()
var _sc: PackedColorArray = PackedColorArray()
var _gv: PackedVector3Array = PackedVector3Array()
var _gn: PackedVector3Array = PackedVector3Array()
var _gc: PackedColorArray = PackedColorArray()


# ------------------------------------------------------------------ materials

static func solid_material() -> StandardMaterial3D:
    if _solid_material == null:
        var m := StandardMaterial3D.new()
        m.vertex_color_use_as_albedo = true
        m.shading_mode = BaseMaterial3D.SHADING_MODE_PER_PIXEL
        m.roughness = 0.8
        _solid_material = m
    return _solid_material


static func ghost_material() -> StandardMaterial3D:
    if _ghost_material == null:
        var m := StandardMaterial3D.new()
        m.vertex_color_use_as_albedo = true
        m.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
        m.shading_mode = BaseMaterial3D.SHADING_MODE_PER_PIXEL
        m.roughness = 0.8
        _ghost_material = m
    return _ghost_material


static func quantise_fill(f: float) -> float:
    return roundf(clampf(f, 0.0, 1.0) * FILL_STEPS) / FILL_STEPS


# ------------------------------------------------------------------ entry point

func build(params: Dictionary) -> ArrayMesh:
    _configure(params)
    _reset()
    _build()
    while not _xf_stack.is_empty():
        pop_xf()
    return _finish()


## Subclasses draw their layers here.
func _build() -> void:
    pass


func _configure(params: Dictionary) -> void:
    kit_id = str(params.get("kit", kit_id))
    var fpv: Variant = params.get("footprint_cells", Vector2i(1, 1))
    if fpv is Vector2i:
        footprint = fpv
    elif fpv is Array and (fpv as Array).size() >= 2:
        footprint = Vector2i(int(fpv[0]), int(fpv[1]))
    footprint = Vector2i(maxi(1, footprint.x), maxi(1, footprint.y))
    cell_size_m = maxf(0.1, float(params.get("cell_size_m", 6.0)))
    width_m = float(footprint.x) * cell_size_m
    depth_m = float(footprint.y) * cell_size_m
    height_m = maxf(0.5, float(params.get("height_m", 4.0)))
    variant = str(params.get("variant", ""))
    seed_value = int(params.get("seed", 0))
    kit_params = params.get("kit_params", {})
    counts = params.get("counts", {})
    layer_states = params.get("layer_states", {})
    show_ghost = bool(params.get("show_ghost", true))
    fills = {}
    var lf: Dictionary = params.get("layer_fills", {})
    for k in lf:
        fills[str(k)] = quantise_fill(float(lf[k]))
    present_layers = {}
    for l in params.get("present_layers", []):
        present_layers[str(l)] = true
    rng.seed = seed_value
    lx = maxf(width_m, depth_m)
    lz = minf(width_m, depth_m)


func _reset() -> void:
    _sv = PackedVector3Array()
    _sn = PackedVector3Array()
    _sc = PackedColorArray()
    _gv = PackedVector3Array()
    _gn = PackedVector3Array()
    _gc = PackedColorArray()
    _xf = Transform3D.IDENTITY
    _has_xf = false
    _xf_stack.clear()
    _orient_pushed = false
    _fill = 1.0
    _solid = true
    _tint_amt = 0.0


func _finish() -> ArrayMesh:
    var mesh := ArrayMesh.new()
    if _sv.size() > 0:
        _add_surface(mesh, _sv, _sn, _sc, "solid", solid_material())
    if _gv.size() > 0:
        _add_surface(mesh, _gv, _gn, _gc, "ghost", ghost_material())
    mesh.set_meta("solid_vertices", _sv.size())
    mesh.set_meta("ghost_vertices", _gv.size())
    mesh.set_meta("triangles", (_sv.size() + _gv.size()) / 3)
    return mesh


static func _add_surface(mesh: ArrayMesh, v: PackedVector3Array, n: PackedVector3Array, c: PackedColorArray,
        surf_name: String, mat: Material) -> void:
    var arrays: Array = []
    arrays.resize(Mesh.ARRAY_MAX)
    arrays[Mesh.ARRAY_VERTEX] = v
    arrays[Mesh.ARRAY_NORMAL] = n
    arrays[Mesh.ARRAY_COLOR] = c
    mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
    var idx: int = mesh.get_surface_count() - 1
    mesh.surface_set_name(idx, surf_name)
    mesh.surface_set_material(idx, mat)


## LOD stand-in: one box over the footprint, tinted by overall fill (ghost surface when nothing is done).
func build_box(params: Dictionary, col: Color, fill: float) -> ArrayMesh:
    _configure(params)
    _reset()
    _tint_amt = 0.0
    _solid = fill > 0.02
    box_mm(Vector3(-width_m * 0.5, 0, -depth_m * 0.5), Vector3(width_m * 0.5, height_m * 0.9, depth_m * 0.5), col)
    return _finish()


# ------------------------------------------------------------------ layers and fill

## Starts a layer. Returns false (draw nothing) when the instance does not show this layer.
func begin_layer(id: String) -> bool:
    _layer = id
    if not present_layers.is_empty() and not present_layers.has(id):
        return false
    _fill = float(fills.get(id, 1.0))
    _solid = _fill >= 1.0 - EPS
    var st: String = str(layer_states.get(id, ""))
    match st:
        "inspected":
            _tint_amt = 0.22
            _tint_col = INSPECTED_TINT
        "rework":
            _tint_amt = 0.45
            _tint_col = REWORK_TINT
        _:
            _tint_amt = 0.0
    return true


func layer_fill() -> float:
    return _fill


## Local fill (0..1) of the i-th of n repeated parts when the layer fill is spread across them.
func part_fill(i: int, n: int) -> float:
    return clampf(_fill * float(n) - float(i), 0.0, 1.0)


## "count" growth: part i of n is solid once the layer fill covers it. Returns its local fill.
func set_part(i: int, n: int) -> float:
    var f: float = part_fill(i, n)
    _solid = f >= 1.0 - EPS
    return f


func set_solid(b: bool) -> void:
    _solid = b


## Local-fill helper for an extent: is position t in 0..1 along the layer already built?
func reached(t: float) -> bool:
    return _fill >= t - EPS


# ------------------------------------------------------------------ orientation and transforms

static func rot_y(angle: float) -> Transform3D:
    return Transform3D(Basis(Vector3.UP, angle), Vector3.ZERO)


static func at(pos: Vector3, yaw: float = 0.0) -> Transform3D:
    return Transform3D(Basis(Vector3.UP, yaw), pos)


func push_xf(t: Transform3D) -> void:
    _xf_stack.push_back(_xf)
    _xf = _xf * t
    _has_xf = true


func pop_xf() -> void:
    if _xf_stack.is_empty():
        return
    _xf = _xf_stack.pop_back()
    _has_xf = not _xf_stack.is_empty()


## Build along the longer footprint side: after this call x is the long axis (lx long, lz across).
## Rotates the frame by 90 degrees when the footprint is longer in z.
func orient_long() -> void:
    lx = maxf(width_m, depth_m)
    lz = minf(width_m, depth_m)
    if depth_m > width_m + 0.001:
        push_xf(rot_y(PI * 0.5))
        _orient_pushed = true


func end_orient() -> void:
    if _orient_pushed:
        pop_xf()
        _orient_pushed = false


static func shade(c: Color, k: float) -> Color:
    return Color(clampf(c.r * k, 0.0, 1.0), clampf(c.g * k, 0.0, 1.0), clampf(c.b * k, 0.0, 1.0), c.a)


# ------------------------------------------------------------------ triangle emission

func _paint(col: Color) -> Color:
    if _solid:
        var c: Color = col
        if _tint_amt > 0.0:
            c = c.lerp(_tint_col, _tint_amt)
        c.a = 1.0
        return c
    var g: Color = col
    g.a = GHOST_ALPHA
    return g


## One triangle; `hint` is the outward direction (local space) used to fix the winding.
## Godot front faces are clockwise, so the triangle is stored (a, c, b) of a counter-clockwise a, b, c.
func tri(a: Vector3, b: Vector3, c: Vector3, col: Color, hint: Vector3) -> void:
    if _has_xf:
        a = _xf * a
        b = _xf * b
        c = _xf * c
        hint = _xf.basis * hint
    var n: Vector3 = (b - a).cross(c - a)
    var ln: float = n.length()
    if ln < 1e-9:
        return
    n /= ln
    if hint != Vector3.ZERO and n.dot(hint) < 0.0:
        var t: Vector3 = b
        b = c
        c = t
        n = -n
    if n.y < -0.95 and a.y <= 0.002 and b.y <= 0.002 and c.y <= 0.002:
        return  # floor-facing faces on the ground are never seen
    var pc: Color = _paint(col)
    if _solid:
        _sv.push_back(a)
        _sv.push_back(c)
        _sv.push_back(b)
        _sn.push_back(n)
        _sn.push_back(n)
        _sn.push_back(n)
        _sc.push_back(pc)
        _sc.push_back(pc)
        _sc.push_back(pc)
    elif show_ghost:
        _gv.push_back(a)
        _gv.push_back(c)
        _gv.push_back(b)
        _gn.push_back(n)
        _gn.push_back(n)
        _gn.push_back(n)
        _gc.push_back(pc)
        _gc.push_back(pc)
        _gc.push_back(pc)


## Quad a-b-c-d around its perimeter.
func quad(a: Vector3, b: Vector3, c: Vector3, d: Vector3, col: Color, hint: Vector3) -> void:
    tri(a, b, c, col, hint)
    tri(a, c, d, col, hint)


# ------------------------------------------------------------------ primitives

func box(centre: Vector3, size: Vector3, col: Color) -> void:
    box_mm(centre - size * 0.5, centre + size * 0.5, col)


func box_mm(mn: Vector3, mx: Vector3, col: Color) -> void:
    var p000 := Vector3(mn.x, mn.y, mn.z)
    var p100 := Vector3(mx.x, mn.y, mn.z)
    var p110 := Vector3(mx.x, mx.y, mn.z)
    var p010 := Vector3(mn.x, mx.y, mn.z)
    var p001 := Vector3(mn.x, mn.y, mx.z)
    var p101 := Vector3(mx.x, mn.y, mx.z)
    var p111 := Vector3(mx.x, mx.y, mx.z)
    var p011 := Vector3(mn.x, mx.y, mx.z)
    quad(p000, p100, p110, p010, col, Vector3(0, 0, -1))
    quad(p001, p101, p111, p011, col, Vector3(0, 0, 1))
    quad(p000, p001, p011, p010, col, Vector3(-1, 0, 0))
    quad(p100, p101, p111, p110, col, Vector3(1, 0, 0))
    quad(p000, p100, p101, p001, col, Vector3(0, -1, 0))
    quad(p010, p110, p111, p011, col, Vector3(0, 1, 0))


## Box that fills from the min side to fraction f along axis (0 x, 1 y, 2 z): solid part then ghost rest.
func box_grow(mn: Vector3, mx: Vector3, f: float, axis: int, col: Color) -> void:
    var keep: bool = _solid
    if f >= 1.0 - EPS:
        _solid = true
        box_mm(mn, mx, col)
    elif f <= EPS:
        _solid = false
        box_mm(mn, mx, col)
    else:
        var cut: Vector3 = mx
        var start: Vector3 = mn
        cut[axis] = lerpf(mn[axis], mx[axis], f)
        start[axis] = cut[axis]
        _solid = true
        box_mm(mn, cut, col)
        _solid = false
        box_mm(start, mx, col)
    _solid = keep


## Straight (optionally tapered) prism between two points; segs sides; caps bit0 = start, bit1 = end.
func cyl(p0: Vector3, p1: Vector3, r0: float, r1: float, col: Color, segs: int = 8, caps: int = 3) -> void:
    var axis: Vector3 = p1 - p0
    var length: float = axis.length()
    if length < 1e-6:
        return
    var ax: Vector3 = axis / length
    var ref: Vector3 = Vector3.UP if absf(ax.y) < 0.95 else Vector3.RIGHT
    var u: Vector3 = ax.cross(ref).normalized()
    var v: Vector3 = ax.cross(u).normalized()
    var prev_d: Vector3 = u
    for i in segs:
        var ang: float = TAU * float(i + 1) / float(segs)
        var d1: Vector3 = u * cos(ang) + v * sin(ang)
        var d0: Vector3 = prev_d
        var a0: Vector3 = p0 + d0 * r0
        var a1: Vector3 = p0 + d1 * r0
        var b0: Vector3 = p1 + d0 * r1
        var b1: Vector3 = p1 + d1 * r1
        var out: Vector3 = d0 + d1
        quad(a0, a1, b1, b0, col, out)
        if (caps & 1) != 0 and r0 > 1e-5:
            tri(p0, a1, a0, col, -ax)
        if (caps & 2) != 0 and r1 > 1e-5:
            tri(p1, b0, b1, col, ax)
        prev_d = d1


## Cylinder that fills from p0 towards p1 (fraction f): solid part then ghost rest.
func cyl_grow(p0: Vector3, p1: Vector3, r0: float, r1: float, col: Color, f: float, segs: int = 8, caps: int = 3) -> void:
    var keep: bool = _solid
    if f >= 1.0 - EPS:
        _solid = true
        cyl(p0, p1, r0, r1, col, segs, caps)
    elif f <= EPS:
        _solid = false
        cyl(p0, p1, r0, r1, col, segs, caps)
    else:
        var pm: Vector3 = p0.lerp(p1, f)
        var rm: float = lerpf(r0, r1, f)
        _solid = true
        cyl(p0, pm, r0, rm, col, segs, caps & 1)
        _solid = false
        cyl(pm, p1, rm, r1, col, segs, caps & 2)
    _solid = keep


func vcyl(base: Vector3, h: float, r: float, col: Color, segs: int = 8) -> void:
    cyl(base, base + Vector3(0, h, 0), r, r, col, segs, 3)


func cone(base: Vector3, h: float, r: float, col: Color, segs: int = 8) -> void:
    cyl(base, base + Vector3(0, h, 0), r, 0.0, col, segs, 1)


## Squashed hemisphere standing on `base`, bulging along `ax` (unit), horizontal radius r, height h.
func dome(base: Vector3, ax: Vector3, r: float, h: float, col: Color, segs: int = 10, rings: int = 3) -> void:
    var prev_p: Vector3 = base
    var prev_r: float = r
    for j in range(1, rings + 1):
        var phi: float = (PI * 0.5) * float(j) / float(rings)
        var rr: float = r * cos(phi)
        var p: Vector3 = base + ax * (h * sin(phi))
        cyl(prev_p, p, prev_r, rr, col, segs, 0)
        prev_p = p
        prev_r = rr


## Annulus slab (ring foundation, platform ring, fan ring) between radii, from y0 to y1.
func ring(centre: Vector3, r_in: float, r_out: float, h: float, col: Color, segs: int = 12) -> void:
    for i in segs:
        var a0: float = TAU * float(i) / float(segs)
        var a1: float = TAU * float(i + 1) / float(segs)
        var d0 := Vector3(cos(a0), 0, sin(a0))
        var d1 := Vector3(cos(a1), 0, sin(a1))
        var o0: Vector3 = centre + d0 * r_out
        var o1: Vector3 = centre + d1 * r_out
        var i0: Vector3 = centre + d0 * r_in
        var i1: Vector3 = centre + d1 * r_in
        var up := Vector3(0, h, 0)
        quad(o0, o1, o1 + up, o0 + up, col, d0 + d1)
        quad(i0, i1, i1 + up, i0 + up, col, -(d0 + d1))
        quad(i0 + up, o0 + up, o1 + up, i1 + up, col, Vector3.UP)
        quad(i0, o0, o1, i1, col, Vector3.DOWN)


## Hollow cylinder (tube / ring along an arbitrary axis): outer wall, bore wall and both annular ends.
func tube(p0: Vector3, p1: Vector3, r_out: float, r_in: float, col: Color, segs: int = 12) -> void:
    var axis: Vector3 = p1 - p0
    var length: float = axis.length()
    if length < 1e-6:
        return
    var ax: Vector3 = axis / length
    var ref: Vector3 = Vector3.UP if absf(ax.y) < 0.95 else Vector3.RIGHT
    var u: Vector3 = ax.cross(ref).normalized()
    var v: Vector3 = ax.cross(u).normalized()
    var prev_d: Vector3 = u
    for i in segs:
        var ang: float = TAU * float(i + 1) / float(segs)
        var d1: Vector3 = u * cos(ang) + v * sin(ang)
        var d0: Vector3 = prev_d
        var out: Vector3 = d0 + d1
        quad(p0 + d0 * r_out, p0 + d1 * r_out, p1 + d1 * r_out, p1 + d0 * r_out, col, out)
        quad(p0 + d0 * r_in, p0 + d1 * r_in, p1 + d1 * r_in, p1 + d0 * r_in, shade(col, 0.8), -out)
        quad(p0 + d0 * r_in, p0 + d1 * r_in, p0 + d1 * r_out, p0 + d0 * r_out, col, -ax)
        quad(p1 + d0 * r_in, p1 + d1 * r_in, p1 + d1 * r_out, p1 + d0 * r_out, col, ax)
        prev_d = d1


## Pipe along a polyline. f < 0 draws it with the current solid / ghost state; f in 0..1 makes the first
## fraction of its length solid and the rest ghost (pipes that fill up).
func pipe(points: Array, r: float, col: Color, f: float = -1.0, segs: int = 6) -> void:
    var total: float = 0.0
    for i in range(points.size() - 1):
        total += (points[i + 1] as Vector3).distance_to(points[i])
    if total <= 0.0:
        return
    var run: float = 0.0
    for i in range(points.size() - 1):
        var p0: Vector3 = points[i]
        var p1: Vector3 = points[i + 1]
        var l: float = p0.distance_to(p1)
        if f < 0.0:
            cyl(p0, p1, r, r, col, segs, 3)
        else:
            var local: float = clampf((f * total - run) / maxf(l, 1e-6), 0.0, 1.0)
            cyl_grow(p0, p1, r, r, col, local, segs, 3)
        run += l


## I-beam approximation: two flanges and a web (thin boxes) between p0 and p1.
func ibeam(p0: Vector3, p1: Vector3, depth: float, flange: float, col: Color, simple: bool = false) -> void:
    var ax: Vector3 = p1 - p0
    var length: float = ax.length()
    if length < 1e-6:
        return
    if simple:
        push_xf(Transform3D(basis_along(ax), (p0 + p1) * 0.5))
        box(Vector3.ZERO, Vector3(length, depth, flange), col)
        pop_xf()
        return
    var tf: float = maxf(0.03, depth * 0.14)
    push_xf(Transform3D(basis_along(ax), (p0 + p1) * 0.5))
    box(Vector3(0, depth * 0.5 - tf * 0.5, 0), Vector3(length, tf, flange), col)
    box(Vector3(0, -depth * 0.5 + tf * 0.5, 0), Vector3(length, tf, flange), col)
    box(Vector3.ZERO, Vector3(length, depth - 2.0 * tf, maxf(0.03, flange * 0.18)), shade(col, 0.9))
    pop_xf()


static func basis_along(ax: Vector3) -> Basis:
    var x: Vector3 = ax.normalized()
    var up: Vector3 = Vector3.UP if absf(x.y) < 0.95 else Vector3.RIGHT
    var z: Vector3 = x.cross(up).normalized()
    var y: Vector3 = z.cross(x).normalized()
    return Basis(x, y, z)


## Thin box (plate / bar) between two points with the given cross-section; no rotation about the axis.
func bar(p0: Vector3, p1: Vector3, w: float, h: float, col: Color) -> void:
    var ax: Vector3 = p1 - p0
    var length: float = ax.length()
    if length < 1e-6:
        return
    push_xf(Transform3D(basis_along(ax), (p0 + p1) * 0.5))
    box(Vector3.ZERO, Vector3(length, h, w), col)
    pop_xf()


## Straight stair flight rising along local +x from `origin` (yaw about y): block treads.
func stair_straight(origin: Vector3, yaw: float, width: float, rise: float, run: float, steps: int, col: Color) -> void:
    push_xf(at(origin, yaw))
    var dx: float = run / float(steps)
    var dy: float = rise / float(steps)
    for i in steps:
        box_mm(Vector3(float(i) * dx, 0.0, -width * 0.5), Vector3(float(i + 1) * dx, dy * float(i + 1), width * 0.5), col)
    pop_xf()


## Vertical ladder: two rails and rungs, on the face at yaw (local +x faces out).
func ladder(base: Vector3, h: float, col: Color, yaw: float = 0.0, width: float = 0.45) -> void:
    push_xf(at(base, yaw))
    var rail_t: float = 0.05
    box_mm(Vector3(0, 0, -width * 0.5), Vector3(rail_t, h, -width * 0.5 + rail_t), col)
    box_mm(Vector3(0, 0, width * 0.5 - rail_t), Vector3(rail_t, h, width * 0.5), col)
    var spacing: float = maxf(0.3, h / 36.0)
    var y: float = spacing
    while y < h:
        box_mm(Vector3(0, y - 0.02, -width * 0.5), Vector3(rail_t, y + 0.02, width * 0.5), col)
        y += spacing
    pop_xf()


## Handrail between two points (posts + top bar) at height h.
func rail(p0: Vector3, p1: Vector3, col: Color, h: float = 1.0, post_gap: float = 1.6) -> void:
    var length: float = p0.distance_to(p1)
    var n: int = maxi(1, int(ceil(length / post_gap)))
    for i in range(n + 1):
        var p: Vector3 = p0.lerp(p1, float(i) / float(n))
        box(p + Vector3(0, h * 0.5, 0), Vector3(0.05, h, 0.05), col)
    bar(p0 + Vector3(0, h, 0), p1 + Vector3(0, h, 0), 0.05, 0.05, col)
    bar(p0 + Vector3(0, h * 0.5, 0), p1 + Vector3(0, h * 0.5, 0), 0.04, 0.04, col)


## Square-ish grating platform (box slab) with rails on the open sides.
func platform(mn: Vector3, mx: Vector3, col: Color, with_rail: bool = true) -> void:
    box_mm(mn, Vector3(mx.x, mn.y + 0.08, mx.z), col)
    if with_rail:
        var y: float = mn.y + 0.08
        var c: Color = shade(col, 1.15)
        rail(Vector3(mn.x, y, mn.z), Vector3(mx.x, y, mn.z), c, 1.0, 2.0)
        rail(Vector3(mn.x, y, mx.z), Vector3(mx.x, y, mx.z), c, 1.0, 2.0)
        rail(Vector3(mn.x, y, mn.z), Vector3(mn.x, y, mx.z), c, 1.0, 2.0)
        rail(Vector3(mx.x, y, mn.z), Vector3(mx.x, y, mx.z), c, 1.0, 2.0)


## Spiral stair around a vertical axis: block treads between y0 and y1 (radius = centre of tread).
func spiral_stair(centre: Vector3, radius: float, y0: float, y1: float, sweep: float, width: float, col: Color,
        steps: int, start_angle: float = 0.0) -> void:
    var dy: float = (y1 - y0) / float(steps)
    var arc: float = sweep / float(steps)
    var tread_len: float = maxf(0.2, radius * arc * 1.1)
    for i in steps:
        var ang: float = start_angle + arc * (float(i) + 0.5)
        var pos: Vector3 = centre + Vector3(cos(ang) * radius, y0 + dy * float(i + 1), sin(ang) * radius)
        push_xf(at(pos, -ang))
        box(Vector3(0, -0.04, 0), Vector3(width, 0.08, tread_len), col)
        pop_xf()
    # outer handrail as a polyline of thin bars
    var prev: Vector3 = Vector3.ZERO
    for i in range(steps + 1):
        var ang2: float = start_angle + arc * float(i)
        var p: Vector3 = centre + Vector3(cos(ang2) * (radius + width * 0.5), y0 + dy * float(i) + 1.0, sin(ang2) * (radius + width * 0.5))
        if i > 0 and (i % 2 == 0 or i == steps):
            bar(prev, p, 0.04, 0.04, shade(col, 1.2))
        if i == 0 or (i % 2 == 0 or i == steps):
            prev = p


# ------------------------------------------------------------------ misc helpers

## Rounded count from a quantity hint, clamped.
func count_hint(key: String, fallback: int, lo: int, hi: int) -> int:
    return clampi(int(counts.get(key, fallback)), lo, hi)


## Equipment envelope: length along the long axis and width, capped by the footprint and by height ratios.
func eq_len(h_ratio: float, foot_ratio: float = 0.85) -> float:
    return minf(maxf(width_m, depth_m) * foot_ratio, height_m * h_ratio)


func eq_wid(h_ratio: float, foot_ratio: float = 0.85) -> float:
    return minf(minf(width_m, depth_m) * foot_ratio, height_m * h_ratio)
