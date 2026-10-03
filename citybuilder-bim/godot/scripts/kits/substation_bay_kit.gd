class_name SubstationBayKit
extends KitBuilder
## Switchyard bay, three phases across the bay: disconnect - CT - breaker - CT - disconnect on steel supports, with
## the A-frame and busbar tap.
## Layers: foundation, steel (count), breaker, disconnects (count), cts (count).


func _build() -> void:
    orient_long()
    var bl: float = lx * 0.8
    var span: float = lz * 0.6
    var post_h: float = height_m * 0.32
    var pad_h: float = 0.25
    if begin_layer("foundation"):
        for ph in 3:
            for k in 5:
                var x: float = lerpf(-bl * 0.45, bl * 0.45, float(k) / 4.0)
                var z: float = lerpf(-span * 0.5, span * 0.5, float(ph) / 2.0)
                box(Vector3(x, pad_h * 0.5, z), Vector3(0.6, pad_h, 0.6), CONCRETE)
    if begin_layer("steel"):
        var n: int = 5
        for k in n:
            set_part(k, n)
            var x2: float = lerpf(-bl * 0.45, bl * 0.45, float(k) / 4.0)
            for ph in 3:
                var z2: float = lerpf(-span * 0.5, span * 0.5, float(ph) / 2.0)
                box_mm(Vector3(x2 - 0.12, pad_h, z2 - 0.12), Vector3(x2 + 0.12, pad_h + post_h, z2 + 0.12), STEEL)
            bar(Vector3(x2, pad_h + post_h * 0.5, -span * 0.5), Vector3(x2, pad_h + post_h * 0.5, span * 0.5), 0.1, 0.1, STEEL_DARK)
        set_part(n - 1, n)
        # A-frame gantry over the bay with the busbar tap
        for side: float in [-1.0, 1.0]:
            bar(Vector3(-bl * 0.45, 0.25, side * span * 0.6), Vector3(-bl * 0.45, height_m * 0.9, side * span * 0.5), 0.2, 0.2, STEEL)
        bar(Vector3(-bl * 0.45, height_m * 0.9, -span * 0.5), Vector3(-bl * 0.45, height_m * 0.9, span * 0.5), 0.2, 0.25, STEEL_LIGHT)
    if begin_layer("breaker"):
        var bx: float = 0.0
        for ph in 3:
            set_part(ph, 3)
            var z3: float = lerpf(-span * 0.5, span * 0.5, float(ph) / 2.0)
            var by: float = pad_h + post_h
            cyl(Vector3(bx - bl * 0.12, by + 0.3, z3), Vector3(bx + bl * 0.12, by + 0.3, z3), 0.3, 0.3, PIPE_GREEN, 10, 3)
            for e: float in [-1.0, 1.0]:
                cyl(Vector3(bx + e * bl * 0.1, by + 0.5, z3), Vector3(bx + e * bl * 0.1, minf(height_m * 0.62, by + 1.9), z3), 0.1, 0.07, BEIGE, 8, 3)
    if begin_layer("disconnects"):
        for i in 6:
            set_part(i, 6)
            var ph2: int = i % 3
            var end: float = -1.0 if i < 3 else 1.0
            var x4: float = end * bl * 0.4
            var z4: float = lerpf(-span * 0.5, span * 0.5, float(ph2) / 2.0)
            var y0: float = pad_h + post_h
            cyl(Vector3(x4, y0, z4), Vector3(x4, y0 + 1.1, z4), 0.09, 0.09, BEIGE, 6, 3)
            bar(Vector3(x4, y0 + 1.1, z4), Vector3(x4 - end * 1.4, y0 + 1.25, z4), 0.07, 0.07, STEEL_LIGHT)
    if begin_layer("cts"):
        for i in 6:
            set_part(i, 6)
            var ph3: int = i % 3
            var end2: float = -1.0 if i < 3 else 1.0
            var x5: float = end2 * bl * 0.22
            var z5: float = lerpf(-span * 0.5, span * 0.5, float(ph3) / 2.0)
            var y1: float = pad_h + post_h
            cyl(Vector3(x5, y1, z5), Vector3(x5, y1 + 0.9, z5), 0.17, 0.17, PIPE_BLUE, 8, 3)
            cyl(Vector3(x5, y1 + 0.9, z5), Vector3(x5, y1 + 1.25, z5), 0.1, 0.1, BEIGE, 8, 3)
    end_orient()
