class_name CulvertKit
extends KitBuilder
## Box culvert: bedding strip, one box segment per cell along the long axis (top slab, two walls, invert),
## headwalls with a barrel opening and splayed wing walls.
## Layers: bedding (grow length), segments (count), headwalls (count).


func _build() -> void:
    orient_long()
    var n: int = maxi(footprint.x, footprint.y)
    var head_t: float = 0.4
    var wing: float = minf(1.5, lx * 0.12)
    var run0: float = -lx * 0.5 + wing + head_t
    var run1: float = lx * 0.5 - wing - head_t
    var bw: float = lz * 0.6
    var bh: float = height_m * 0.75
    var wt: float = 0.3
    var inv: float = 0.25
    if begin_layer("bedding"):
        box_grow(Vector3(-lx * 0.5, 0, -bw * 0.5 - 0.5), Vector3(lx * 0.5, 0.15, bw * 0.5 + 0.5), layer_fill(), 0, CONCRETE_DARK)
    if begin_layer("segments"):
        var sl: float = (run1 - run0) / float(n)
        for i in n:
            set_part(i, n)
            var x0: float = run0 + sl * float(i) + 0.03
            var x1: float = run0 + sl * float(i + 1) - 0.03
            var y0: float = 0.15
            box_mm(Vector3(x0, y0, -bw * 0.5), Vector3(x1, y0 + inv, bw * 0.5), CONCRETE)
            box_mm(Vector3(x0, y0 + inv, -bw * 0.5), Vector3(x1, y0 + bh, -bw * 0.5 + wt), CONCRETE)
            box_mm(Vector3(x0, y0 + inv, bw * 0.5 - wt), Vector3(x1, y0 + bh, bw * 0.5), CONCRETE)
            box_mm(Vector3(x0, y0 + bh - inv, -bw * 0.5 + wt), Vector3(x1, y0 + bh, bw * 0.5 - wt), CONCRETE)
    if begin_layer("headwalls"):
        var hh: float = height_m - 0.15
        for e in 2:
            set_part(e, 2)
            var sgn: float = -1.0 if e == 0 else 1.0
            var xa: float = run0 - head_t if e == 0 else run1
            var xb: float = run0 if e == 0 else run1 + head_t
            var hw: float = lz * 0.9
            var open_h: float = 0.15 + bh - inv
            box_mm(Vector3(xa, 0.15, -hw * 0.5), Vector3(xb, hh, -bw * 0.5 + wt), BRICK)
            box_mm(Vector3(xa, 0.15, bw * 0.5 - wt), Vector3(xb, hh, hw * 0.5), BRICK)
            box_mm(Vector3(xa, open_h, -bw * 0.5 + wt), Vector3(xb, hh, bw * 0.5 - wt), BRICK)
            # splayed wing walls
            var xe: float = xa if e == 0 else xb
            for sz: float in [-1.0, 1.0]:
                bar(Vector3(xe, hh * 0.45, sz * hw * 0.5), Vector3(xe + sgn * wing * 0.9, hh * 0.45, sz * (lz * 0.5 - 0.3)), 0.25, hh * 0.7, BRICK)
    end_orient()
