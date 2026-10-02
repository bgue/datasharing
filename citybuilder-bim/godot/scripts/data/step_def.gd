class_name StepDef
extends RefCounted
## A construction step type from the embedded step library.

var id: String = ""
var name: String = ""
var description: String = ""
var phase: String = ""
var trade: String = ""
var discipline: String = "general"
var quantity_basis: String = "count"
var rate_per_crew_day: float = 1.0
var unit_cost: float = 0.0
var min_duration_days: int = 1
var requires_crane: bool = false
var requires_access: bool = true
var laydown_cells: int = 0
var lead_time_weeks: int = 0
var inspection: bool = false
var inspection_type: String = ""
var risk: float = 0.1
var weather_sensitive: bool = false
var noisy: bool = false
var dusty: bool = false
var progress_visual: String = "solid"
var tags: Array[String] = []


static func from_dict(d: Dictionary) -> StepDef:
    var s := StepDef.new()
    s.id = str(d.get("id", ""))
    s.name = str(d.get("name", s.id))
    s.description = str(d.get("description", ""))
    s.phase = str(d.get("phase", ""))
    s.trade = str(d.get("trade", ""))
    s.discipline = str(d.get("discipline", "general"))
    s.quantity_basis = str(d.get("quantity_basis", "count"))
    s.rate_per_crew_day = float(d.get("rate_per_crew_day", 1.0))
    s.unit_cost = float(d.get("unit_cost", 0.0))
    s.min_duration_days = int(d.get("min_duration_days", 1))
    s.requires_crane = bool(d.get("requires_crane", false))
    s.requires_access = bool(d.get("requires_access", true))
    s.laydown_cells = int(d.get("laydown_cells", 0))
    s.lead_time_weeks = int(d.get("lead_time_weeks", 0))
    s.inspection = bool(d.get("inspection", false))
    var it: Variant = d.get("inspection_type", null)
    s.inspection_type = "" if it == null else str(it)
    s.risk = float(d.get("risk", 0.1))
    s.weather_sensitive = bool(d.get("weather_sensitive", false))
    s.noisy = bool(d.get("noisy", false))
    s.dusty = bool(d.get("dusty", false))
    s.progress_visual = str(d.get("progress_visual", "solid"))
    var tg: Variant = d.get("tags", [])
    if tg is Array:
        for t in tg:
            s.tags.append(str(t))
    return s
