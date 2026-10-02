extends TC
## Construction logic library (docs/06 A.1 / A.2): bundle parsing, matcher, explain, apply, library loader.

const RS := TaskRuntime.State
const ZONE: String = "L00-Z1"

const REC_FOOTING: Dictionary = {
    "schema_version": "1.0", "id": "rec_footing_column", "name": "Footing and column", "summary": "Survey, pour, cure, column.",
    "sector": "healthcare", "tags": ["structure"],
    "applies_to": {"ifc_class": ["IfcFooting"], "keywords": ["pad"]},
    "prerequisites": {"equipment": ["crawler_crane"], "permits": ["hot_work"]},
    "steps": [
        {"ref": "GEN-SURVEY-SETOUT", "virtual": true, "duration_days": 2, "marker": "survey"},
        {"ref": "STR-FOOT-POUR", "hold_point": "structural"},
        {"ref": "STR-COL-POUR", "from_element": "host"},
        {"key": "hydro", "ref": "GEN-HYDROTEST", "virtual": true, "optional": true, "duration_days": 3, "hold_point": "pressure_test", "marker": "test"},
    ],
    "logic": [{"after": "STR-FOOT-POUR", "before": "STR-COL-POUR", "lag_days": 7, "reason": "cure"}],
    "checks": ["settlement survey before hydrotest"], "typical_duration_weeks": [8, 14],
}
const REC_DUCT: Dictionary = {
    "schema_version": "1.0", "id": "rec_duct", "name": "Duct run", "sector": "healthcare",
    "applies_to": {"keywords": ["duct"]},
    "steps": [{"ref": "MEP-DUCT-INSTALL"}, {"ref": "CX-AIR-BALANCE"}],
}
const REC_ZONE: Dictionary = {
    "schema_version": "1.0", "id": "rec_occupied", "name": "Occupied-area works", "sector": "healthcare",
    "applies_to": {"zone_tags_any": ["occupied_adjacent"]},
    "steps": [{"ref": "GEN-SURVEY-SETOUT", "virtual": true, "marker": "permit", "duration_days": 1}],
}
const REC_ANY: Dictionary = {
    "schema_version": "1.0", "id": "rec_wall_any", "name": "Wall by class only", "sector": "all",
    "applies_to": {"ifc_class": ["IfcWall"], "keywords": ["zzz"], "visual_kit": ["tank"], "name_regex": "^NOPE$"},
    "steps": [{"ref": "ARC-WALL-BUILD"}],
}
const REC_NESTED: Dictionary = {
    "schema_version": "1.0", "id": "rec_nested", "name": "Nested", "sector": "healthcare",
    "applies_to": {"keywords": ["partition"]},
    "steps": [{"recipe": "rec_duct"}, {"ref": "GEN-SURVEY-ASBUILT", "virtual": true, "duration_days": 1}],
    "logic": [{"after": "rec_duct/MEP-DUCT-INSTALL", "before": "GEN-SURVEY-ASBUILT", "lag_days": 1}],
}


func _recipes() -> Array:
    return [REC_FOOTING.duplicate(true), REC_DUCT.duplicate(true), REC_ZONE.duplicate(true), REC_ANY.duplicate(true),
            REC_NESTED.duplicate(true)]


func _state() -> SimState:
    var gs: SimState = fixture_state(_recipes())
    gs.inspection_fail_override = 0.0
    return gs


func _ids_of(res: Dictionary) -> Array:
    var out: Array = []
    for r in res["recipes"]:
        out.append(r["recipe_id"])
    out.sort()
    return out


func _row(entry: Dictionary, key: String) -> Dictionary:
    for r in entry["steps"]:
        if r["key"] == key:
            return r
    return {}


func _entry(res: Dictionary, id: String) -> Dictionary:
    for r in res["recipes"]:
        if r["recipe_id"] == id:
            return r
    return {}


