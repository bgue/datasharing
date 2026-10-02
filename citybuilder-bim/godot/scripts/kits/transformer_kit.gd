class_name TransformerKit
extends KitBuilder
## Power transformer: plinth with bund, tank + conservator, radiator banks, HV bushings, firewall.
## Layers: plinth (grow height), tank, radiators (count), bushings (count), firewall (grow length).


func _build() -> void:
    var ph: float = height_m * 0.08
    var tl: float = minf(width_m * 0.55, height_m * 1.1)
    var tw: float = minf(depth_m * 0.3, height_m * 0.55)
    var th: float = height_m * 0.42
    var y0: float = ph
    if begin_layer("plinth"):
        box_grow(Vector3(-tl * 0.5 - 0.5, 0, -tw * 0.5 - 0.5), Vector3(tl * 0.5 + 0.5, ph, tw * 0.5 + 0.5 + tl * 0.25), layer_fill(), 1, CONCRETE)
    if begin_layer("tank"):
        set_part(0, 2)
        box_mm(Vector3(-tl * 0.5, y0, -tw * 0.5), Vector3(tl * 0.5, y0 + th, tw * 0.5), PIPE_GREEN)
        box_mm(Vector3(-tl * 0.5 - 0.05, y0 + th, -tw * 0.5 - 0.05), Vector3(tl * 0.5 + 0.05, y0 + th + 0.08, tw * 0.5 + 0.05), STEEL_DARK)
        set_part(1, 2)
        cyl(Vector3(-tl * 0.4, y0 + th + 0.35, -tw * 0.1), Vector3(tl * 0.15, y0 + th + 0.35, -tw * 0.1), 0.28, 0.28, PIPE_GREEN, 8, 3)
    if begin_layer("radiators"):
        var n: int = 4
        var rw: float = tl * 0.18
        for i in n:
            set_part(i, n)
            var x: float = lerpf(-tl * 0.38, tl * 0.38, float(i) / float(n - 1))
            box_mm(Vector3(x - rw * 0.5, y0 + th * 0.08, tw * 0.5 + 0.15), Vector3(x + rw * 0.5, y0 + th * 0.95, tw * 0.5 + 0.45), STEEL_LIGHT)
            box_mm(Vector3(x - 0.04, y0 + th * 0.08, tw * 0.5), Vector3(x + 0.04, y0 + th * 0.08 + 0.1, tw * 0.5 + 0.15), STEEL_DARK)
    if begin_layer("bushings"):
        for i in 3:
            set_part(i, 3)
            var x: float = lerpf(-tl * 0.3, tl * 0.3, float(i) / 2.0)
            var by: float = y0 + th + 0.08
            var bh: float = minf(height_m * 0.3, height_m - by)
            cyl(Vector3(x, by, tw * 0.15), Vector3(x, by + bh * 0.75, tw * 0.15), 0.1, 0.07, BRICK, 8, 3)
            cone(Vector3(x, by + bh * 0.75, tw * 0.15), bh * 0.25, 0.1, STEEL_DARK, 8)
    if begin_layer("firewall"):
        var fz: float = -depth_m * 0.5 + 0.3
        box_grow(Vector3(-width_m * 0.45, 0, fz - 0.15), Vector3(width_m * 0.45, height_m * 0.85, fz + 0.15), layer_fill(), 0, CONCRETE_DARK)
