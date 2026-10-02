class_name MriKit
extends KitBuilder
## MRI suite: RF-shielded room walls (grow height, open top for the cut-away look), shield lining,
## magnet (ring housing with bore), patient table.
## Layers: room (grow height), shield (grow height), magnet (count), table.


func _build() -> void:
    var rw: float = width_m * 0.85
    var rd: float = depth_m * 0.8
    var wt: float = 0.2
    var wh: float = height_m * 0.95
    if begin_layer("room"):
        var f: float = layer_fill()
        box_grow(Vector3(-rw * 0.5, 0, -rd * 0.5), Vector3(rw * 0.5, wh, -rd * 0.5 + wt), f, 1, BEIGE)
        box_grow(Vector3(-rw * 0.5, 0, -rd * 0.5 + wt), Vector3(-rw * 0.5 + wt, wh, rd * 0.5), f, 1, BEIGE)
        box_grow(Vector3(rw * 0.5 - wt, 0, -rd * 0.5 + wt), Vector3(rw * 0.5, wh, rd * 0.5), f, 1, BEIGE)
        # front wall with a door opening
        box_grow(Vector3(-rw * 0.5 + wt, 0, rd * 0.5 - wt), Vector3(-0.6, wh, rd * 0.5), f, 1, BEIGE)
        box_grow(Vector3(0.6, 0, rd * 0.5 - wt), Vector3(rw * 0.5 - wt, wh, rd * 0.5), f, 1, BEIGE)
        box_grow(Vector3(-0.6, minf(2.2, wh * 0.75), rd * 0.5 - wt), Vector3(0.6, wh, rd * 0.5), f, 1, BEIGE)
    if begin_layer("shield"):
        var f2: float = layer_fill()
        var t: float = 0.06
        box_grow(Vector3(-rw * 0.5 + wt, 0, -rd * 0.5 + wt), Vector3(rw * 0.5 - wt, wh * 0.98, -rd * 0.5 + wt + t), f2, 1, STEEL_DARK)
        box_grow(Vector3(-rw * 0.5 + wt, 0, -rd * 0.5 + wt + t), Vector3(-rw * 0.5 + wt + t, wh * 0.98, rd * 0.5 - wt), f2, 1, STEEL_DARK)
        box_grow(Vector3(rw * 0.5 - wt - t, 0, -rd * 0.5 + wt + t), Vector3(rw * 0.5 - wt, wh * 0.98, rd * 0.5 - wt), f2, 1, STEEL_DARK)
    var mr: float = minf(height_m * 0.28, rd * 0.22)
    var my: float = mr + 0.25
    if begin_layer("magnet"):
        set_part(0, 2)
        tube(Vector3(-mr * 0.9, my, -rd * 0.1), Vector3(mr * 0.9, my, -rd * 0.1), mr, mr * 0.45, WHITE, 14)
        set_part(1, 2)
        box_mm(Vector3(-mr * 0.9, 0, -rd * 0.1 - mr * 0.7), Vector3(mr * 0.9, my - mr * 0.8, -rd * 0.1 + mr * 0.7), STEEL_LIGHT)
        box_mm(Vector3(-mr * 0.5, my + mr, -rd * 0.1 - mr * 0.3), Vector3(mr * 0.5, my + mr + 0.2, -rd * 0.1 + mr * 0.3), MED_MAGENTA)
    if begin_layer("table"):
        set_part(0, 2)
        box_mm(Vector3(-0.25, my - mr * 0.5, -rd * 0.1 + mr * 1.0), Vector3(0.25, my - mr * 0.5 + 0.1, rd * 0.5 - wt - 0.3), PIPE_BLUE)
        set_part(1, 2)
        box_mm(Vector3(-0.3, 0, rd * 0.5 - wt - 1.3), Vector3(0.3, my - mr * 0.5, rd * 0.5 - wt - 0.7), STEEL_DARK)
