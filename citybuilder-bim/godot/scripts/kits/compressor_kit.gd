class_name CompressorKit
extends KitBuilder
## Compressor package: skid with beams, multi-stage casing, motor, coolers on a frame, suction scrubber + pipes.
## Layers: skid, casing (count), motor, coolers (count), piping.


func _build() -> void:
    orient_long()
    var sk: float = minf(0.4, height_m * 0.08)
    var rc: float = minf(lz * 0.12, height_m * 0.14)
    var ya: float = sk + rc + 0.1
    var l: float = lx * 0.9
    var wd: float = lz * 0.8
    if begin_layer("skid"):
        box_mm(Vector3(-l * 0.5, 0, -wd * 0.4), Vector3(l * 0.5, sk, wd * 0.4), STEEL_DARK)
        for side: float in [-1.0, 1.0]:
            ibeam(Vector3(-l * 0.5, sk * 0.5, side * wd * 0.4), Vector3(l * 0.5, sk * 0.5, side * wd * 0.4), sk, 0.15, STEEL, true)
    if begin_layer("casing"):
        set_part(0, 4)
        cyl(Vector3(-l * 0.1, ya, 0), Vector3(l * 0.15, ya, 0), rc, rc, STEEL_LIGHT, 10, 3)
        for i in 3:
            set_part(i + 1, 4)
            var x: float = -l * 0.06 + float(i) * l * 0.08
            cyl(Vector3(x - 0.2, ya, 0), Vector3(x + 0.2, ya, 0), rc * 1.25, rc * 1.25, PIPE_BLUE, 10, 3)
    if begin_layer("motor"):
        set_part(0, 2)
        cyl(Vector3(l * 0.2, ya, 0), Vector3(l * 0.46, ya, 0), rc * 1.4, rc * 1.4, ELEC_YELLOW, 10, 3)
        set_part(1, 2)
        cyl(Vector3(l * 0.15, ya, 0), Vector3(l * 0.2, ya, 0), rc * 0.4, rc * 0.4, STEEL_DARK, 8, 3)
    if begin_layer("coolers"):
        var cz: float = -wd * 0.3
        var cy: float = minf(height_m - 0.5, ya + rc * 2.2)
        for i in 2:
            set_part(i, 2)
            var x: float = -l * 0.15 + float(i) * l * 0.25
            for px: float in [-0.5, 0.5]:
                box(Vector3(x + px * l * 0.1, (sk + cy) * 0.5, cz), Vector3(0.12, cy - sk, 0.12), STEEL)
            box_mm(Vector3(x - l * 0.1, cy, cz - 0.5), Vector3(x + l * 0.1, cy + 0.4, cz + 0.5), PIPE_BLUE)
    if begin_layer("piping"):
        var sx: float = -l * 0.38
        set_part(0, 2)
        cyl(Vector3(sx, sk, wd * 0.25), Vector3(sx, minf(height_m, sk + height_m * 0.6), wd * 0.25), rc * 0.8, rc * 0.8, STEEL_LIGHT, 10, 3)
        set_part(1, 2)
        pipe([Vector3(sx, sk + height_m * 0.4, wd * 0.25), Vector3(-l * 0.1, ya, wd * 0.15), Vector3(-l * 0.1, ya, rc)], rc * 0.2, PIPE_ORANGE, -1.0, 8)
        pipe([Vector3(l * 0.15, ya + rc, 0.0), Vector3(l * 0.15, ya + rc * 2.0, -wd * 0.3)], rc * 0.15, PIPE_ORANGE, -1.0, 8)
    end_orient()
