class_name ZoneData
extends RefCounted
## A named set of cells on one storey: the unit of work release and congestion.

var id: String = ""
var name: String = ""
var storey_id: String = ""
var cells: Array[Vector2i] = []
var max_crews: int = 1
var tags: Array[String] = []


static func cell_from_variant(v: Variant) -> Vector2i:
    var a: Array = v
    return Vector2i(int(a[0]), int(a[1]))


static func cells_from_variant(v: Variant) -> Array[Vector2i]:
    var out: Array[Vector2i] = []
    if v is Array:
        for c in v:
            out.append(cell_from_variant(c))
    return out


static func from_dict(d: Dictionary) -> ZoneData:
    var z := ZoneData.new()
    z.id = str(d.get("id", ""))
    z.name = str(d.get("name", z.id))
    z.storey_id = str(d.get("storey_id", ""))
    z.cells = cells_from_variant(d.get("cells", []))
    z.max_crews = int(d.get("max_crews", 1))
    var tg: Variant = d.get("tags", [])
    if tg is Array:
        for t in tg:
            z.tags.append(str(t))
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
