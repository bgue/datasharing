class_name TaskData
extends RefCounted
## One task = one step applied to one element (element_step_map.schema.json task).

var task_id: String = ""
var element_guid: String = ""
var ifc_class: String = ""
var element_name: String = ""
var storey_id: String = ""
var zone_id: String = ""
var system_id: String = ""
var step_id: String = ""
var phase: String = ""
var trade: String = ""
var quantity: float = 0.0
var unit: String = ""
var estimated_crew_days: float = 0.0
var cost: float = 0.0
var cells: Array[Vector2i] = []
var requires_crane: bool = false
var requires_access: bool = true
var inspection: bool = false
var inspection_type: String = ""
var lead_time_weeks: int = 0
var laydown_cells: int = 0
var risk: float = 0.1
var weather_sensitive: bool = false
var noisy: bool = false
var dusty: bool = false
## Array of {task_id: String, type: String ("FS"|"SS"|"FF"), lag_days: int}
var predecessors: Array[Dictionary] = []
var rule_id: String = ""
var package_id: String = ""
var work_face: String = "any"
var planned_start_day: int = 0
var planned_finish_day: int = 0
var is_critical: bool = false
## The original JSON dictionary (kept for lossless export).
var raw: Dictionary = {}


static func from_dict(d: Dictionary) -> TaskData:
    var t := TaskData.new()
    t.raw = d
    t.task_id = str(d.get("task_id", ""))
    t.element_guid = str(d.get("element_guid", ""))
    t.ifc_class = str(d.get("ifc_class", ""))
    t.element_name = str(d.get("element_name", ""))
    t.storey_id = str(d.get("storey_id", ""))
    t.zone_id = str(d.get("zone_id", ""))
    var sys: Variant = d.get("system_id", null)
    t.system_id = "" if sys == null else str(sys)
    t.step_id = str(d.get("step_id", ""))
    t.phase = str(d.get("phase", ""))
    t.trade = str(d.get("trade", ""))
    t.quantity = float(d.get("quantity", 0.0))
    t.unit = str(d.get("unit", ""))
    t.estimated_crew_days = float(d.get("estimated_crew_days", 0.0))
    t.cost = float(d.get("cost", 0.0))
    t.cells = ZoneData.cells_from_variant(d.get("cells", []))
    var f: Dictionary = d.get("flags", {})
    t.requires_crane = bool(f.get("requires_crane", false))
    t.requires_access = bool(f.get("requires_access", true))
    t.inspection = bool(f.get("inspection", false))
    var it: Variant = f.get("inspection_type", null)
    t.inspection_type = "" if it == null else str(it)
    t.lead_time_weeks = int(f.get("lead_time_weeks", 0))
    t.laydown_cells = int(f.get("laydown_cells", 0))
    t.risk = float(f.get("risk", 0.1))
    t.weather_sensitive = bool(f.get("weather_sensitive", false))
    t.noisy = bool(f.get("noisy", false))
    t.dusty = bool(f.get("dusty", false))
    var preds: Variant = d.get("predecessors", [])
    if preds is Array:
        for p in preds:
            var pd: Dictionary = p
            t.predecessors.append({
                "task_id": str(pd.get("task_id", "")),
                "type": str(pd.get("type", "FS")),
                "lag_days": int(pd.get("lag_days", 0)),
            })
    t.rule_id = str(d.get("rule_id", ""))
    t.package_id = str(d.get("package_id", ""))
    var ps: Variant = d.get("planned_start_day", null)
    t.planned_start_day = 0 if ps == null else int(ps)
    var pf: Variant = d.get("planned_finish_day", null)
    t.planned_finish_day = 0 if pf == null else int(pf)
    t.is_critical = bool(d.get("is_critical", false))
    return t


## Tag lookup used by event filters: flags (weather_sensitive, noisy, dusty,
## requires_crane, inspection) or step tags passed in.
func has_tag(tag: String, step_tags: Array[String] = []) -> bool:
    match tag:
        "weather_sensitive":
            return weather_sensitive
        "noisy":
            return noisy
        "dusty":
            return dusty
        "requires_crane":
            return requires_crane
        "inspection":
            return inspection
    return step_tags.has(tag) or tag == phase or tag == trade or tag == step_id
