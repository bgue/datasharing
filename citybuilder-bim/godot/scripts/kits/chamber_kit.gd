class_name ChamberKit
extends KitBuilder
## Manhole / pull chamber: base slab, concrete shaft ring (grows in height), benching dish, pipe stubs and the iron cover.
## Layers: base, shaft (grow height), benching, cover.


func _build() -> void:
    var s: float = minf(width_m, depth_m)
    var r: float = clampf(s * 0.16, 0.5, 1.2)
    var base_h: float = 0.25
    var shaft_h: float = height_m - base_h - 0.15
    if begin_layer("base"):
        cyl(Vector3.ZERO, Vector3(0, base_h, 0), r * 1.5, r * 1.5, CONCRETE_DARK, 12, 3)
    if begin_layer("shaft"):
        var courses: int = 3
        var ch: float = shaft_h / float(courses)
        for i in courses:
            var y0: float = base_h + ch * float(i)
            var pos0 := Vector3(0, y0, 0)
            # a tube: outer and inner wall, so the open top shows the inside
            var f: float = part_fill(i, courses)
            var keep: bool = _solid
            set_solid(f >= 1.0 - EPS)
            tube(pos0, Vector3(0, y0 + ch, 0), r, r * 0.78, CONCRETE if i % 2 == 0 else shade(CONCRETE, 0.92), 12)
            if f > EPS and f < 1.0 - EPS:
                set_solid(true)
                tube(pos0, Vector3(0, y0 + ch * f, 0), r, r * 0.78, CONCRETE, 12)
            set_solid(keep)
    if begin_layer("benching"):
        set_part(0, 2)
        cyl(Vector3(0, base_h, 0), Vector3(0, base_h + 0.35, 0), r * 0.76, r * 0.5, CONCRETE_DARK, 10, 3)
        set_part(1, 2)
        for side: float in [-1.0, 1.0]:
            cyl(Vector3(side * (r - 0.05), base_h + 0.5, 0), Vector3(side * (r * 1.45), base_h + 0.5, 0), 0.18, 0.18, PIPE_ORANGE, 8, 3)
    if begin_layer("cover"):
        var top: float = base_h + shaft_h
        cyl(Vector3(0, top, 0), Vector3(0, top + 0.1, 0), r * 1.12, r * 1.12, STEEL_DARK, 12, 3)
        cyl(Vector3(0, top + 0.1, 0), Vector3(0, top + 0.15, 0), r * 0.85, r * 0.85, STEEL, 12, 3)
