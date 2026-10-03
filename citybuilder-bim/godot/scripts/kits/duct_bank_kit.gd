class_name DuctBankKit
extends KitBuilder
## Duct bank (below-ground face, drawn as a cut-away at grade): the open trench with soil berms and a sand bed, the
## conduit array (2 rows of 3), the concrete encasement and the warning tape.
## Layers: trench (grow length), conduits (grow length), encasement (grow length).


func _build() -> void:
    orient_long()
    var tw: float = minf(lz * 0.5, 2.4)
    var l: float = lx * 0.96
    var bed_h: float = 0.12
    var cy: float = bed_h + 0.2
    if begin_layer("trench"):
        var f: float = layer_fill()
        for side: float in [-1.0, 1.0]:
            box_grow(Vector3(-l * 0.5, 0, side * (tw * 0.5 + 0.6) - 0.3), Vector3(l * 0.5, minf(0.55, height_m * 0.5), side * (tw * 0.5 + 0.6) + 0.3), f, 0, BRICK)
        box_grow(Vector3(-l * 0.5, 0, -tw * 0.5), Vector3(l * 0.5, bed_h, tw * 0.5), f, 0, BEIGE)
    if begin_layer("conduits"):
        var f2: float = layer_fill()
        for row in 2:
            for col in 3:
                var z: float = lerpf(-tw * 0.3, tw * 0.3, float(col) / 2.0)
                var y: float = cy + float(row) * 0.3
                cyl_grow(Vector3(-l * 0.5, y, z), Vector3(l * 0.5, y, z), 0.1, 0.1, ELEC_YELLOW if row == 0 else PIPE_ORANGE, f2, 8, 3)
    if begin_layer("encasement"):
        var f3: float = layer_fill()
        box_grow(Vector3(-l * 0.5, bed_h, -tw * 0.45), Vector3(l * 0.5, cy + 0.3 + 0.25, tw * 0.45), f3, 0, CONCRETE)
        box_grow(Vector3(-l * 0.5, cy + 0.55, -tw * 0.45), Vector3(l * 0.5, cy + 0.58, tw * 0.45), f3, 0, PAINT_RED)
    end_orient()
