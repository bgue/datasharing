class_name PileCapKit
extends KitBuilder
## Pile group: pile stubs in a grid (appear one by one), the cap (grows in height) standing on them and the anchor
## bolts on top. A lone pile instance draws only its stub.
## Layers: piles (count), cap (grow height), bolts (count).


func _build() -> void:
    var n: int = count_hint("piles", maxi(1, footprint.x * footprint.y * 4), 1, 25)
    var g: int = int(ceil(sqrt(float(n))))
    var aw: float = width_m * 0.7
    var ad: float = depth_m * 0.7
    var pile_r: float = clampf(minf(aw, ad) / float(g) * 0.36, 0.15, 0.5)
    var stub_h: float = height_m * 0.5
    var cap_y0: float = height_m * 0.42
    var cap_y1: float = height_m * 0.8
    if begin_layer("piles"):
        for i in n:
            set_part(i, n)
            var gx: int = i % g
            var gz: int = i / g
            var x: float = (float(gx) + 0.5 - float(g) * 0.5) * (aw / float(g))
            var z: float = (float(gz) + 0.5 - float(g) * 0.5) * (ad / float(g))
            cyl(Vector3(x, 0, z), Vector3(x, stub_h, z), pile_r, pile_r, STEEL_DARK, 8, 3)
    if begin_layer("cap"):
        box_grow(Vector3(-aw * 0.3, cap_y0, -ad * 0.3), Vector3(aw * 0.3, cap_y1, ad * 0.3), layer_fill(), 1, CONCRETE)
    if begin_layer("bolts"):
        var nb: int = 4
        for i in nb:
            set_part(i, nb)
            var x2: float = (-1.0 if i % 2 == 0 else 1.0) * aw * 0.2
            var z2: float = (-1.0 if i < 2 else 1.0) * ad * 0.2
            cyl(Vector3(x2, cap_y1, z2), Vector3(x2, height_m, z2), 0.05, 0.05, STEEL_LIGHT, 6, 3)
            box(Vector3(x2, cap_y1 + 0.03, z2), Vector3(0.3, 0.06, 0.3), STEEL_DARK)
