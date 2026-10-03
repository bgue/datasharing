class_name ZoneData
extends RefCounted
## A named set of cells on one storey: the unit of work release and congestion.

var id: String = ""
var name: String = ""
var storey_id: String = ""
var cells: Array[Vector2i] = []
var max_crews: int = 1
var tags: Array[String] = []
## Per-face crew cap (face -> int); a missing face has no cap.
var faces: Dictionary = {}
var shift_allowed: bool = true


static func cell_from_variant(v: Variant) -> Vector2i:
    var a: Array = v
    return Vector2i(int(a[0]), int(a[1]))


static func cells_from_variant(v: Variant) -> Array[Vector2i]:
    var out: Array[Vector2i] = []
    if v is Array:
        var src: Array = v
        out.resize(src.size())
        var i: int = 0
        for c in src:
            var a: Array = c
            out[i] = Vector2i(int(a[0]), int(a[1]))
            i += 1
    return out


## Cells of `rects`: [[x, z, width, depth], ...] (the compact form of `cells`; `cell_rects` in bundle rows).
static func cells_from_rects(rects: Variant) -> Array[Vector2i]:
    var out: Array[Vector2i] = []
    if rects is Array:
        for r in rects:
            var a: Array = r
            var x0: int = int(a[0])
            var z0: int = int(a[1])
            var w: int = int(a[2])
            var d: int = int(a[3])
            for dz in d:
                for dx in w:
                    out.append(Vector2i(x0 + dx, z0 + dz))
    return out


## `cells` (list of [x, z]) or, when the row has no `cells`, `cell_rects` of a bundle row.
static func cells_of_row(d: Dictionary) -> Array[Vector2i]:
    if d.has("cells"):
        return cells_from_variant(d["cells"])
    if d.has("cell_rects"):
        return cells_from_rects(d["cell_rects"])
    return [] as Array[Vector2i]


static func from_dict(d: Dictionary) -> ZoneData:
    var z := ZoneData.new()
    z.id = str(d.get("id", ""))
    z.name = str(d.get("name", z.id))
    z.storey_id = str(d.get("storey_id", ""))
    z.cells = cells_of_row(d)
    z.max_crews = int(d.get("max_crews", 1))
    var tg: Variant = d.get("tags", [])
    if tg is Array:
        for t in tg:
            z.tags.append(str(t))
    var fc: Variant = d.get("faces", {})
    if fc is Dictionary:
        for k in fc:
            z.faces[str(k)] = int(fc[k])
    z.shift_allowed = bool(d.get("shift_allowed", true))
    return z


func has_cell(c: Vector2i) -> bool:
    return cells.has(c)


func centroid() -> Vector2:
    if cells.is_empty():
        return Vector2.ZERO
    var s := Vector2.ZERO
    for c in cells:
        s += Vector2(c)
    return s / float(cells.size())


## The zone cell closest to the centroid (a cell that belongs to the zone), (0, 0) for an empty zone.
func centre_cell() -> Vector2i:
    if cells.is_empty():
        return Vector2i.ZERO
    var c: Vector2 = centroid()
    var best: Vector2i = cells[0]
    var best_d: float = INF
    for cell in cells:
        var d: float = Vector2(cell).distance_squared_to(c)
        if d < best_d - 0.000001:
            best_d = d
            best = cell
    return best
