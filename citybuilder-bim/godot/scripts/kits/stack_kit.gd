class_name StackKit
extends KitBuilder
## Exhaust stack: base block, tapered shaft in painted bands (grows with height), ladder, platform rings.
## Layers: base, shaft (grow height), ladder, platforms.


func _build() -> void:
    var s: float = minf(width_m, depth_m)
    var rb: float = clampf(height_m * 0.03, 0.3, s * 0.3)
    var rt: float = rb * 0.6
    var bh: float = minf(0.5, height_m * 0.03)
    if begin_layer("base"):
        box_mm(Vector3(-rb * 1.8, 0, -rb * 1.8), Vector3(rb * 1.8, bh, rb * 1.8), CONCRETE)
        cyl(Vector3(0, bh, 0), Vector3(0, bh + 0.1, 0), rb * 1.25, rb * 1.25, STEEL_DARK, 10, 2)
    if begin_layer("shaft"):
        var bands: int = 8
        var h: float = height_m - bh
        for i in bands:
            var y0: float = bh + h * float(i) / float(bands)
            var y1: float = bh + h * float(i + 1) / float(bands)
            var r0: float = lerpf(rb, rt, float(i) / float(bands))
            var r1: float = lerpf(rb, rt, float(i + 1) / float(bands))
            var c: Color = STEEL_LIGHT
            if i >= bands - 3:
                c = PAINT_RED if (i % 2 == 0) else WHITE
            cyl_grow(Vector3(0, y0, 0), Vector3(0, y1, 0), r0, r1, c, part_fill(i, bands), 10, 2 if i == bands - 1 else 0)
    if begin_layer("ladder"):
        ladder(Vector3(rb + 0.05, bh, 0), (height_m - bh) * 0.9, STEEL_DARK, 0.0, 0.4)
    if begin_layer("platforms"):
        for k in 3:
            set_part(k, 3)
            var y: float = bh + (height_m - bh) * (0.35 + 0.25 * float(k))
            var r: float = lerpf(rb, rt, (y - bh) / (height_m - bh))
            var ro: float = minf(r + 0.7, s * 0.5 - 0.05)
            ring(Vector3(0, y, 0), r + 0.02, ro, 0.08, STEEL_DARK, 10)
            ring_rail(Vector3(0, y + 0.08, 0), ro - 0.03, STEEL_LIGHT, minf(1.0, height_m - y - 0.08), 8)
