class_name EhouseKit
extends KitBuilder
## Prefabricated electrical house: foundation stubs with a steel skid, the box (grows in height) with roof slab and
## doors, cable entry plates and a ladder rack, rooftop HVAC units.
## Layers: foundation (count of stubs), building (grow height), doors, electrical (count), hvac (count).


func _build() -> void:
    orient_long()
    var bw: float = lx * 0.82
    var bd: float = lz * 0.62
    var stub_h: float = minf(0.6, height_m * 0.12)
    var skid_t: float = 0.25
    var roof_t: float = 0.25
    var by0: float = stub_h + skid_t
    var wh: float = height_m - by0 - roof_t - 0.5
    if begin_layer("foundation"):
        var n: int = 6
        for i in n:
            set_part(i, n)
            var x: float = lerpf(-bw * 0.45, bw * 0.45, float(i % 3) / 2.0)
            var z: float = bd * 0.4 * (-1.0 if i < 3 else 1.0)
            box(Vector3(x, stub_h * 0.5, z), Vector3(0.7, stub_h, 0.7), CONCRETE)
        set_part(0, 1)
        for side: float in [-1.0, 1.0]:
            ibeam(Vector3(-bw * 0.5, stub_h + skid_t * 0.5, side * bd * 0.4), Vector3(bw * 0.5, stub_h + skid_t * 0.5, side * bd * 0.4), skid_t, 0.3, STEEL_DARK, true)
    if begin_layer("building"):
        box_grow(Vector3(-bw * 0.5, by0, -bd * 0.5), Vector3(bw * 0.5, by0 + wh, bd * 0.5), layer_fill(), 1, WHITE)
        # roof slab with a drip edge, only once the walls are up
        set_solid(layer_fill() >= 1.0 - EPS)
        box_mm(Vector3(-bw * 0.5 - 0.15, by0 + wh, -bd * 0.5 - 0.15), Vector3(bw * 0.5 + 0.15, by0 + wh + roof_t, bd * 0.5 + 0.15), STEEL_LIGHT)
    if begin_layer("doors"):
        for i in 2:
            set_part(i, 2)
            var x2: float = lerpf(-bw * 0.3, bw * 0.3, float(i))
            var dh: float = minf(2.2, wh * 0.8)
            box_mm(Vector3(x2 - 0.55, by0, bd * 0.5), Vector3(x2 + 0.55, by0 + dh, bd * 0.5 + 0.07), PIPE_BLUE)
            box_mm(Vector3(x2 - 0.7, by0 - 0.15, bd * 0.5), Vector3(x2 + 0.7, by0, bd * 0.5 + 0.7), STEEL_LIGHT)  # landing
    if begin_layer("electrical"):
        var n2: int = 4
        for i in n2:
            set_part(i, n2)
            var x3: float = lerpf(-bw * 0.35, bw * 0.35, float(i) / float(n2 - 1))
            # cable entry plate under the floor and the cable bundle rising to it
            box_mm(Vector3(x3 - 0.3, stub_h + 0.02, -bd * 0.25), Vector3(x3 + 0.3, stub_h + skid_t, -bd * 0.25 + 0.5), ELEC_YELLOW)
            cyl(Vector3(x3, 0.0, -bd * 0.25 + 0.25), Vector3(x3, stub_h + 0.02, -bd * 0.25 + 0.25), 0.12, 0.12, DARK, 6, 2)
        # ladder rack along the side wall
        set_part(n2 - 1, n2)
        box_mm(Vector3(-bw * 0.4, by0 + wh * 0.8, -bd * 0.5 - 0.35), Vector3(bw * 0.4, by0 + wh * 0.8 + 0.05, -bd * 0.5), ELEC_YELLOW)
    if begin_layer("hvac"):
        var top: float = by0 + wh + roof_t
        for i in 2:
            set_part(i, 2)
            var x4: float = lerpf(-bw * 0.22, bw * 0.22, float(i))
            var uh: float = minf(0.5, height_m - top - 0.02)
            if uh > 0.1:
                box_mm(Vector3(x4 - 0.7, top, -0.5), Vector3(x4 + 0.7, top + uh, 0.5), PIPE_BLUE)
                cyl(Vector3(x4, top + uh, 0), Vector3(x4, top + uh + 0.02, 0), 0.4, 0.4, STEEL_DARK, 8, 3)
    end_orient()
