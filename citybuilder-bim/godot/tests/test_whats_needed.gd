extends TC
## "What's needed?" dialog (docs/06 A.2): recipes of a zone / element, status chips, Add missing, rationale,
## and the no-recipes case. Recipes are injected into a bundle dictionary (pattern of test_logic).

const ZONE: String = "L00-Z1"

const REC_FOOTING: Dictionary = {
    "schema_version": "1.0", "id": "rec_footing_column", "name": "Footing and column", "summary": "Survey, pour, cure, column.",
    "sector": "healthcare", "tags": ["structure"],
    "applies_to": {"ifc_class": ["IfcFooting"]},
    "prerequisites": {"equipment": ["crawler_crane"], "site": ["access_road", "laydown>=3"], "permits": ["hot_work"],
            "information": ["geotechnical report"]},
    "steps": [
        {"ref": "GEN-SURVEY-SETOUT", "virtual": true, "duration_days": 2, "marker": "survey"},
        {"ref": "STR-FOOT-POUR", "hold_point": "structural"},
        {"ref": "STR-COL-POUR", "from_element": "host"},
        {"key": "hydro", "ref": "GEN-HYDROTEST", "virtual": true, "optional": true, "duration_days": 3, "hold_point": "pressure_test", "marker": "test"},
    ],
    "logic": [{"after": "STR-FOOT-POUR", "before": "STR-COL-POUR", "lag_days": 7, "reason": "cure"}],
    "checks": ["settlement survey before hydrotest"], "references": ["API 650 section 7"], "typical_duration_weeks": [8, 14],
}
const REC_DUCT: Dictionary = {
    "schema_version": "1.0", "id": "rec_duct", "name": "Duct run", "sector": "healthcare",
    "applies_to": {"keywords": ["duct"]},
    "steps": [{"ref": "MEP-DUCT-INSTALL"}, {"ref": "CX-AIR-BALANCE"}],
}

var _msgs: Array[String] = []


func _dialog(gs: SimState) -> WhatsNeededDialog:
    var d := WhatsNeededDialog.new()
    d.setup(gs)
    _msgs.clear()
    d.message.connect(func(t: String) -> void: _msgs.append(t))
    return d


func _free(d: WhatsNeededDialog, gs: SimState) -> void:
    d.free()
    gs.free()


func test_statuses_zone_scope() -> void:
    var gs: SimState = fixture_state([REC_FOOTING.duplicate(true), REC_DUCT.duplicate(true)])
    var d: WhatsNeededDialog = _dialog(gs)
    d.open_for_zone(ZONE)
    var ids: Array[String] = []
    for r in d.recipes():
        ids.append(str((r as Dictionary)["recipe_id"]))
    ids.sort()
    eq(ids, ["rec_duct", "rec_footing_column"] as Array[String], "both recipes apply to the ground zone")
    ok(d.select_recipe("rec_footing_column"), "recipe selected")
    ok(not d._empty.visible, "no empty message")
    eq(d.step_statuses(), ["missing", "covered", "covered", "missing"] as Array[String],
            "survey missing, generated pours covered, optional hydrotest missing")
    var rows: Array[Dictionary] = d.step_rows()
    eq(rows.size(), 4, "four step rows")
    ok(rows[1]["task_id"] != null, "covered row carries a task id")
    eq(d._steps.get_root().get_child(1).get_text(2).begins_with("T"), true, "task id shown in the table: " + d._steps.get_root().get_child(1).get_text(2))
    ok(rows[3]["optional"], "optional flag")
    var opt_item: TreeItem = d._steps.get_root().get_child(3)
    ok(opt_item.get_custom_font(0) != null, "optional rows are drawn in italics")
    ok(d._steps.get_root().get_child(1).get_custom_bg_color(1) == WhatsNeededDialog.CHIP_GOOD, "covered chip is green")
    ok(d._steps.get_root().get_child(0).get_custom_bg_color(1) == WhatsNeededDialog.CHIP_MISSING, "missing chip is grey")
    ok(d._steps.get_root().get_child(0).get_text(0).contains("virtual"), "virtual steps are marked")
    ok(d._steps.get_root().get_child(1).get_text(0).contains("hold: structural"), "hold point shown")
    # summary, duration and prerequisites
    eq(d._title.text, "Footing and column", "title")
    ok(d._meta.text.contains("8 to 14 weeks"), "typical duration: " + d._meta.text)
    var pre: String = d._prereq.text
    ok(pre.contains("Equipment: crawler_crane") and pre.contains("Site: access_road, laydown>=3") and pre.contains("Permits: hot_work")
            and pre.contains("Information: geotechnical report"), "prerequisites listed: " + pre)
    eq(WhatsNeededDialog.prerequisites_text({}), "No prerequisites", "empty prerequisites")
    eq(d.missing_count(false), 1, "one required step missing")
    eq(d.missing_count(true), 2, "two with the optional one")
    _free(d, gs)


