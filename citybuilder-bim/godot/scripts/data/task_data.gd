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
## Virtual task (docs/06 A.3): no BIM element produces it (survey, dewatering, permit...). `element_guid` is "".
var is_virtual: bool = false
## "rule" (generated), "recipe" or "manual".
var origin: String = "rule"
var recipe_id: String = ""
## Fixed duration for time-driven tasks (0 = effort driven): one crew advances it one day per working day.
var duration_days: int = 0
var marker: String = ""
var manual_id: String = ""
## Every element a manual task is bound to (`element_guid` is the first one, "" for virtual tasks).
var element_guids: Array[String] = []
## Created at runtime (manual.add_task / recipes), not part of the loaded bundle.
var runtime_added: bool = false
var note: String = ""
## The original JSON dictionary (kept for lossless export).
var raw: Dictionary = {}


static func from_dict(d: Dictionary) -> TaskData:
    var t := TaskData.new()
    t.raw = d
    t.task_id = str(d.get("task_id", ""))
    var eg: Variant = d.get("element_guid", null)
    t.element_guid = "" if eg == null else str(eg)
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
            var pr: Dictionary = {
                "task_id": str(pd.get("task_id", "")),
                "type": str(pd.get("type", "FS")),
                "lag_days": int(pd.get("lag_days", 0)),
            }
            if pd.has("reason"):
                pr["reason"] = str(pd["reason"])
            t.predecessors.append(pr)
    t.rule_id = str(d.get("rule_id", ""))
    t.package_id = str(d.get("package_id", ""))
    var ps: Variant = d.get("planned_start_day", null)
    t.planned_start_day = 0 if ps == null else int(ps)
    var pf: Variant = d.get("planned_finish_day", null)
    t.planned_finish_day = 0 if pf == null else int(pf)
    t.is_critical = bool(d.get("is_critical", false))
    t.is_virtual = bool(d.get("virtual", false))
    t.origin = str(d.get("origin", "rule"))
    var rid: Variant = d.get("recipe_id", null)
    t.recipe_id = "" if rid == null else str(rid)
    var dd: Variant = d.get("duration_days", null)
    t.duration_days = 0 if dd == null else maxi(0, int(dd))
    var mk: Variant = d.get("marker", null)
    t.marker = "" if mk == null else str(mk)
    var mid: Variant = d.get("manual_id", null)
    t.manual_id = "" if mid == null else str(mid)
    var eg_list: Variant = d.get("element_guids", null)
    if eg_list is Array:
        for g in eg_list:
            t.element_guids.append(str(g))
    elif t.element_guid != "":
        t.element_guids.append(t.element_guid)
    t.runtime_added = bool(d.get("runtime_added", false))
    t.note = str(d.get("note", ""))
    if t.duration_days > 0:
        t.estimated_crew_days = float(t.duration_days)  # duration-driven: one crew, one day per working day
    return t


## True for tasks authored by the player / a recipe (not generated by the pipeline rules).
func is_authored() -> bool:
    return origin == "manual" or runtime_added


## Time-driven (duration_days) task: needs exactly one crew, advances one day per working day.
func is_duration_driven() -> bool:
    return duration_days > 0


## Task dictionary in element_step_map.schema.json shape built from the current fields (for runtime-added tasks,
## saves and exports; loaded tasks merge these over their `raw` dictionary, see export_dict()).
func to_dict() -> Dictionary:
    var cells_out: Array = []
    for c in cells:
        cells_out.append([c.x, c.y])
    var preds: Array = []
    for p in predecessors:
        var pr: Dictionary = {"task_id": str(p["task_id"]), "type": str(p["type"]), "lag_days": int(p["lag_days"])}
        if p.has("reason"):
            pr["reason"] = str(p["reason"])
        preds.append(pr)
    var d: Dictionary = {
        "task_id": task_id,
        "element_guid": element_guid if element_guid != "" else null,
        "ifc_class": ifc_class, "element_name": element_name, "storey_id": storey_id, "zone_id": zone_id,
        "system_id": system_id if system_id != "" else null,
        "step_id": step_id, "phase": phase, "trade": trade, "quantity": quantity, "unit": unit,
        "estimated_crew_days": estimated_crew_days, "cost": cost, "cells": cells_out,
        "flags": {
            "requires_crane": requires_crane, "requires_access": requires_access, "inspection": inspection,
            "inspection_type": inspection_type if inspection_type != "" else null,
            "lead_time_weeks": lead_time_weeks, "laydown_cells": laydown_cells, "risk": risk,
            "weather_sensitive": weather_sensitive, "noisy": noisy, "dusty": dusty,
        },
        "predecessors": preds, "rule_id": rule_id,
        "planned_start_day": planned_start_day, "planned_finish_day": planned_finish_day,
        "is_critical": is_critical, "package_id": package_id, "work_face": work_face,
        "virtual": is_virtual, "origin": origin,
        "recipe_id": recipe_id if recipe_id != "" else null,
        "duration_days": duration_days if duration_days > 0 else null,
        "marker": marker if marker != "" else null,
        "manual_id": manual_id if manual_id != "" else null,
    }
    if element_guids.size() > 1 or (is_virtual and not element_guids.is_empty()):
        d["element_guids"] = element_guids.duplicate()
    if runtime_added:
        d["runtime_added"] = true
    if note != "":
        d["note"] = note
    return d


## Export / view dictionary: the loaded `raw` dictionary (lossless) with the fields the simulation may
## have changed (links, virtual / manual fields) refreshed; runtime-added tasks are built from scratch.
func export_dict() -> Dictionary:
    if raw.is_empty():
        return to_dict()
    var d: Dictionary = raw.duplicate(true)
    var cur: Dictionary = to_dict()
    for k in ["predecessors", "package_id", "virtual", "origin", "recipe_id", "duration_days", "marker", "manual_id",
            "element_guid", "element_guids", "runtime_added", "note", "estimated_crew_days", "quantity", "cost",
            "element_name", "zone_id", "storey_id", "cells", "step_id", "phase", "trade", "unit"]:
        if cur.has(k):
            d[k] = cur[k]
    return d


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
