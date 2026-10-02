class_name MarkerKit
extends KitBuilder
## Marker meshes for virtual tasks (survey, dewatering, scaffold, lift_plan, permit, test, shoring, crane).
## Each marker is a small solid (no layers) in metres with its origin on the ground at the anchor point,
## at most 6 m across so it fits a 6 m cell. `mesh_for(id)` is the Callable-friendly cached entry point.

const IDS: Array[String] = ["survey", "dewatering", "scaffold", "lift_plan", "permit", "test", "shoring", "crane"]
const DEFAULT_COLOURS: Dictionary = {
    "survey": Color(0.95, 0.55, 0.1), "dewatering": Color(0.25, 0.55, 0.95), "scaffold": Color(0.95, 0.8, 0.2),
    "lift_plan": Color(0.85, 0.25, 0.2), "permit": Color(0.3, 0.75, 0.4), "test": Color(0.6, 0.4, 0.85),
    "shoring": Color(0.6, 0.42, 0.25), "crane": Color(0.98, 0.85, 0.2),
}

static var _cache: Dictionary = {}
var _accent: Color = Color.WHITE


## Mesh for a marker id (cached). `accent` overrides the default colour (e.g. from the manifest).
static func mesh_for(marker_id: String, accent: Color = Color(0, 0, 0, 0)) -> ArrayMesh:
    var key: String = "%s|%s" % [marker_id, accent.to_html()]
    if _cache.has(key):
        return _cache[key]
    var b := MarkerKit.new()
    var mesh: ArrayMesh = b.build({"marker": marker_id, "accent": accent, "footprint_cells": Vector2i(1, 1), "cell_size_m": 6.0, "height_m": 14.0})
    _cache[key] = mesh
    return mesh


## Mesh scaled and centred to sit on BimView's marker nodes (world units: about `height` x `width` grid
## cells, origin at the mesh centre). Assign `MarkerKit.world_mesh` as BimView.marker_mesh_provider.
static func world_mesh(marker_id: String, height: float = 0.4, width: float = 0.3, accent: Color = Color(0, 0, 0, 0)) -> ArrayMesh:
    var key: String = "world|%s|%.2f|%.2f|%s" % [marker_id, height, width, accent.to_html()]
    if _cache.has(key):
        return _cache[key]
    var b := MarkerKit.new()
    var mesh: ArrayMesh = b.build({"marker": marker_id, "accent": accent, "fit": Vector2(height, width), "footprint_cells": Vector2i(1, 1), "cell_size_m": 6.0, "height_m": 14.0})
    _cache[key] = mesh
    return mesh


func _build() -> void:
    pass


## KitBuilder.build reads the marker id from params, so override the entry point.
func build(params: Dictionary) -> ArrayMesh:
    _configure(params)
    var id: String = str(params.get("marker", ""))
    var acc: Color = params.get("accent", Color(0, 0, 0, 0))
    _accent = acc if acc.a > 0.0 else (DEFAULT_COLOURS.get(id, Color(0.9, 0.5, 0.1)) as Color)
    var fit: Vector2 = params.get("fit", Vector2.ZERO)
    _reset()
    _draw(id)
    if fit != Vector2.ZERO:
        var bb: AABB = _finish().get_aabb()
        var s: float = minf(fit.x / maxf(bb.size.y, 0.01), fit.y / maxf(maxf(bb.size.x, bb.size.z), 0.01))
        _reset()
        push_xf(Transform3D(Basis.from_scale(Vector3.ONE * s), -bb.get_center() * s))
        _draw(id)
        pop_xf()
    return _finish()


func _draw(id: String) -> void:
    _fill = 1.0
    _solid = true
    match id:
        "survey":
            _survey()
        "dewatering":
            _dewatering()
        "scaffold":
            _scaffold()
        "lift_plan":
            _lift_plan()
        "permit":
            _permit()
        "test":
            _test()
        "shoring":
            _shoring()
        "crane":
            _crane()
        _:
            box(Vector3(0, 0.5, 0), Vector3(1, 1, 1), _accent)


