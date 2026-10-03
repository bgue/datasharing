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
## Visual kit id assigned by the pipeline ("" = none), see docs/06 track B; read by the kits code.
var visual_kit: String = ""
## Member element GUIDs when this element is an aggregate (docs/06 B.5); empty for plain elements.
var member_guids: Array[String] = []


static func from_dict(d: Dictionary) -> ElementData:
    var e := ElementData.new()
    e.guid = str(d.get("guid", ""))
    e.ifc_class = str(d.get("ifc_class", ""))
    e.name = str(d.get("name", e.guid))
    e.storey_id = str(d.get("storey_id", ""))
    e.zone_id = str(d.get("zone_id", ""))
    var sys: Variant = d.get("system_id", null)
    e.system_id = "" if sys == null else str(sys)
    e.cells = ZoneData.cells_of_row(d)
    e.visual = str(d.get("visual", "generic"))
    var sh: Variant = d.get("size_hint", null)
    if sh is Array and (sh as Array).size() == 3:
        e.size_hint = Vector3(float(sh[0]), float(sh[1]), float(sh[2]))
        e.has_size_hint = true
    var vk: Variant = d.get("visual_kit", null)
    e.visual_kit = "" if vk == null else str(vk)
    var mg: Variant = d.get("member_guids", null)
    if mg is Array:
        for g in mg:
            e.member_guids.append(str(g))
    return e
