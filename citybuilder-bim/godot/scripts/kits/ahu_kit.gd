class_name AhuKit
extends KitBuilder
## Air handling unit: base frame, four box sections (intake, filter, coil, fan) with access doors, supply /
## return ducts, control panel.
## Layers: base, sections (count), ducts (count), controls.


func _build() -> void:
    orient_long()
    var ln: float = eq_len(2.0)
    var wd: float = eq_wid(1.0)
    var bh: float = height_m * 0.1
    var uh: float = height_m * 0.6
    var gap: float = 0.05
    var secs: int = 4
    var lens: Array[float] = [0.18, 0.2, 0.27, 0.35]
    if begin_layer("base"):
        box_mm(Vector3(-ln * 0.5, 0, -wd * 0.5), Vector3(ln * 0.5, bh, wd * 0.5), STEEL_DARK)
    var x: float = -ln * 0.5
    var xs: Array[float] = []
    if begin_layer("sections"):
        for i in secs:
            set_part(i, secs)
            var sl: float = ln * lens[i] - gap
            var c: Color = WHITE if i != 3 else shade(WHITE, 0.92)
            box_mm(Vector3(x, bh, -wd * 0.5 + 0.02), Vector3(x + sl, bh + uh, wd * 0.5 - 0.02), c)
            # access door panel on the +z face
            box_mm(Vector3(x + sl * 0.15, bh + uh * 0.1, wd * 0.5 - 0.02), Vector3(x + sl * 0.85, bh + uh * 0.8, wd * 0.5 + 0.03), PIPE_BLUE)
            if i == 0:
                for k in 3:
                    var ly: float = bh + uh * (0.25 + 0.2 * float(k))
                    box_mm(Vector3(x - 0.05, ly, -wd * 0.3), Vector3(x, ly + 0.08, wd * 0.3), STEEL_DARK)
            xs.push_back(x)
            x += sl + gap
    if begin_layer("ducts"):
        var rx: float = ln * 0.5 - ln * 0.17
        var dw: float = minf(wd * 0.4, 0.7)
        set_part(0, 3)
        box_mm(Vector3(rx - dw * 0.5, bh + uh, -dw * 0.5), Vector3(rx + dw * 0.5, height_m * 0.97, dw * 0.5), STEEL_LIGHT)
        set_part(1, 3)
        box_mm(Vector3(rx - dw * 0.5, height_m * 0.97 - dw * 0.8, 0), Vector3(rx + dw * 0.5, height_m * 0.97, depth_span() * 0.5 - 0.05), STEEL_LIGHT)
        set_part(2, 3)
        var sx: float = -ln * 0.5 + ln * 0.3
        box_mm(Vector3(sx - dw * 0.5, bh + 0.1, -depth_span() * 0.5 + 0.05), Vector3(sx + dw * 0.5, bh + 0.1 + dw * 0.8, -wd * 0.5), shade(STEEL_LIGHT, 0.85))
    if begin_layer("controls"):
        box_mm(Vector3(ln * 0.5 - 0.5, bh + 0.4, wd * 0.5), Vector3(ln * 0.5 - 0.1, bh + 1.0, wd * 0.5 + 0.2), ELEC_YELLOW)
        box_mm(Vector3(ln * 0.5 - 0.35, bh + 1.0, wd * 0.5 + 0.05), Vector3(ln * 0.5 - 0.25, bh + 1.3, wd * 0.5 + 0.12), INSTR_PURPLE)
    end_orient()


## Footprint extent across the long axis (z after orient_long).
func depth_span() -> float:
    return lz
