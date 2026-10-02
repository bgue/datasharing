class_name VesselKit
extends KitBuilder
## Process vessel. kit_params.orientation = "vertical" (skirt, shell, top head, platforms, ladder) or
## "horizontal" (plinth + saddles, shell, two heads, nozzles, side platform).
## Layers: skirt (saddles), shell (grow height / length), heads, nozzles, platforms.


func _build() -> void:
    if str(kit_params.get("orientation", "vertical")) == "horizontal":
        _horizontal()
    else:
        _vertical()


func _vertical() -> void:
    var s: float = minf(width_m, depth_m)
    var r: float = maxf(s * 0.2, minf(s * 0.5 - 0.85, s * 0.36))
    var avail: float = maxf(0.1, s * 0.5 - r)
    var skirt_h: float = height_m * 0.1
    var head_h: float = minf(r * 0.5, height_m * 0.18)
    var shell_h: float = height_m - skirt_h - head_h
    if begin_layer("skirt"):
        cyl(Vector3(0, 0, 0), Vector3(0, skirt_h, 0), r * 0.92, r * 0.92, CONCRETE_DARK, 12, 2)
        ring(Vector3.ZERO, r * 0.95, r * 1.12, 0.12, STEEL_DARK, 12)
    if begin_layer("shell"):
        cyl_grow(Vector3(0, skirt_h, 0), Vector3(0, skirt_h + shell_h, 0), r, r, STEEL_LIGHT, layer_fill(), 12, 0)
    if begin_layer("heads"):
        set_part(0, 2)
        dome(Vector3(0, skirt_h + shell_h, 0), Vector3.UP, r, head_h, STEEL, 12, 3)
        set_part(1, 2)
        cyl(Vector3(0, height_m - head_h * 0.3, 0), Vector3(0, height_m, 0), r * 0.12, r * 0.12, STEEL_DARK, 6, 3)
        dome(Vector3(0, skirt_h, 0), Vector3.DOWN, r, minf(head_h, skirt_h), STEEL, 12, 2)
    if begin_layer("nozzles"):
        var reach: float = minf(0.6, avail * 0.7)
        for i in 3:
            set_part(i, 3)
            var ang: float = PI * (0.9 + 0.5 * float(i))
            var d := Vector3(cos(ang), 0, sin(ang))
            var y: float = skirt_h + shell_h * (0.25 + 0.3 * float(i))
            cyl(d * (r - 0.05) + Vector3(0, y, 0), d * (r + reach) + Vector3(0, y, 0), 0.14, 0.14, PIPE_ORANGE, 8, 3)
            flange(d * (r + reach - 0.07) + Vector3(0, y, 0), d, 0.22, STEEL_DARK)
    if begin_layer("platforms"):
        var pw: float = minf(0.8, avail * 0.7)
        set_part(0, 3)
        for k in 2:
            var y: float = skirt_h + shell_h * (0.45 + 0.35 * float(k))
            ring(Vector3(0, y, 0), r + 0.02, r + pw, 0.08, STEEL_DARK, 12)
        set_part(1, 3)
        for k in 2:
            var y2: float = skirt_h + shell_h * (0.45 + 0.35 * float(k)) + 0.08
            ring_rail(Vector3(0, y2, 0), r + pw - 0.03, STEEL_LIGHT, 1.0, 12)
        set_part(2, 3)
        ladder(Vector3(r + 0.02, skirt_h, 0), shell_h * 0.8, STEEL_LIGHT, 0.0, 0.4)


func _horizontal() -> void:
    orient_long()
    var r: float = minf(lz * 0.3, height_m * 0.3)
    var hd: float = r * 0.5
    var shell_l: float = lx * 0.9 - 2.0 * hd
    var hs: float = maxf(0.3, height_m - 2.0 * r - 0.45)
    var yc: float = hs + r
    var avail: float = maxf(0.1, lz * 0.5 - r)
    if begin_layer("skirt"):
        set_part(0, 2)
        box_mm(Vector3(-shell_l * 0.5, 0, -r * 1.0), Vector3(shell_l * 0.5, 0.2, r * 1.0), CONCRETE)
        set_part(1, 2)
        for sx: float in [-0.3, 0.3]:
            box_mm(Vector3(sx * shell_l - 0.2, 0.2, -r * 0.85), Vector3(sx * shell_l + 0.2, yc - r * 0.55, r * 0.85), STEEL_DARK)
            box_mm(Vector3(sx * shell_l - 0.2, yc - r * 0.55, -r * 0.5), Vector3(sx * shell_l + 0.2, yc - r * 0.35, r * 0.5), STEEL_DARK)
    if begin_layer("shell"):
        cyl_grow(Vector3(-shell_l * 0.5, yc, 0), Vector3(shell_l * 0.5, yc, 0), r, r, STEEL_LIGHT, layer_fill(), 12, 0)
    if begin_layer("heads"):
        set_part(0, 2)
        dome(Vector3(-shell_l * 0.5, yc, 0), Vector3.LEFT, r, hd, STEEL, 12, 3)
        set_part(1, 2)
        dome(Vector3(shell_l * 0.5, yc, 0), Vector3.RIGHT, r, hd, STEEL, 12, 3)
    if begin_layer("nozzles"):
        for i in 3:
            set_part(i, 3)
            var x: float = shell_l * (-0.3 + 0.3 * float(i))
            cyl(Vector3(x, yc + r - 0.05, 0), Vector3(x, minf(height_m, yc + r + 0.4), 0), 0.14, 0.14, PIPE_ORANGE, 8, 3)
            flange(Vector3(x, minf(height_m, yc + r + 0.4) - 0.07, 0), Vector3.UP, 0.22, STEEL_DARK)
    if begin_layer("platforms"):
        var pw: float = minf(0.9, avail * 0.8)
        var py: float = yc + r * 0.1
        set_part(0, 2)
        platform(Vector3(-shell_l * 0.15, py, r + 0.02), Vector3(shell_l * 0.15, py + 0.08, r + pw), STEEL_DARK, true)
        set_part(1, 2)
        ladder(Vector3(shell_l * 0.15, 0, r + pw - 0.05), py, STEEL_LIGHT, 0.0, 0.4)
    end_orient()
