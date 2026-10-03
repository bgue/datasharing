class_name BusGantryKit
extends KitBuilder
## Bus gantry: a lattice portal per cell boundary along the long axis, insulator strings hanging from the beam and
## three bus tubes spanning the portals.
## Layers: foundation, portals (count), insulators (count), bus (grows along its length).


func _build() -> void:
    orient_long()
    var n: int = maxi(footprint.x, footprint.y) + 1
    var span: float = lz * 0.7
    var x0: float = -lx * 0.5 + 0.4
    var x1: float = lx * 0.5 - 0.4
    var top: float = height_m * 0.92
    var hang: float = minf(1.4, height_m * 0.15)
    if begin_layer("foundation"):
        for i in n:
            for side: float in [-1.0, 1.0]:
                box(Vector3(_px(i, n, x0, x1), 0.2, side * span * 0.5), Vector3(0.8, 0.4, 0.8), CONCRETE)
    if begin_layer("portals"):
        for i in n:
            set_part(i, n)
            var x: float = _px(i, n, x0, x1)
            for side: float in [-1.0, 1.0]:
                # lattice leg: two chords and cross braces
                bar(Vector3(x - 0.15, 0.4, side * span * 0.5), Vector3(x - 0.1, top, side * span * 0.5), 0.1, 0.1, STEEL)
                bar(Vector3(x + 0.15, 0.4, side * span * 0.5), Vector3(x + 0.1, top, side * span * 0.5), 0.1, 0.1, STEEL)
                var steps: int = 5
                for k in steps:
                    var ya: float = 0.4 + (top - 0.4) * float(k) / float(steps)
                    var yb: float = 0.4 + (top - 0.4) * float(k + 1) / float(steps)
                    bar(Vector3(x - 0.15, ya, side * span * 0.5), Vector3(x + 0.15, yb, side * span * 0.5), 0.05, 0.05, STEEL_DARK)
            bar(Vector3(x, top, -span * 0.5), Vector3(x, top, span * 0.5), 0.2, 0.3, STEEL_LIGHT)
    if begin_layer("insulators"):
        for i in n:
            set_part(i, n)
            var x2: float = _px(i, n, x0, x1)
            for ph in 3:
                var z: float = lerpf(-span * 0.35, span * 0.35, float(ph) / 2.0)
                cyl(Vector3(x2, top, z), Vector3(x2, top - hang, z), 0.1, 0.1, BEIGE, 6, 3)
                for k in 3:
                    var yy: float = top - hang * (0.15 + 0.28 * float(k))
                    cyl(Vector3(x2, yy, z), Vector3(x2, yy - 0.05, z), 0.17, 0.17, BEIGE, 6, 3)
    if begin_layer("bus"):
        for ph in 3:
            var z2: float = lerpf(-span * 0.35, span * 0.35, float(ph) / 2.0)
            cyl_grow(Vector3(x0, top - hang, z2), Vector3(x1, top - hang, z2), 0.08, 0.08, ELEC_YELLOW, layer_fill(), 6, 3)
    end_orient()


static func _px(i: int, n: int, x0: float, x1: float) -> float:
    return lerpf(x0, x1, float(i) / float(maxi(1, n - 1)))
