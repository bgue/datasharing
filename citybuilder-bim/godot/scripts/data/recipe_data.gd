class_name RecipeData
extends RefCounted
## A construction logic recipe (recipe.schema.json): the steps an installation needs, BIM-represented or not.

var id: String = ""
var name: String = ""
var summary: String = ""
var sector: String = ""
var tags: Array[String] = []
## applies_to matcher (OR across keys): ifc_class, predefined_type, name_regex, properties, visual_kit,
## zone_tags_any, keywords. Typed access through the helpers below.
var applies_to: Dictionary = {}
var prerequisites: Dictionary = {}
var steps: Array[RecipeStepData] = []
## Array of {after: String, before: String, type: String, lag_days: int, reason: String}
var logic: Array[Dictionary] = []
var checks: Array[String] = []
var references: Array[String] = []
var typical_duration_weeks: Array[float] = []
var virtual_visual: String = ""
var raw: Dictionary = {}


static func from_dict(d: Dictionary) -> RecipeData:
    var r := RecipeData.new()
    r.raw = d
    r.id = str(d.get("id", ""))
    r.name = str(d.get("name", r.id))
    r.summary = str(d.get("summary", ""))
    r.sector = str(d.get("sector", ""))
    r.tags = _strings(d.get("tags", []))
    var at: Variant = d.get("applies_to", {})
    r.applies_to = at if at is Dictionary else {}
    var pr: Variant = d.get("prerequisites", {})
    r.prerequisites = pr if pr is Dictionary else {}
    for s in d.get("steps", []):
        if s is Dictionary:
            r.steps.append(RecipeStepData.from_dict(s))
    for l in d.get("logic", []):
        if l is Dictionary:
            var ld: Dictionary = l
            r.logic.append({
                "after": str(ld.get("after", "")), "before": str(ld.get("before", "")),
                "type": str(ld.get("type", "FS")), "lag_days": int(ld.get("lag_days", 0)),
                "reason": str(ld.get("reason", "")),
            })
    r.checks = _strings(d.get("checks", []))
    r.references = _strings(d.get("references", []))
    var tdw: Variant = d.get("typical_duration_weeks", [])
    if tdw is Array:
        for v in tdw:
            r.typical_duration_weeks.append(float(v))
    r.virtual_visual = str(d.get("virtual_visual", ""))
    return r


static func _strings(v: Variant) -> Array[String]:
    var out: Array[String] = []
    if v is Array:
        for x in v:
            out.append(str(x))
    return out


func applies_list(key: String) -> Array[String]:
    return _strings(applies_to.get(key, []))


## Steps with nested recipes expanded in place (`lookup`: recipe id -> RecipeData, or null). Nested step keys are
## prefixed "<nested id>/"; nested logic is returned with the same prefixing as {after, before, ...}.
func flatten(lookup: Callable, depth: int = 0) -> Dictionary:
    var out_steps: Array[RecipeStepData] = []
    var out_logic: Array[Dictionary] = []
    for s in steps:
        if s.recipe == "":
            out_steps.append(s)
            continue
        var nested: RecipeData = lookup.call(s.recipe) if depth < 4 else null
        if nested == null:
            continue
        var sub: Dictionary = nested.flatten(lookup, depth + 1)
        var first: bool = true
        for ns in sub["steps"]:
            var ps: RecipeStepData = ns
            var copy: RecipeStepData = RecipeStepData.from_dict({"ref": ps.ref, "step": ps.step, "key": s.recipe + "/" + ps.key,
                    "virtual": ps.is_virtual, "from_element": ps.from_element, "quantity": ps.quantity,
                    "duration_days": ps.duration_days if ps.duration_days > 0 else null,
                    "parallel_with": (s.recipe + "/" + ps.parallel_with) if ps.parallel_with != "" else "",
                    "lag_days": ps.lag_days + (s.lag_days if first else 0), "hold_point": ps.hold_point,
                    "progress_visual": ps.progress_visual, "marker": ps.marker, "note": ps.note,
                    "optional": ps.optional or s.optional})
            out_steps.append(copy)
            first = false
        for l in sub["logic"]:
            var ld: Dictionary = (l as Dictionary).duplicate()
            ld["after"] = s.recipe + "/" + str(ld["after"])
            ld["before"] = s.recipe + "/" + str(ld["before"])
            out_logic.append(ld)
    out_logic.append_array(logic)
    return {"steps": out_steps, "logic": out_logic}


func to_summary() -> Dictionary:
    return {"id": id, "name": name, "summary": summary, "sector": sector, "tags": tags.duplicate(),
            "applies_to": applies_to.duplicate(true), "steps": steps.size(),
            "typical_duration_weeks": typical_duration_weeks.duplicate()}
