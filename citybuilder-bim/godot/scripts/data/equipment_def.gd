class_name EquipmentDef
extends RefCounted

const CRANE_TYPES: Array[String] = ["tower_crane", "mobile_crane", "crawler_crane"]

var id: String = ""
var type: String = ""
var name: String = ""
var weekly_cost: float = 0.0
var mobilisation_cost: float = 0.0
var reach_cells: int = 0
var footprint_cells: int = 1
var max_count: int = 1


static func from_dict(d: Dictionary) -> EquipmentDef:
    var e := EquipmentDef.new()
    e.id = str(d.get("id", ""))
    e.type = str(d.get("type", ""))
    e.name = str(d.get("name", e.id))
    e.weekly_cost = float(d.get("weekly_cost", 0.0))
    e.mobilisation_cost = float(d.get("mobilisation_cost", 0.0))
    e.reach_cells = int(d.get("reach_cells", 0))
    e.footprint_cells = int(d.get("footprint_cells", 1))
    e.max_count = int(d.get("max_count", 1))
    return e


func is_crane() -> bool:
    return CRANE_TYPES.has(type)
