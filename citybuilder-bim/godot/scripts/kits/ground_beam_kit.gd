class_name GroundBeamKit
extends KitBuilder
## Ground beam: the trench strip, the beam (grows in length) and column starter bars at every cell boundary.
## Layers: trench (grow length), beam (grow length), starters (count).


func _build() -> void:
    orient_long()
    var l: float = lx * 0.96
    var bw: float = clampf(lz * 0.14, 0.3, 0.7)
    var bh: float = minf(0.7, height_m * 0.7)
    if begin_layer("trench"):
        var f: float = layer_fill()
        box_grow(Vector3(-l * 0.5, 0, -bw * 1.4), Vector3(l * 0.5, 0.1, bw * 1.4), f, 0, BRICK)
    if begin_layer("beam"):
        box_grow(Vector3(-l * 0.5, 0.1, -bw * 0.5), Vector3(l * 0.5, 0.1 + bh, bw * 0.5), layer_fill(), 0, CONCRETE)
    if begin_layer("starters"):
        var n: int = maxi(footprint.x, footprint.y) + 1
        for i in n:
            set_part(i, n)
            var x: float = lerpf(-l * 0.5 + 0.2, l * 0.5 - 0.2, float(i) / float(maxi(1, n - 1)))
            for dz: float in [-0.12, 0.12]:
                cyl(Vector3(x, 0.1 + bh, dz * bw * 2.0), Vector3(x, minf(height_m, 0.1 + bh + 0.2), dz * bw * 2.0), 0.03, 0.03, STEEL_DARK, 5, 3)
    end_orient()
