class_name ElementData
extends RefCounted
## One BIM element (already voxelised onto the grid by the pipeline).

var guid: String = ""
var ifc_class: String = ""
var name: String = ""
var storey_id: String = ""
var zone_id: String = ""
var system_id: String = ""
var cells: Array[Vector2i] = []
var visual: String = "generic"
var size_hint: Vector3 = Vector3.ZERO  # metres [w, h, d]; ZERO when absent
var has_size_hint: bool = false


static func from_dict(d: Dictionary) -> ElementData:
    var e := ElementData.new()
    e.guid = str(d.get("guid", ""))
    e.ifc_class = str(d.get("ifc_class", ""))
    e.name = str(d.get("name", e.guid))
    e.storey_id = str(d.get("storey_id", ""))
    e.zone_id = str(d.get("zone_id", ""))
    var sys: Variant = d.get("system_id", null)
    e.system_id = "" if sys == null else str(sys)
    e.cells = ZoneData.cells_from_variant(d.get("cells", []))
    e.visual = str(d.get("visual", "generic"))
    var sh: Variant = d.get("size_hint", null)
    if sh is Array and (sh as Array).size() == 3:
        e.size_hint = Vector3(float(sh[0]), float(sh[1]), float(sh[2]))
        e.has_size_hint = true
    return e
