class_name RecipeStepData
extends RefCounted
## One step of a construction logic recipe (recipe.schema.json recipe_step).

var key: String = ""
## Step id in the step library ("" for inline `step` definitions and nested recipes).
var ref: String = ""
## Inline step definition (step_library steps[] shape) for one-off activities.
var step: Dictionary = {}
## Nested recipe id ("" when the entry is a plain step).
var recipe: String = ""
var is_virtual: bool = false
## self | foundation | host | system | zone
var from_element: String = "self"
var quantity: Dictionary = {}
var duration_days: int = 0
var parallel_with: String = ""
var lag_days: int = 0
var hold_point: String = ""
var progress_visual: String = ""
var marker: String = ""
var note: String = ""
var optional: bool = false


static func from_dict(d: Dictionary) -> RecipeStepData:
    var s := RecipeStepData.new()
    s.ref = str(d.get("ref", ""))
    var inline: Variant = d.get("step", null)
    if inline is Dictionary:
        s.step = inline
    s.recipe = str(d.get("recipe", ""))
    var k: String = str(d.get("key", ""))
    if k == "":
        k = s.ref if s.ref != "" else (str(s.step.get("id", "")) if not s.step.is_empty() else s.recipe)
    s.key = k
    if s.ref == "" and not s.step.is_empty():
        s.ref = str(s.step.get("id", ""))
    s.is_virtual = bool(d.get("virtual", false))
    s.from_element = str(d.get("from_element", "self"))
    var q: Variant = d.get("quantity", null)
    if q is Dictionary:
        s.quantity = q
    var dd: Variant = d.get("duration_days", null)
    s.duration_days = 0 if dd == null else maxi(0, int(dd))
    s.parallel_with = str(d.get("parallel_with", ""))
    s.lag_days = int(d.get("lag_days", 0))
    var hp: Variant = d.get("hold_point", null)
    s.hold_point = "" if hp == null else str(hp)
    s.progress_visual = str(d.get("progress_visual", ""))
    s.marker = str(d.get("marker", ""))
    s.note = str(d.get("note", ""))
    s.optional = bool(d.get("optional", false))
    return s
