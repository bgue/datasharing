class_name PlanExport
extends RefCounted
## Writes the executed plan as plan_export.json (element_step_map.schema.json shape with
## actual dates) and a companion CSV for scheduling tools (docs/02 section 5).

const GENERATOR: String = "sitebuilder-godot"
const CSV_HEADER: String = "element_guid,task_id,step_id,planned_start,planned_finish,actual_start,actual_finish"


static func user_json_path(scenario_id: String) -> String:
    return "user://plan_export_%s.json" % scenario_id


static func user_csv_path(scenario_id: String) -> String:
    return "user://plan_export_%s.csv" % scenario_id


## task id -> exported T-id: loaded tasks keep theirs, runtime-added tasks (M000001 ...) get T-ids beyond the maximum
## (their manual id stays in `manual_id`).
static func task_id_map(gs: SimState) -> Dictionary:
    var map: Dictionary = {}
    var max_n: int = 0
    for t in gs.bundle.tasks:
        if t.task_id.length() == 7 and t.task_id.begins_with("T") and t.task_id.substr(1).is_valid_int():
            max_n = maxi(max_n, t.task_id.substr(1).to_int())
    for t in gs.bundle.tasks:
        if t.task_id.length() == 7 and t.task_id.begins_with("T") and t.task_id.substr(1).is_valid_int():
            map[t.task_id] = t.task_id
        else:
            max_n += 1
            map[t.task_id] = "T%06d" % max_n
    return map


static func build(gs: SimState) -> Dictionary:
    var tasks: Array = []
    var idmap: Dictionary = task_id_map(gs)
    for t in gs.bundle.tasks:
        var d: Dictionary = t.export_dict()
        d["task_id"] = idmap[t.task_id]
        if d.get("manual_id", null) == null and t.is_authored():
            d["manual_id"] = t.task_id
        for p in d.get("predecessors", []):
            (p as Dictionary)["task_id"] = idmap.get(str((p as Dictionary)["task_id"]), (p as Dictionary)["task_id"])
        var rt: TaskRuntime = gs.runtime[t.task_id]
        d["planned_start_day"] = t.planned_start_day
        d["planned_finish_day"] = t.planned_finish_day
        d["actual_start_day"] = rt.actual_start_day if rt.actual_start_day >= 0 else null
        d["actual_finish_day"] = rt.actual_finish_day if (TaskRuntime.is_finished(rt.state) and rt.actual_finish_day >= 0) else null
        tasks.append(d)
    var doc: Dictionary = {
        "schema_version": "1.0",
        "project": gs.bundle.project_raw.duplicate(true),
        "sector": gs.bundle.sector,
        "generated_at": Time.get_datetime_string_from_system(true) + "Z",
        "generator": GENERATOR,
        "step_library_ref": "sequence.json#step_library",
        "tasks": tasks,
    }
    if not gs.manual_zones.is_empty() or gs.bundle.manual != null or _has_authored(gs):
        doc["manual"] = Manual.export_doc(gs)
    return doc


static func _has_authored(gs: SimState) -> bool:
    for t in gs.bundle.tasks:
        if t.is_authored():
            return true
    return false


static func _day_str(v: Variant) -> String:
    return "" if v == null else str(int(v))


static func _guid_str(v: Variant) -> String:
    return "" if v == null else str(v)


static func build_csv(gs: SimState) -> String:
    var lines: PackedStringArray = [CSV_HEADER]
    for d in build(gs)["tasks"]:
        var row: Dictionary = d
        lines.append("%s,%s,%s,%s,%s,%s,%s" % [
            _guid_str(row["element_guid"]), row["task_id"], row["step_id"],
            _day_str(row["planned_start_day"]), _day_str(row["planned_finish_day"]),
            _day_str(row["actual_start_day"]), _day_str(row["actual_finish_day"]),
        ])
    return "\n".join(lines) + "\n"


## Writes JSON to `json_path` and (optionally) CSV to `csv_path`. Returns true on success.
static func export_to_path(gs: SimState, json_path: String, csv_path: String = "") -> bool:
    var f := FileAccess.open(json_path, FileAccess.WRITE)
    if f == null:
        push_error("plan export: cannot write %s (error %d)" % [json_path, FileAccess.get_open_error()])
        return false
    f.store_string(JSON.stringify(build(gs), " ") + "\n")
    f.close()
    if csv_path != "":
        var c := FileAccess.open(csv_path, FileAccess.WRITE)
        if c == null:
            push_error("plan export: cannot write %s" % csv_path)
            return false
        c.store_string(build_csv(gs))
        c.close()
    return true


## Writes user://plan_export_<scenario_id>.json and .csv. Returns the JSON path ("" on failure).
static func export_to_user(gs: SimState) -> String:
    var jp: String = user_json_path(gs.scenario.id)
    if export_to_path(gs, jp, user_csv_path(gs.scenario.id)):
        return jp
    return ""