func test_parse_recipes_manual_block_and_element_fields() -> void:
    var d: Dictionary = fixture_dict(_recipes())
    d["manual"] = {"schema_version": "1.0", "project": "p", "author": "me", "zones_in_manual_mode": [ZONE], "inherit_logic": false,
            "tasks": [{"id": "M0001", "step": "CIV-EARTH-CUT", "zone_id": ZONE, "elements": ["FOOT0"], "after": ["M0002"]}],
            "overrides": [{"element_guid": "FOOT0", "suppress_steps": ["STR-FOOT-POUR"]}],
            "applied_recipes": [{"recipe": "rec_duct", "zone_id": ZONE, "element_guid": null}]}
    (d["elements"][0] as Dictionary)["visual_kit"] = "rack"
    (d["elements"][0] as Dictionary)["member_guids"] = ["m1", "m2"]
    (d["elements"][1] as Dictionary)["visual_kit"] = null
    var b := SequenceBundle.from_dictionary(d)
    ok(b.valid, "valid: %s" % ", ".join(b.errors))
    eq(b.recipes.size(), 5, "recipes parsed")
    var r: RecipeData = b.recipes_by_id["rec_footing_column"]
    eq(r.name, "Footing and column", "name")
    eq(r.summary, "Survey, pour, cure, column.", "summary")
    eq(r.sector, "healthcare", "sector")
    eq(r.applies_list("ifc_class"), ["IfcFooting"] as Array[String], "applies_to ifc_class")
    eq(r.steps.size(), 4, "steps")
    eq(r.steps[0].key, "GEN-SURVEY-SETOUT", "key defaults to ref")
    ok(r.steps[0].is_virtual, "virtual step")
    eq(r.steps[0].duration_days, 2, "duration")
    eq(r.steps[0].marker, "survey", "marker")
    eq(r.steps[1].hold_point, "structural", "hold point")
    eq(r.steps[2].from_element, "host", "from_element")
    eq(r.steps[1].from_element, "self", "from_element default")
    ok(r.steps[3].optional, "optional")
    eq(r.steps[3].key, "hydro", "explicit key")
    eq(r.logic.size(), 1, "logic")
    eq(r.logic[0]["lag_days"], 7, "logic lag")
    eq(r.logic[0]["type"], "FS", "logic type default")
    eq(r.prerequisites["permits"], ["hot_work"], "prerequisites")
    eq(r.checks.size(), 1, "checks")
    eq(r.typical_duration_weeks, [8.0, 14.0] as Array[float], "typical duration")
    ok(b.manual != null, "manual block parsed")
    eq(b.manual.zones_in_manual_mode, [ZONE] as Array[String], "zones in manual mode")
    eq(b.manual.tasks.size(), 1, "manual tasks")
    eq(b.manual.tasks[0]["after"], ["M0002"], "manual task links")
    eq(b.manual.overrides.size(), 1, "overrides")
    eq(b.manual.applied_recipes[0]["recipe"], "rec_duct", "applied recipes")
    ok(not b.manual.inherit_logic, "inherit_logic")
    eq(b.elements[0].visual_kit, "rack", "element visual_kit")
    eq(b.elements[0].member_guids, ["m1", "m2"] as Array[String], "element member_guids")
    eq(b.elements[1].visual_kit, "", "null visual_kit")
    eq(b.elements[2].member_guids.size(), 0, "absent member_guids")
    var gs := SimState.new()
    ok(gs.start(b), "starts")
    ok(gs.manual_zones.has(ZONE), "the bundle's manual zone is in manual mode at start")
    # nothing is synthesised when recipes are absent
    var plain: SequenceBundle = load_bundle()
    eq(plain.recipes.size(), 0, "no recipes in the minimal bundle")
    ok(plain.manual == null, "no manual block")
    eq(plain.elements[0].visual_kit, "", "element without a kit")