func _survey() -> void:
    var top := Vector3(0, 1.5, 0)
    for i in 3:
        var a: float = TAU * float(i) / 3.0 + 0.3
        bar(top, Vector3(cos(a) * 0.7, 0, sin(a) * 0.7), 0.05, 0.05, STEEL_DARK)
    box(Vector3(0, 1.6, 0), Vector3(0.3, 0.2, 0.3), _accent)
    cyl(Vector3(0, 1.7, 0), Vector3(0.18, 1.85, 0), 0.07, 0.07, STEEL_LIGHT, 8, 3)
    # ranging pole with red / white bands and a flag
    for i in 4:
        cyl(Vector3(1.0, 0.45 * float(i), 0.3), Vector3(1.0, 0.45 * float(i + 1), 0.3), 0.035, 0.035, PAINT_RED if i % 2 == 0 else WHITE, 6, 2)
    box_mm(Vector3(1.0, 1.4, 0.3), Vector3(1.4, 1.75, 0.32), _accent)


func _dewatering() -> void:
    box(Vector3(0, 0.35, 0), Vector3(1.1, 0.7, 0.8), _accent)
    cyl(Vector3(0.1, 0.7, 0), Vector3(0.1, 0.95, 0), 0.12, 0.12, STEEL_DARK, 8, 3)
    # sump drum and hoses
    vcyl(Vector3(-1.6, 0, 0.4), 0.9, 0.45, STEEL, 10)
    pipe([Vector3(-1.3, 0.8, 0.4), Vector3(-1.3, 1.1, 0.2), Vector3(-0.5, 0.5, 0.0)], 0.07, STEEL_DARK)
    pipe([Vector3(0.55, 0.3, 0.0), Vector3(1.4, 0.15, 0.5), Vector3(2.4, 0.1, 0.5)], 0.1, _accent)
    for i in 3:
        cone(Vector3(2.5 + 0.15 * float(i), 0.1 + 0.25 * float(i % 2), 0.5), 0.2, 0.06, PIPE_BLUE, 6)


func _scaffold() -> void:
    var bays: int = 2
    var w: float = 1.4
    var d: float = 1.0
    var h: float = 4.0
    for ix in range(bays + 1):
        for iz in 2:
            cyl(Vector3(float(ix) * w - w, 0, float(iz) * d - d * 0.5), Vector3(float(ix) * w - w, h, float(iz) * d - d * 0.5), 0.04, 0.04, _accent, 6, 3)
    for lift in 3:
        var y: float = 0.1 + float(lift) * 1.3 + 0.7
        if lift > 0:
            box_mm(Vector3(-w, y, -d * 0.5), Vector3(w, y + 0.05, d * 0.5), BEIGE)
        for iz in 2:
            bar(Vector3(-w, y + 0.45, float(iz) * d - d * 0.5), Vector3(w, y + 0.45, float(iz) * d - d * 0.5), 0.05, 0.05, STEEL_LIGHT)
    for ix in range(bays):
        bar(Vector3(float(ix) * w - w, 0.2, d * 0.5), Vector3(float(ix + 1) * w - w, h - 0.6, d * 0.5), 0.04, 0.04, STEEL_DARK)


func _lift_plan() -> void:
    # gin pole with a boom, hook block and a red load tag
    cyl(Vector3(0, 0, 0), Vector3(0, 3.2, 0), 0.1, 0.07, STEEL_LIGHT, 8, 3)
    bar(Vector3(0, 3.1, 0), Vector3(1.8, 2.5, 0), 0.1, 0.1, _accent)
    pipe([Vector3(1.8, 2.5, 0), Vector3(1.8, 1.4, 0)], 0.025, STEEL_DARK, 1.0, 5)
    box(Vector3(1.8, 1.25, 0), Vector3(0.2, 0.25, 0.2), STEEL_DARK)
    box(Vector3(1.8, 0.55, 0), Vector3(0.9, 0.9, 0.9), STEEL)
    box_mm(Vector3(-0.9, 0.6, -0.04), Vector3(-0.2, 1.3, 0.04), _accent)


func _permit() -> void:
    cyl(Vector3(0, 0, 0), Vector3(0, 2.0, 0), 0.05, 0.05, STEEL_DARK, 6, 3)
    box_mm(Vector3(-0.5, 1.2, -0.04), Vector3(0.5, 2.0, 0.04), WHITE)
    box_mm(Vector3(-0.5, 1.7, -0.05), Vector3(0.5, 2.0, 0.05), _accent)
    for i in 3:
        box_mm(Vector3(-0.35, 1.5 - 0.12 * float(i), -0.05), Vector3(0.35, 1.55 - 0.12 * float(i), 0.05), STEEL_LIGHT)
    box_mm(Vector3(-0.3, 0.0, -0.3), Vector3(0.3, 0.1, 0.3), CONCRETE_DARK)


