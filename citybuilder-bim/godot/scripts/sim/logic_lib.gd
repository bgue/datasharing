class_name LogicLib
extends RefCounted
## Construction logic library in the game (docs/06 track A): recipe matcher, `explain` (which steps of the
## applicable recipes are covered by tasks, virtual and present, or missing) and the library loader.
## Recipes normally come from the bundle's `recipes[]` (SimState.recipe_list); a bundle without any falls back to
## res://logic/index.json + res://logic/recipes/**.json when present.

const LIBRARY_ROOT: String = "res://logic"


# ----------------------------------------------------------------- loading

## Loads the standalone library: index.json (optional, `recipes` array of ids / paths / summaries) and every
## recipes/**.json under `root`. Tolerant: missing folder, bad JSON or foreign shapes are skipped.
static func load_library(root: String = LIBRARY_ROOT) -> Array[RecipeData]:
    var out: Array[RecipeData] = []
    var seen: Dictionary = {}
    var files: Array[String] = []
    _collect_json(root + "/recipes", files)
    files.sort()
    for f in files:
        var json := JSON.new()
        if json.parse(FileAccess.get_file_as_string(f)) != OK:
            continue
        var parsed: Variant = json.data
        if parsed is Dictionary and (parsed as Dictionary).has("steps") and (parsed as Dictionary).has("id"):
            var r := RecipeData.from_dict(parsed)
            if r.id != "" and not seen.has(r.id):
                seen[r.id] = true
                out.append(r)
    return out


static func _collect_json(dir_path: String, out: Array[String]) -> void:
    var dir := DirAccess.open(dir_path)
    if dir == null:
        return
    for f in dir.get_files():
        if f.ends_with(".json"):
            out.append(dir_path + "/" + f)
    for d in dir.get_directories():
        _collect_json(dir_path + "/" + d, out)


# ----------------------------------------------------------------- matching

## Why a recipe applies to an element by its own properties (ANY key of applies_to matching is enough):
## ifc_class, visual_kit, name_regex, keywords (against the element name and ifc class). The zone tags and the
## properties / predefined_type keys are not part of an element in the bundle: see recipe_matches_zone().
static func element_matches(_gs: SimState, recipe: RecipeData, e: ElementData) -> Array[String]:
    var why: Array[String] = []
    var at: Dictionary = recipe.applies_to
    if recipe.applies_list("ifc_class").has(e.ifc_class):
        why.append("ifc_class")
    var kits: Array[String] = recipe.applies_list("visual_kit")
    if e.visual_kit != "" and kits.has(e.visual_kit):
        why.append("visual_kit")
    var rx: String = str(at.get("name_regex", ""))
    if rx != "":
        var re := RegEx.new()
        if re.compile(rx) == OK and re.search(e.name) != null:
            why.append("name_regex")
    var hay: String = (e.name + " " + e.ifc_class).to_lower()
    for kw in recipe.applies_list("keywords"):
        if kw != "" and hay.contains(kw.to_lower()):
            why.append("keywords")
            break
    return why


static func recipe_matches_zone(recipe: RecipeData, zone: ZoneData) -> bool:
    for tag in recipe.applies_list("zone_tags_any"):
        if zone.tags.has(tag):
            return true
    return false


## All the reasons a recipe applies to an element: its own matchers plus the zone's tags.
static func match_reasons(gs: SimState, recipe: RecipeData, e: ElementData) -> Array[String]:
    var why: Array[String] = element_matches(gs, recipe, e)
    var zone: ZoneData = gs.bundle.zones_by_id.get(e.zone_id, null)
    if zone != null and recipe_matches_zone(recipe, zone):
        why.append("zone_tags_any")
    return why


## Recipes applying to an element: Array of {recipe: RecipeData, matched_by: Array[String]}.
static func recipes_for_element(gs: SimState, e: ElementData) -> Array[Dictionary]:
    var out: Array[Dictionary] = []
    for r in gs.recipe_list():
        var why: Array[String] = match_reasons(gs, r, e)
        if not why.is_empty():
            out.append({"recipe": r, "matched_by": why})
    return out


# ----------------------------------------------------------------- list / get

static func list_recipes(gs: SimState, sector: String = "") -> Array[Dictionary]:
    var out: Array[Dictionary] = []
    for r in gs.recipe_list():
        if sector != "" and r.sector != sector and r.sector != "all":
            continue
        out.append(r.to_summary())
    return out


static func get_recipe(gs: SimState, id: String) -> Dictionary:
    var r: RecipeData = gs.recipe_by_id(id)
    if r == null:
        gs.last_error = "unknown recipe: %s" % id
        return {}
    return r.raw.duplicate(true)


# ----------------------------------------------------------------- explain

## Row of one step: {key, ref, name, status: covered | virtual_present | missing, virtual, task_id, task_ids,
## from_element, hold_point, optional, note[, frozen_task_ids]}.
static func _row(gs: SimState, step: RecipeStepData, zone_id: String, elements: Array[String]) -> Dictionary:
    var b: SequenceBundle = gs.bundle
    var st: StepDef = b.steps_by_id.get(step.ref, null)
    var nm: String = st.name if st != null else (str(step.step.get("name", "")) if not step.step.is_empty() else step.ref)
    var row: Dictionary = {"key": step.key, "ref": step.ref, "name": nm if nm != "" else step.key, "status": "missing",
            "virtual": step.is_virtual, "task_id": null, "task_ids": [], "from_element": step.from_element,
            "hold_point": step.hold_point if step.hold_point != "" else null, "optional": step.optional, "note": step.note}
    if step.duration_days > 0:
        row["duration_days"] = step.duration_days
    var ids: Array[String] = []
    var frozen: Array[String] = []
    if step.is_virtual:
        for t in b.virtual_tasks:
            if t.step_id == step.ref and t.zone_id == zone_id:
                ids.append(t.task_id)
        if not ids.is_empty():
            row["status"] = "virtual_present"
    else:
        for t in _tasks_of_step(gs, step.ref, elements, zone_id):
            if gs.is_frozen_task(t):
                frozen.append(t.task_id)
            else:
                ids.append(t.task_id)
        if not ids.is_empty():
            row["status"] = "covered"
        if not frozen.is_empty():
            row["frozen_task_ids"] = frozen
    row["task_ids"] = ids
    if not ids.is_empty():
        row["task_id"] = ids[0]
    return row