func test_add_missing_creates_tasks_and_updates_chips() -> void:
    var gs: SimState = fixture_state([REC_FOOTING.duplicate(true)])
    var d: WhatsNeededDialog = _dialog(gs)
    var applied: Array[Dictionary] = []
    d.applied.connect(func(r: Dictionary) -> void: applied.append(r))
    d.open_for_zone(ZONE)
    eq(gs.bundle.virtual_tasks.size(), 0, "no virtual tasks yet")
    var res: Dictionary = d.add_missing()
    ok(not res.is_empty() and bool(res["ok"]), "Manual.apply_recipe ran: " + gs.last_error)
    eq(applied.size(), 1, "applied signal")
    var steps: Array[String] = []
    for t in gs.bundle.virtual_tasks:
        steps.append(t.step_id)
    eq(steps, ["GEN-SURVEY-SETOUT"] as Array[String], "required virtual step created, optional one skipped")
    ok(not _msgs.is_empty() and _msgs.back().contains("added"), "toast text: " + str(_msgs.back()))
    eq(d.step_statuses()[0], "virtual", "survey chip turned virtual present")
    var cell: String = d._steps.get_root().get_child(0).get_text(2)
    ok(cell != "", "virtual row shows its task id: " + cell)
    ok(d._steps.get_root().get_child(0).get_custom_bg_color(1) == WhatsNeededDialog.CHIP_VIRTUAL, "virtual chip is blue")
    eq(d.missing_count(false), 0, "nothing required is missing")
    ok(d._add.disabled, "Add missing disabled when nothing is missing")
    # a second press adds no duplicate
    var again: Dictionary = d.add_missing()
    eq((again["created"] as Array).size(), 0, "nothing new created the second time")
    eq(gs.bundle.virtual_tasks.size(), 1, "still one virtual task")
    # include optional
    d.set_include_optional(true)
    eq(d.missing_count(true), 1, "the optional hydrotest is the only missing step now")
    ok(not d._add.disabled, "enabled with the optional box")
    d.add_missing()
    var after: Array[String] = []
    for t in gs.bundle.virtual_tasks:
        after.append(t.step_id)
    after.sort()
    eq(after, ["GEN-HYDROTEST", "GEN-SURVEY-SETOUT"] as Array[String], "optional step created with the box ticked")
    var hydro: TaskData = null
    for t in gs.bundle.virtual_tasks:
        if t.step_id == "GEN-HYDROTEST":
            hydro = t
    ok(hydro != null and hydro.inspection and hydro.inspection_type == "pressure_test", "hold point became an inspection")
    eq(gs.manual_applied.size(), 3, "each application is recorded")
    _free(d, gs)


