class_name ManualSequenceData
extends RefCounted
## The `manual` block of a bundle / a manual_sequence.json document (manual_sequence.schema.json).

var project: String = ""
var author: String = ""
var zones_in_manual_mode: Array[String] = []
var inherit_logic: bool = true
## Normalised manual task dictionaries (schema manual_task shape).
var tasks: Array[Dictionary] = []
var overrides: Array[Dictionary] = []
var applied_recipes: Array[Dictionary] = []
var raw: Dictionary = {}


static func from_dict(d: Dictionary) -> ManualSequenceData:
    var m := ManualSequenceData.new()
    m.raw = d
    m.project = str(d.get("project", ""))
    m.author = str(d.get("author", ""))
    var z: Variant = d.get("zones_in_manual_mode", [])
    if z is Array:
        for x in z:
            m.zones_in_manual_mode.append(str(x))
    m.inherit_logic = bool(d.get("inherit_logic", true))
    for t in d.get("tasks", []):
        if t is Dictionary:
            m.tasks.append(_normalise_task(t))
    for o in d.get("overrides", []):
        if o is Dictionary:
            m.overrides.append((o as Dictionary).duplicate(true))
    for a in d.get("applied_recipes", []):
        if a is Dictionary:
            var ad: Dictionary = a
            var eg: Variant = ad.get("element_guid", null)
            m.applied_recipes.append({"recipe": str(ad.get("recipe", "")), "zone_id": str(ad.get("zone_id", "")),
                    "element_guid": "" if eg == null else str(eg), "include_optional": bool(ad.get("include_optional", false))})
    return m


static func _normalise_task(t: Dictionary) -> Dictionary:
    var out: Dictionary = t.duplicate(true)
    out["id"] = str(t.get("id", ""))
    out["step"] = str(t.get("step", ""))
    out["zone_id"] = str(t.get("zone_id", ""))
    out["virtual"] = bool(t.get("virtual", false))
    out["link_type"] = str(t.get("link_type", "FS"))
    out["lag_days"] = int(t.get("lag_days", 0))
    var el: Array = []
    if t.get("elements", []) is Array:
        for g in t.get("elements", []):
            el.append(str(g))
    out["elements"] = el
    var af: Array = []
    if t.get("after", []) is Array:
        for g in t.get("after", []):
            af.append(str(g))
    out["after"] = af
    return out


func task_by_id(id: String) -> Dictionary:
    for t in tasks:
        if str(t["id"]) == id:
            return t
    return {}
