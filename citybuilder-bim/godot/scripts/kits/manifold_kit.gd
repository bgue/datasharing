class_name ManifoldKit
extends KitBuilder
## Pipe manifold: a header on supports (grows along its length), branches with valves and hand wheels, and two small
## pumps on skids at one end.
## Layers: supports, header (grow length), branches (count), pumps (count).


func _build() -> void:
    orient_long()
    var l: float = lx * 0.9
    var hy: float = height_m * 0.4
    var hr: float = clampf(height_m * 0.1, 0.1, 0.3)
    var nb: int = clampi(maxi(footprint.x, footprint.y) * 2 + 2, 3, 8)
    if begin_layer("supports"):
        for i in 4:
            var x: float = lerpf(-l * 0.4, l * 0.4, float(i) / 3.0)
            box(Vector3(x, hy * 0.5 - hr * 0.5, 0), Vector3(0.3, hy - hr, 0.5), CONCRETE)
    if begin_layer("header"):
        cyl_grow(Vector3(-l * 0.5, hy, 0), Vector3(l * 0.5, hy, 0), hr, hr, PIPE_ORANGE, layer_fill(), 10, 3)
        for e: float in [-0.5, 0.5]:
            set_solid(layer_fill() >= 1.0 - EPS)
            flange(Vector3(e * l - (0.07 if e > 0.0 else 0.0), hy, 0), Vector3.RIGHT, hr * 1.5, STEEL_DARK)
    if begin_layer("branches"):
        var reach: float = lz * 0.38
        for i in nb:
            set_part(i, nb)
            var x2: float = lerpf(-l * 0.4, l * 0.1, float(i) / float(nb - 1))
            var side: float = -1.0 if i % 2 == 0 else 1.0
            cyl(Vector3(x2, hy, 0), Vector3(x2, hy, side * reach), hr * 0.6, hr * 0.6, PIPE_ORANGE, 8, 3)
            # valve body and hand wheel
            box(Vector3(x2, hy, side * reach * 0.55), Vector3(0.3, 0.3, 0.3), PIPE_RED)
            cyl(Vector3(x2, hy + 0.15, side * reach * 0.55), Vector3(x2, hy + 0.45, side * reach * 0.55), 0.03, 0.03, STEEL_DARK, 5, 3)
            cyl(Vector3(x2, hy + 0.45, side * reach * 0.55), Vector3(x2, hy + 0.5, side * reach * 0.55), 0.18, 0.18, STEEL_LIGHT, 8, 3)
    if begin_layer("pumps"):
        for i in 2:
            set_part(i, 2)
            var px: float = l * (0.3 + 0.12 * float(i)) - 0.0
            var pz: float = -lz * 0.3
            box_mm(Vector3(px - 0.6, 0, pz - 0.4), Vector3(px + 0.6, 0.15, pz + 0.4), STEEL_DARK)
            cyl(Vector3(px - 0.4, 0.4, pz), Vector3(px + 0.1, 0.4, pz), 0.25, 0.25, PIPE_BLUE, 8, 3)
            cyl(Vector3(px + 0.1, 0.4, pz), Vector3(px + 0.55, 0.4, pz), 0.22, 0.22, ELEC_YELLOW, 8, 3)
            cyl(Vector3(px - 0.4, 0.4, pz), Vector3(px - 0.4, hy, pz), 0.08, 0.08, PIPE_ORANGE, 6, 3)
    end_orient()
