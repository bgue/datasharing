class_name PumpKit
extends KitBuilder
## Pump on a concrete plinth: plinth, baseplate + volute casing, motor with fan cowl, suction / discharge pipes.
## Layers: plinth (grow height), pump, motor, piping.


func _build() -> void:
    orient_long()
    var ln: float = eq_len(3.2)
    var wd: float = eq_wid(1.6)
    var ph: float = height_m * 0.2
    var bh: float = height_m * 0.07
    var rr: float = minf(height_m * 0.28, wd * 0.35)
    var ya: float = ph + bh + rr
    if begin_layer("plinth"):
        box_grow(Vector3(-ln * 0.5 - 0.1, 0, -wd * 0.5), Vector3(ln * 0.5 + 0.1, ph, wd * 0.5), layer_fill(), 1, CONCRETE)
    if begin_layer("pump"):
        set_part(0, 2)
        box_mm(Vector3(-ln * 0.46, ph, -wd * 0.4), Vector3(ln * 0.46, ph + bh, wd * 0.4), STEEL_DARK)
        set_part(1, 2)
        # volute: disc with its axis along the shaft, plus the casing nose
        cyl(Vector3(-ln * 0.2, ya, -rr * 0.5), Vector3(-ln * 0.2, ya, rr * 0.5), rr, rr, PIPE_BLUE, 10, 3)
        cyl(Vector3(-ln * 0.2 - rr * 0.7, ya, 0), Vector3(-ln * 0.2, ya, 0), rr * 0.35, rr * 0.35, PIPE_BLUE, 8, 3)
    if begin_layer("motor"):
        set_part(0, 3)
        cyl(Vector3(ln * 0.0, ya, 0), Vector3(ln * 0.42, ya, 0), rr * 0.95, rr * 0.95, ELEC_YELLOW, 10, 3)
        set_part(1, 3)
        cyl(Vector3(ln * 0.42, ya, 0), Vector3(ln * 0.5, ya, 0), rr * 0.95, rr * 0.6, STEEL_DARK, 10, 3)
        set_part(2, 3)
        box(Vector3(ln * 0.2, ya + rr * 0.95 + 0.05, 0), Vector3(rr * 0.5, 0.1, rr * 0.5), STEEL_DARK)
    if begin_layer("piping"):
        var yt: float = minf(height_m * 0.95, ya + rr * 1.6)
        set_part(0, 2)
        pipe([Vector3(-ln * 0.5, ya, 0), Vector3(-ln * 0.2 - rr * 0.7, ya, 0)], rr * 0.3, PIPE_BLUE, -1.0, 8)
        set_part(1, 2)
        pipe([Vector3(-ln * 0.2, ya + rr * 0.6, 0), Vector3(-ln * 0.2, yt, 0), Vector3(-ln * 0.2 + ln * 0.25, yt, 0)], rr * 0.2, PIPE_ORANGE, -1.0, 8)
    end_orient()