func test_matcher_any_key_semantics() -> void:
    var gs: SimState = _state()
    var foot: Dictionary = LogicLib.explain_element(gs, "FOOT0")
    eq(_ids_of(foot), ["rec_footing_column"], "footing: ifc_class")
    eq(_entry(foot, "rec_footing_column")["matched_by"], ["ifc_class"], "matched by ifc_class")
    var col: Dictionary = LogicLib.explain_element(gs, "COL00")
    ok(col["none"] and (col["recipes"] as Array).is_empty(), "column: no recipe -> none")
    eq(_ids_of(LogicLib.explain_element(gs, "DUCT1")), ["rec_duct"], "duct: keywords against name / class")
    eq(_ids_of(LogicLib.explain_element(gs, "WALL1")), ["rec_nested", "rec_wall_any"], "wall: ifc_class alone is enough (ANY), keyword 'partition'")
    var slab: Dictionary = LogicLib.explain_element(gs, "SLAB1")
    eq(_ids_of(slab), ["rec_occupied"], "slab: zone tag of its zone")
    eq(_entry(slab, "rec_occupied")["matched_by"], ["zone_tags_any"], "matched by the zone tag")
    ok(LogicLib.explain_element(gs, "NOPE").is_empty(), "unknown element")
    var z: Dictionary = LogicLib.explain_zone(gs, ZONE)
    eq(_ids_of(z), ["rec_duct", "rec_footing_column", "rec_nested", "rec_wall_any"], "ground zone recipes")
    eq(_entry(z, "rec_footing_column")["matching_elements"], 4, "four footings match")
    eq(_ids_of(LogicLib.explain_zone(gs, "L01-Z1")), ["rec_occupied"], "level 1 zone by tag")
    ok(LogicLib.explain_zone(gs, "NOPE").is_empty(), "unknown zone")
    var by_sector: Array[Dictionary] = LogicLib.list_recipes(gs, "healthcare")
    eq(by_sector.size(), 5, "list by sector includes 'all'")
    eq(LogicLib.list_recipes(gs, "civil").size(), 1, "only the 'all' recipe for civil")
    eq(LogicLib.get_recipe(gs, "rec_duct")["id"], "rec_duct", "get")
    ok(LogicLib.get_recipe(gs, "rec_nope").is_empty(), "get unknown")


func test_explain_statuses() -> void:
    var gs: SimState = _state()
    var e: Dictionary = _entry(LogicLib.explain_element(gs, "FOOT0"), "rec_footing_column")
    var survey: Dictionary = _row(e, "GEN-SURVEY-SETOUT")
    eq(survey["status"], "missing", "survey missing")
    ok(survey["virtual"], "survey is virtual")
    eq(survey["duration_days"], 2, "duration shown")
    var pour: Dictionary = _row(e, "STR-FOOT-POUR")
    eq(pour["status"], "covered", "footing pour covered")
    eq(pour["task_id"], "T000001", "by the generated task")
    ok(not pour["virtual"], "not virtual")
    eq(pour["hold_point"], "structural", "hold point shown")
    var col: Dictionary = _row(e, "STR-COL-POUR")
    eq(col["status"], "covered", "column pour covered through the host element")
    eq(col["task_id"], "T000005", "task of the column above")
    var hydro: Dictionary = _row(e, "hydro")
    eq(hydro["status"], "missing", "optional hydrotest missing")
    ok(hydro["optional"], "optional flag")
    eq(e["coverage"]["covered"], 2, "coverage covered")
    eq(e["coverage"]["missing"], 1, "coverage missing (optional steps do not count)")
    eq(e["coverage"]["total"], 3, "coverage total")
    ok(LogicLib.explain_lines(LogicLib.explain_element(gs, "FOOT0")).size() >= 5, "text rows for the inspector")
    ok(LogicLib.explain_lines(LogicLib.explain_element(gs, "COL00"))[0].contains("No recipe"), "none rows")
    # in a manual zone the generated tasks are frozen and no longer cover a step
    Manual.set_mode(gs, ZONE, true)
    var e2: Dictionary = _entry(LogicLib.explain_element(gs, "FOOT0"), "rec_footing_column")
    var pour2: Dictionary = _row(e2, "STR-FOOT-POUR")
    eq(pour2["status"], "missing", "frozen task does not cover")
    eq(pour2["frozen_task_ids"], ["T000001"], "frozen tasks listed")
    # nested recipes are expanded with prefixed keys
    var n: Dictionary = _entry(LogicLib.explain_element(gs, "WALL1"), "rec_nested")
    eq(n["steps"].size(), 3, "nested steps expanded")
    eq(_row(n, "rec_duct/MEP-DUCT-INSTALL")["status"], "missing", "nested step bound to the wall")