static func _tasks_of_step(gs: SimState, ref: String, elements: Array[String], zone_id: String) -> Array[TaskData]:
    var out: Array[TaskData] = []
    if elements.is_empty():  # zone scope: any non-virtual task of the step in the zone
        for t in gs.bundle.tasks_by_zone.get(zone_id, []):
            var task: TaskData = t
            if task.step_id == ref and not task.is_virtual:
                out.append(task)
        return out
    for g in elements:
        for t in gs.bundle.tasks_by_element.get(g, []):
            var task2: TaskData = t
            if task2.step_id == ref and not out.has(task2):
                out.append(task2)
    return out


static func _coverage(rows: Array) -> Dictionary:
    var c: Dictionary = {"covered": 0, "virtual_present": 0, "missing": 0, "total": 0}
    for r in rows:
        if bool((r as Dictionary)["optional"]) and str((r as Dictionary)["status"]) == "missing":
            continue
        c[str((r as Dictionary)["status"])] = int(c[str((r as Dictionary)["status"])]) + 1
        c["total"] = int(c["total"]) + 1
    return c


static func _recipe_entry(gs: SimState, recipe: RecipeData, matched_by: Array[String], zone_id: String, base: ElementData) -> Dictionary:
    var flat: Dictionary = recipe.flatten(func(id: String) -> RecipeData: return gs.recipe_by_id(id))
    var rows: Array = []
    for s in flat["steps"]:
        var step: RecipeStepData = s
        var bound: Array[String] = []
        if not step.is_virtual and base != null:
            bound = Manual.bind_elements(gs, base, step.from_element, zone_id)
        rows.append(_row(gs, step, zone_id, bound))
    return {"recipe_id": recipe.id, "name": recipe.name, "sector": recipe.sector, "summary": recipe.summary,
            "matched_by": matched_by, "steps": rows, "coverage": _coverage(rows), "checks": recipe.checks.duplicate(),
            "prerequisites": recipe.prerequisites.duplicate(true)}


## Explains an element: every applicable recipe with the status of each step bound to the element (self /
## foundation / host / system / zone semantics). `recipes` is empty and `none` true when nothing applies.
static func explain_element(gs: SimState, guid: String) -> Dictionary:
    var e: ElementData = gs.bundle.elements_by_guid.get(guid, null)
    if e == null:
        gs.last_error = "unknown element: %s" % guid
        return {}
    var out: Array = []
    for m in recipes_for_element(gs, e):
        out.append(_recipe_entry(gs, m["recipe"], m["matched_by"], e.zone_id, e))
    return {"scope": "element", "element_guid": guid, "zone_id": e.zone_id, "name": e.name, "ifc_class": e.ifc_class,
            "recipes": out, "none": out.is_empty()}


## Explains a zone: recipes matched by the zone tags or by any element of the zone; a step is covered when the
## zone holds a task of its step (virtual steps: a virtual task of the step in the zone).
static func explain_zone(gs: SimState, zone_id: String) -> Dictionary:
    var zone: ZoneData = gs.bundle.zones_by_id.get(zone_id, null)
    if zone == null:
        gs.last_error = "no such zone: %s" % zone_id
        return {}
    var out: Array = []
    for r in gs.recipe_list():
        var why: Array[String] = []
        var n_el: int = 0
        if recipe_matches_zone(r, zone):
            why.append("zone_tags_any")
        for e in gs.bundle.elements:
            if e.zone_id == zone_id and not element_matches(gs, r, e).is_empty():
                n_el += 1
                for w in element_matches(gs, r, e):
                    if not why.has(w):
                        why.append(w)
        if why.is_empty():
            continue
        var entry: Dictionary = _recipe_entry(gs, r, why, zone_id, null)
        entry["matching_elements"] = n_el
        out.append(entry)
    return {"scope": "zone", "zone_id": zone_id, "name": zone.name, "recipes": out, "none": out.is_empty(),
            "manual_mode": gs.manual_zones.has(zone_id)}


## Plain-text lines of an explain result (zone inspector, logs).
static func explain_lines(res: Dictionary) -> Array[String]:
    var lines: Array[String] = []
    if res.is_empty():
        return lines
    if bool(res.get("none", false)):
        lines.append("No recipe applies here.")
        return lines
    for r in res["recipes"]:
        var rd: Dictionary = r
        var cov: Dictionary = rd["coverage"]
        lines.append("%s  (%d of %d steps in place)" % [rd["name"], int(cov["covered"]) + int(cov["virtual_present"]), int(cov["total"])])
        for row in rd["steps"]:
            var rr: Dictionary = row
            var mark: String = "[x]"
            match str(rr["status"]):
                "virtual_present":
                    mark = "[v]"
                "missing":
                    mark = "[ ]"
            lines.append("  %s %s%s%s" % [mark, rr["name"], " (virtual)" if bool(rr["virtual"]) else "",
                    " - optional" if bool(rr["optional"]) else ""])
    return lines