func test_element_scope_and_manual_zone() -> void:
    var gs: SimState = fixture_state([REC_FOOTING.duplicate(true)])
    var d: WhatsNeededDialog = _dialog(gs)
    d.open_for_element("FOOT0")
    ok(d._heading.text.contains("Footing F1"), "heading names the element: " + d._heading.text)
    eq(d.zone_id, ZONE, "zone of the element")
    eq(d.recipes().size(), 1, "one recipe for a footing")
    eq(d.step_statuses()[0], "missing", "survey missing")
    var res: Dictionary = d.add_missing()
    eq(res["element_guid"], "FOOT0", "applied for the element")
    eq(d.step_statuses()[0], "virtual", "survey present afterwards")
    # an element nothing applies to
    d.open_for_element("WALL1")
    eq(d.recipes().size(), 0, "no recipe for the wall")
    ok(d._empty.visible and d._empty.text == "No recipe applies here.", "message: " + d._empty.text)
    ok(not d._detail.visible, "details hidden")
    eq(d.add_missing().size(), 0, "nothing to add")
    # a zone in manual mode: the generated tasks are frozen and no longer count
    Manual.set_mode(gs, ZONE, true)
    d.open_for_zone(ZONE)
    d.select_recipe("rec_footing_column")
    var st: Array[String] = d.step_statuses()
    eq(st[1], "missing", "frozen generated pours do not cover the step in manual mode")
    ok(d._steps.get_root().get_child(1).get_text(2) == "frozen", "frozen tasks are flagged")
    _free(d, gs)


func test_rationale_and_selection() -> void:
    var gs: SimState = fixture_state([REC_FOOTING.duplicate(true), REC_DUCT.duplicate(true)])
    var d: WhatsNeededDialog = _dialog(gs)
    d.open_for_zone(ZONE)
    d.select_recipe("rec_footing_column")
    ok(not d.rationale_visible(), "rationale closed at first")
    d.toggle_rationale()
    ok(d.rationale_visible(), "rationale opened")
    var txt: String = d._rationale.text
    ok(txt.contains("Survey, pour, cure, column."), "summary: " + txt)
    ok(txt.contains("settlement survey before hydrotest"), "checks")
    ok(txt.contains("API 650 section 7"), "references")
    ok(txt.contains("cure") and txt.contains("lag 7"), "ordering logic with the reason")
    ok(d._rationale.scroll_active, "scrollable")
    d.select_recipe("rec_duct")
    ok(d._rationale.text.contains("Duct run"), "rationale follows the recipe")
    eq(d.step_statuses(), ["covered", "covered"] as Array[String], "duct steps are covered by the generated duct tasks of the zone")
    # the selection survives a refresh
    d.refresh()
    eq(d.recipe_id(), "rec_duct", "selected recipe kept")
    d.toggle_rationale()
    ok(not d.rationale_visible(), "toggled off")
    # reopening for the zone resets the rationale
    d.open_for_zone(ZONE)
    ok(not d.rationale_visible(), "closed again on reopen")
    _free(d, gs)


func test_no_recipes_message() -> void:
    var gs: SimState = fixture_state()
    ok(gs.recipe_list().is_empty(), "bundle without recipes")
    var d: WhatsNeededDialog = _dialog(gs)
    d.open_for_zone(ZONE)
    ok(d._empty.visible, "message shown")
    eq(d._empty.text, "No recipes available", "No recipes available")
    ok(not d._recipes.visible and not d._detail.visible, "list and details hidden")
    eq(d.add_missing().size(), 0, "Add missing does nothing")
    eq(d.recipe_id(), "", "no recipe selected")
    d.open_for_element("FOOT0")
    eq(d._empty.text, "No recipes available", "also for an element")
    # recipes exist but none applies to the zone
    var gs2: SimState = fixture_state([REC_FOOTING.duplicate(true)])
    var d2: WhatsNeededDialog = _dialog(gs2)
    d2.open_for_zone("L01-Z1")
    eq(d2._empty.text, "No recipe applies here.", "different message when recipes exist")
    _free(d, gs)
    _free(d2, gs2)


func test_refreshes_when_tasks_change_while_open() -> void:
    var gs: SimState = fixture_state([REC_FOOTING.duplicate(true)])
    var d: WhatsNeededDialog = _dialog(gs)
    d.open_for_zone(ZONE)
    d.select_recipe("rec_footing_column")
    eq(d.step_statuses()[0], "missing", "missing")
    # created through the API path while the dialog is closed: the next opening reflects it
    Manual.add_task(gs, {"step": "GEN-SURVEY-SETOUT", "zone_id": ZONE, "virtual": true})
    d.open_for_zone(ZONE)
    d.select_recipe("rec_footing_column")
    eq(d.step_statuses()[0], "virtual", "present after the API created it")
    _free(d, gs)