func test_apply_recipe_creates_and_reuses() -> void:
    var gs: SimState = _state()
    var n_before: int = gs.bundle.tasks.size()
    var res: Dictionary = Manual.apply_recipe(gs, "rec_footing_column", ZONE, "FOOT0")
    ok(not res.is_empty(), "apply: %s" % gs.last_error)
    eq((res["created"] as Array).size(), 1, "only the virtual survey is created")
    ok((res["reused"] as Array).has("T000001"), "footing pour task reused")
    ok((res["reused"] as Array).has("T000005"), "column pour task reused")
    eq(gs.bundle.tasks.size(), n_before + 1, "one task added")
    var sid: String = (res["created"] as Array)[0]
    var s: TaskData = gs.bundle.tasks_by_id[sid]
    ok(s.is_virtual, "virtual")
    eq(s.origin, "recipe", "origin recipe")
    eq(s.recipe_id, "rec_footing_column", "recipe id")
    eq(s.marker, "survey", "marker")
    eq(s.duration_days, 2, "duration")
    eq(s.zone_id, ZONE, "zone")
    eq(s.element_guid, "", "no element")
    var foot: TaskData = gs.bundle.tasks_by_id["T000001"]
    var has_survey: bool = false
    for p in foot.predecessors:
        if p["task_id"] == sid:
            has_survey = true
            eq(p["type"], "FS", "survey -> pour is FS")
    ok(has_survey, "footing pour now follows the survey")
    var col: TaskData = gs.bundle.tasks_by_id["T000005"]
    var lag: int = -1
    for p in col.predecessors:
        if p["task_id"] == "T000001":
            lag = p["lag_days"]
    eq(lag, 7, "cure lag from the recipe logic replaced the 0 day link")
    eq(state_of(gs, "T000001"), RS.BLOCKED, "footing waits for the survey now")
    eq(gs.manual_applied.size(), 1, "recorded")
    var after: Dictionary = _entry(LogicLib.explain_element(gs, "FOOT0"), "rec_footing_column")
    eq(_row(after, "GEN-SURVEY-SETOUT")["status"], "virtual_present", "survey now virtual_present")
    eq(_row(after, "GEN-SURVEY-SETOUT")["task_id"], sid, "with its task id")
    # idempotent: a second apply reuses everything
    var again: Dictionary = Manual.apply_recipe(gs, "rec_footing_column", ZONE, "FOOT0")
    eq((again["created"] as Array).size(), 0, "nothing new the second time")
    eq(again["links"], 0, "no new links")
    eq(gs.bundle.tasks.size(), n_before + 1, "still one task added")
    # optional steps on request: the hydrotest is an inspection (hold point) task after the column pour
    var opt: Dictionary = Manual.apply_recipe(gs, "rec_footing_column", ZONE, "FOOT0", true)
    eq((opt["created"] as Array).size(), 1, "hydrotest created")
    var h: TaskData = gs.bundle.tasks_by_id[(opt["created"] as Array)[0]]
    ok(h.is_virtual and h.inspection and h.inspection_type == "pressure_test", "hold point makes it an inspection")
    eq(h.duration_days, 3, "hydrotest duration")
    eq(h.predecessors[0]["task_id"], "T000005", "after the column pour")
    # the export carries the recipe tasks and the record
    var doc: Dictionary = Manual.export_doc(gs)
    eq((doc["tasks"] as Array).size(), 2, "survey and hydrotest exported as manual tasks")
    eq((doc["applied_recipes"] as Array).size(), 3, "applied recipes exported")
    eq((doc["applied_recipes"] as Array)[0]["recipe"], "rec_footing_column", "recipe id exported")
    eq((doc["tasks"] as Array)[0]["recipe_id"], "rec_footing_column", "task recipe id exported")


func test_applied_recipe_is_played_with_its_logic() -> void:
    var gs: SimState = _state()
    var res: Dictionary = Manual.apply_recipe(gs, "rec_footing_column", ZONE, "FOOT0")
    var sid: String = (res["created"] as Array)[0]
    Planner.auto_layout(gs, 4)
    Planner.autopilot(gs, 5, "ideal", 1.0, true, 8, true)
    var s: TaskRuntime = gs.runtime[sid]
    var foot: TaskRuntime = gs.runtime["T000001"]
    var col: TaskRuntime = gs.runtime["T000005"]
    ok(TaskRuntime.is_finished(s.state), "survey done")
    ok(foot.actual_start_day >= s.actual_finish_day, "footing after the survey (%d >= %d)" % [foot.actual_start_day, s.actual_finish_day])
    ok(TaskRuntime.is_finished(foot.state), "footing done")
    ok(col.actual_start_day >= foot.actual_finish_day + 7, "column after the 7 day cure (%d >= %d + 7)" % [col.actual_start_day, foot.actual_finish_day])