func _test() -> void:
    box_mm(Vector3(-0.6, 0, -0.4), Vector3(0.6, 0.1, 0.4), STEEL_DARK)
    cyl(Vector3(0, 0.1, 0), Vector3(0, 1.2, 0), 0.05, 0.05, STEEL, 6, 3)
    cyl(Vector3(0, 1.15, -0.1), Vector3(0, 1.15, 0.12), 0.32, 0.32, STEEL_LIGHT, 12, 3)
    cyl(Vector3(0, 1.15, 0.12), Vector3(0, 1.15, 0.15), 0.26, 0.26, _accent, 12, 3)
    bar(Vector3(0, 1.15, 0.16), Vector3(0.15, 1.28, 0.16), 0.03, 0.03, PAINT_RED)
    pipe([Vector3(0, 0.3, 0), Vector3(0.8, 0.3, 0), Vector3(0.8, 0.8, 0.0)], 0.05, PIPE_BLUE)
    box(Vector3(0.8, 0.9, 0), Vector3(0.25, 0.2, 0.25), _accent)


func _shoring() -> void:
    box_mm(Vector3(-2.0, 0, -1.2), Vector3(-1.8, 2.6, 1.2), CONCRETE_DARK)
    box_mm(Vector3(1.8, 0, -1.2), Vector3(2.0, 2.6, 1.2), CONCRETE_DARK)
    for iz in 3:
        var z: float = -0.8 + 0.8 * float(iz)
        for y: float in [0.6, 1.7]:
            cyl(Vector3(-1.8, y, z), Vector3(1.8, y, z), 0.07, 0.07, _accent, 6, 3)
    for y2: float in [0.6, 1.7]:
        box_mm(Vector3(-1.85, y2 - 0.1, -1.1), Vector3(-1.75, y2 + 0.1, 1.1), STEEL_DARK)
        box_mm(Vector3(1.75, y2 - 0.1, -1.1), Vector3(1.85, y2 + 0.1, 1.1), STEEL_DARK)
    bar(Vector3(-1.8, 0.6, -0.8), Vector3(1.8, 1.7, -0.8), 0.06, 0.06, STEEL_DARK)


func _crane() -> void:
    var mast_h: float = 11.0
    box_mm(Vector3(-0.9, 0, -0.9), Vector3(0.9, 0.3, 0.9), CONCRETE_DARK)
    # lattice mast: four corner posts and a few cross bars
    for sx: float in [-1.0, 1.0]:
        for sz: float in [-1.0, 1.0]:
            bar(Vector3(sx * 0.4, 0.3, sz * 0.4), Vector3(sx * 0.3, mast_h, sz * 0.3), 0.1, 0.1, _accent)
    for k in 5:
        var y: float = 1.5 + float(k) * 2.0
        bar(Vector3(-0.38, y, -0.38), Vector3(0.38, y, 0.38), 0.05, 0.05, STEEL_DARK)
    box_mm(Vector3(-0.5, mast_h, -0.5), Vector3(0.5, mast_h + 0.7, 0.5), STEEL_DARK)
    # jib and counter-jib
    bar(Vector3(-1.2, mast_h + 0.9, 0), Vector3(2.9, mast_h + 0.9, 0), 0.2, 0.35, _accent)
    bar(Vector3(-1.9, mast_h + 0.9, 0), Vector3(-1.2, mast_h + 0.9, 0), 0.2, 0.3, _accent)
    box_mm(Vector3(-2.4, mast_h + 0.6, -0.4), Vector3(-1.6, mast_h + 1.2, 0.4), CONCRETE_DARK)
    cone(Vector3(0, mast_h + 0.7, 0), 1.0, 0.2, STEEL_LIGHT, 4)
    bar(Vector3(0, mast_h + 1.7, 0), Vector3(2.8, mast_h + 1.0, 0), 0.04, 0.04, STEEL_LIGHT)
    pipe([Vector3(2.2, mast_h + 0.75, 0), Vector3(2.2, mast_h - 3.0, 0)], 0.025, STEEL_DARK, 1.0, 5)
    box(Vector3(2.2, mast_h - 3.2, 0), Vector3(0.3, 0.35, 0.3), PAINT_RED)
