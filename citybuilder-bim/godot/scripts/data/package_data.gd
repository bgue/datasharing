class_name PackageData
extends RefCounted
## A work package: tasks of one trade in one zone (and work face) planned as a unit (docs/05 section 1).

var package_id: String = ""
var name: String = ""
var zone_id: String = ""
var storey_id: String = ""
var phase: String = ""
var trade: String = ""
var discipline: String = "general"
var work_face: String = "any"
var task_ids: Array[String] = []
var tasks: Array[TaskData] = []  # resolved by SequenceBundle
var total_crew_days: float = 0.0
var crew_min: int = 1
var crew_ideal: int = 1
var crew_max: int = 1
var planned_start_day: int = 0
var planned_finish_day: int = 0
var requires_crane: bool = false
var lead_time_weeks: int = 0
var laydown_cells: int = 0
var cost: float = 0.0
var synthesized: bool = false


static func from_dict(d: Dictionary) -> PackageData:
    var p := PackageData.new()
    p.package_id = str(d.get("package_id", ""))
    p.name = str(d.get("name", p.package_id))
    p.zone_id = str(d.get("zone_id", ""))
    p.storey_id = str(d.get("storey_id", ""))
    p.phase = str(d.get("phase", ""))
    p.trade = str(d.get("trade", ""))
    p.discipline = str(d.get("discipline", "general"))
    p.work_face = str(d.get("work_face", "any"))
    var ids: Variant = d.get("task_ids", [])
    if ids is Array:
        for t in ids:
            p.task_ids.append(str(t))
    p.total_crew_days = float(d.get("total_crew_days", 0.0))
    var cp: Dictionary = d.get("crew_profile", {})
    p.crew_min = maxi(1, int(cp.get("min", 1)))
    p.crew_ideal = maxi(p.crew_min, int(cp.get("ideal", p.crew_min)))
    p.crew_max = maxi(p.crew_ideal, int(cp.get("max", p.crew_ideal)))
    var ps: Variant = d.get("planned_start_day", null)
    p.planned_start_day = 0 if ps == null else int(ps)
    var pf: Variant = d.get("planned_finish_day", null)
    p.planned_finish_day = 0 if pf == null else int(pf)
    p.requires_crane = bool(d.get("requires_crane", false))
    p.lead_time_weeks = int(d.get("lead_time_weeks", 0))
    p.laydown_cells = int(d.get("laydown_cells", 0))
    p.cost = float(d.get("cost", 0.0))
    return p


func profile_dict() -> Dictionary:
    return {"min": crew_min, "ideal": crew_ideal, "max": crew_max}


func to_dict() -> Dictionary:
    return {"package_id": package_id, "name": name, "zone_id": zone_id, "storey_id": storey_id, "phase": phase,
            "trade": trade, "discipline": discipline, "work_face": work_face, "task_ids": task_ids.duplicate(),
            "total_crew_days": total_crew_days, "crew_profile": profile_dict(),
            "planned_start_day": planned_start_day, "planned_finish_day": planned_finish_day,
            "requires_crane": requires_crane, "lead_time_weeks": lead_time_weeks, "laydown_cells": laydown_cells,
            "cost": cost}