func test_apply_to_a_zone_and_errors() -> void:
    var gs: SimState = _state()
    var z: Dictionary = Manual.apply_recipe(gs, "rec_occupied", "L01-Z1")
    eq((z["created"] as Array).size(), 1, "zone-level recipe creates its virtual task")
    eq(gs.bundle.tasks_by_id[(z["created"] as Array)[0]].zone_id, "L01-Z1", "in the zone")
    var all: Dictionary = Manual.apply_recipe(gs, "rec_footing_column", ZONE)
    ok(not all.is_empty(), "apply to the zone: %s" % gs.last_error)
    eq((all["created"] as Array).size(), 1, "one shared virtual survey for the four footings")
    ok((all["reused"] as Array).has("T000004"), "every footing task reused")
    ok(int(all["links"]) > 0, "links added")
    ok(Manual.apply_recipe(gs, "rec_nope", ZONE).is_empty(), "unknown recipe")
    ok(gs.last_error.contains("unknown recipe"), gs.last_error)
    ok(Manual.apply_recipe(gs, "rec_duct", "NOZONE").is_empty(), "unknown zone")
    ok(Manual.apply_recipe(gs, "rec_duct", ZONE, "NOPE").is_empty(), "unknown element")
    # steps of the library that do not exist are reported, the others are still applied
    gs.bundle.recipes_by_id["rec_broken"] = RecipeData.from_dict({"id": "rec_broken", "name": "Broken", "sector": "all",
            "applies_to": {}, "steps": [{"ref": "XXX-MISSING", "virtual": true}, {"ref": "GEN-SURVEY-ASBUILT", "virtual": true, "duration_days": 1}]})
    var br: Dictionary = Manual.apply_recipe(gs, "rec_broken", ZONE)
    eq(br["steps"][0]["status"], "error", "unknown step reported")
    eq((br["created"] as Array).size(), 1, "the valid step is applied")
    # nested recipe applied to the wall: nested steps bound to the wall, logic with prefixed keys
    var nested: Dictionary = Manual.apply_recipe(gs, "rec_nested", ZONE, "WALL1")
    ok(not nested.is_empty(), "nested apply: %s" % gs.last_error)
    eq((nested["created"] as Array).size() + (nested["reused"] as Array).size(), 3, "nested steps expanded")


func test_library_loader_is_tolerant() -> void:
    eq(LogicLib.load_library("res://no_such_logic_dir").size(), 0, "missing folder -> empty")
    var root: String = "user://logic_test"
    DirAccess.make_dir_recursive_absolute(root + "/recipes/industrial")
    var f := FileAccess.open(root + "/recipes/industrial/rec_x.json", FileAccess.WRITE)
    f.store_string(JSON.stringify({"schema_version": "1.0", "id": "rec_x", "name": "X", "sector": "industrial", "applies_to": {}, "steps": [{"ref": "GEN-SURVEY-SETOUT", "virtual": true}]}))
    f.close()
    var bad := FileAccess.open(root + "/recipes/industrial/bad.json", FileAccess.WRITE)
    bad.store_string("{not json")
    bad.close()
    var idx := FileAccess.open(root + "/index.json", FileAccess.WRITE)
    idx.store_string("{}")
    idx.close()
    var lib: Array[RecipeData] = LogicLib.load_library(root)
    eq(lib.size(), 1, "one valid recipe, the broken file is skipped")
    eq(lib[0].id, "rec_x", "recipe id")
    # a bundle without recipes uses the library fallback (nothing here when res://logic is absent)
    var gs: SimState = new_state()
    ok(gs.recipe_list() is Array, "fallback list is an array")
    ok(gs.recipe_by_id("rec_nope") == null, "unknown recipe -> null")
