class_name PilingRigKit
extends KitBuilder
## Piling rig (equipment visual shown on the active pile cell): crawler tracks, cab and engine, the tall leader with a
## hammer and a pile section.
## Layers: rig (one layer; KitInstances shows the instance only while its tasks are active).


func _build() -> void:
    orient_long()
    var tl: float = minf(lx * 0.7, 4.4)
    var tw: float = minf(lz * 0.55, 3.0)
    var th: float = 0.7
    if begin_layer("rig"):
        set_part(0, 4)
        for side: float in [-1.0, 1.0]:
            box_mm(Vector3(-tl * 0.5, 0, side * tw * 0.5 - 0.35), Vector3(tl * 0.5, th, side * tw * 0.5 + 0.35), STEEL_DARK)
            box_mm(Vector3(-tl * 0.5 + 0.1, th, side * tw * 0.5 - 0.3), Vector3(tl * 0.5 - 0.1, th + 0.05, side * tw * 0.5 + 0.3), STEEL)
        set_part(1, 4)
        box_mm(Vector3(-tl * 0.35, th, -tw * 0.35), Vector3(tl * 0.1, th + 1.0, tw * 0.35), ELEC_YELLOW)  # house
        box_mm(Vector3(-tl * 0.05, th + 0.5, -tw * 0.25), Vector3(tl * 0.1, th + 1.5, tw * 0.1), PIPE_BLUE)  # cab
        box_mm(Vector3(-tl * 0.5, th, -tw * 0.3), Vector3(-tl * 0.35, th + 0.8, tw * 0.3), PIPE_ORANGE)  # counterweight
        var mast_x: float = tl * 0.38
        var top: float = height_m * 0.96
        set_part(2, 4)
        bar(Vector3(mast_x - 0.5, th + 0.8, 0), Vector3(mast_x, top, 0), 0.25, 0.4, STEEL_LIGHT)  # leader
        bar(Vector3(-tl * 0.1, th + 1.0, 0), Vector3(mast_x, top * 0.7, 0), 0.12, 0.12, STEEL)  # backstay
        set_part(3, 4)
        box(Vector3(mast_x - 0.12, top * 0.55, 0), Vector3(0.5, 1.2, 0.5), PIPE_RED)  # hammer
        cyl(Vector3(mast_x - 0.2, th, 0), Vector3(mast_x - 0.2, top * 0.45, 0), 0.18, 0.18, CONCRETE_DARK, 8, 3)  # pile
    end_orient()
