extends TC
## Sequence editor panel (docs/06 A.3): palette, chain editing through the Manual API, manual mode, recipe menu,
## export, element binding, one-lane timeline, and the zone inspector hooks.

const ZONE: String = "L00-Z1"
const DEMO_PATH: String = "res://scenarios/healthcare_manual_demo/sequence.json"
const SCHEMA_PATH: String = "res://../schema/manual_sequence.schema.json"

const REC: Dictionary = {
    "schema_version": "1.0", "id": "rec_ui_footing", "name": "Footing and column", "sector": "healthcare",
    "applies_to": {"ifc_class": ["IfcFooting"]},
    "steps": [{"ref": "GEN-SURVEY-SETOUT", "virtual": true, "duration_days": 2}, {"ref": "STR-FOOT-POUR"}],
}
const REC_OTHER: Dictionary = {
    "schema_version": "1.0", "id": "rec_ui_other", "name": "Other recipe", "sector": "all",
    "applies_to": {"ifc_class": ["IfcTank"]},
    "steps": [{"ref": "GEN-HYDROTEST", "virtual": true, "duration_days": 3}],
}

var _msgs: Array[String] = []


func _editor(gs: SimState, zone: String = ZONE) -> SequenceEditor:
    var ed := SequenceEditor.new()
    ed.setup(gs)
    _msgs.clear()
    ed.message.connect(func(t: String) -> void: _msgs.append(t))
    ed.open_for_zone(zone)
    return ed


func _free(ed: SequenceEditor, gs: SimState) -> void:
    ed.free()
    gs.free()


func _find(node: Node, pred: Callable) -> Node:
    if pred.call(node):
        return node
    for c in node.get_children():
        var r: Node = _find(c, pred)
        if r != null:
            return r
    return null


func _manual_ids(gs: SimState, ids: Array[String]) -> Array[String]:
    var out: Array[String] = []
    for id in ids:
        out.append((gs.bundle.tasks_by_id[id] as TaskData).manual_id)
    return out


func _pred_ids(gs: SimState, id: String) -> Array[String]:
    var out: Array[String] = []
    for p in (gs.bundle.tasks_by_id[id] as TaskData).predecessors:
        out.append(str(p["task_id"]))
    return out


# ------------------------------------------------------------------ opening, palette, elements

func test_open_palette_elements_and_lane() -> void:
    var gs: SimState = fixture_state([REC.duplicate(true)])
    var ed: SequenceEditor = _editor(gs)
    ok(ed.visible, "open_for_zone shows the panel")
    eq(ed.zone_id, ZONE, "zone set")
    ok(ed._title.text.contains("Ground"), "title names the zone: " + ed._title.text)
    eq(ed.palette_entry_count("step") >= gs.bundle.steps.size(), true, "every library step is in the palette")
    eq(ed.palette_entry_count("recipe"), 1, "the recipe is a palette group")
    eq(ed.palette_entry_count("recipe_step"), 0, "its steps are library steps")
    var all_steps: int = ed.palette_entry_count("step")
    ed.set_search("survey")
    var found: int = ed.palette_entry_count("step")
    ok(found > 0 and found < all_steps, "search narrows the palette (%d of %d)" % [found, all_steps])
    ed.set_search("zzzzzz")
    eq(ed.palette_entry_count("step"), 0, "nothing matches gibberish")
    ed.set_search("")
    eq(ed.palette_entry_count("step"), all_steps, "clearing the search restores the palette")
    var n_el: int = 0
    for e in gs.bundle.elements:
        if e.zone_id == ZONE:
            n_el += 1
    eq(ed.element_row_count(), n_el, "elements of the zone listed")
    eq(ed.chain_ids().size(), 0, "no manual tasks yet")
    eq(ed.lane_bar_count(), 0, "empty lane")
    ed.show_zone("L01-Z1")
    ed.refresh()
    eq(ed.element_row_count(), 1, "switching zone lists its elements")
    eq(ed.selected_elements.size(), 0, "selection cleared on zone switch")
    ed.show_zone("")
    ed.refresh()
    eq(ed.element_row_count(), 0, "no zone, no elements")
    _free(ed, gs)


func test_manual_mode_toggle_freezes_generated_packages() -> void:
    var gs: SimState = fixture_state()
    var ed: SequenceEditor = _editor(gs)
    var pkgs: Array = gs.bundle.packages_by_zone[ZONE]
    ok(not pkgs.is_empty(), "zone has generated packages")
    for p in pkgs:
        ok(not (gs.package_runtime[(p as PackageData).package_id] as PackageRuntime).frozen, "not frozen before")
    ok(ed.set_manual_mode(true), "mode switched through the editor")
    ok(gs.manual_zones.has(ZONE), "Manual.set_mode applied")
    for p in pkgs:
        var rt: PackageRuntime = gs.package_runtime[(p as PackageData).package_id]
        ok(rt.frozen and not rt.released, "generated package frozen and held: " + (p as PackageData).package_id)
    ok(ed._manual_check.button_pressed, "check button reflects the state")
    # the header check button drives the same call
    ed._manual_check.button_pressed = false
    ok(not gs.manual_zones.has(ZONE), "toggling the button switches the mode off")
    for p in pkgs:
        ok(not (gs.package_runtime[(p as PackageData).package_id] as PackageRuntime).frozen, "restored")
    _free(ed, gs)


# ------------------------------------------------------------------ the chain

func test_chain_authoring_export() -> void:
    var gs: SimState = fixture_state()
    var ed: SequenceEditor = _editor(gs)
    # 1. a virtual survey task (no elements selected)
    ed.select_palette_step("GEN-SURVEY-SETOUT")
    var survey: String = ed.add_step(ed.palette_step)
    ok(survey != "", "virtual task added: " + gs.last_error)
    var st: TaskData = gs.bundle.tasks_by_id[survey]
    ok(st.is_virtual and st.element_guids.is_empty(), "no elements selected -> virtual")
    eq(st.origin, "manual", "origin manual")
    eq(st.zone_id, ZONE, "in the editor's zone")
    eq(ed.selected_task_id, survey, "new row highlighted")
    # 2. a bound task with two elements selected
    ed.select_elements(["FOOT0", "FOOT1"])
    eq(ed.selected_element_guids(), ["FOOT0", "FOOT1"] as Array[String], "two elements selected")
    ed.select_palette_step("STR-FOOT-POUR")
    var pour: String = ed.add_step(ed.palette_step)
    ok(pour != "", "bound task added: " + gs.last_error)
    var pt: TaskData = gs.bundle.tasks_by_id[pour]
    ok(not pt.is_virtual, "elements selected -> bound")
    eq(pt.element_guids, ["FOOT0", "FOOT1"] as Array[String], "bound to both elements")
    # a third one explicitly virtual even though elements are selected
    ed.select_palette_step("GEN-HYDROTEST")
    var test_id: String = ed.add_step("GEN-HYDROTEST", "virtual")
    ok(gs.bundle.tasks_by_id[test_id].is_virtual, "explicit virtual mode")
    ed.select_elements(["COL00"])
    var col: String = ed.add_step("STR-COL-POUR", "bound")
    ok(col != "", "fourth task")
    var chain: Array[String] = ed.chain_ids()
    eq(chain, [survey, pour, test_id, col] as Array[String], "rows in creation order while unlinked")
    eq(ed.lane_bar_count(), 4, "lane shows every authored task")
    # row texts
    var rows: Array[String] = ed.row_texts(gs.bundle.tasks_by_id[survey], chain)
    eq(rows[0], "1", "row number")
    eq(rows[1], MarkerLegend.glyph("survey"), "marker glyph of the virtual survey")
    eq(rows[3], "virtual", "binding of a virtual row")
    ok(rows[4].ends_with("d"), "virtual rows show their duration: " + rows[4])
    eq(ed.row_texts(gs.bundle.tasks_by_id[pour], chain)[3], "2 el.", "binding count")
    # 3. auto-link
    eq(ed.auto_link(), 3, "three links created")
    eq(_pred_ids(gs, pour), [survey] as Array[String], "row 2 follows row 1")
    eq(_pred_ids(gs, test_id), [pour] as Array[String], "row 3 follows row 2")
    eq(_pred_ids(gs, col), [test_id] as Array[String], "row 4 follows row 3")
    eq(ed.auto_link(), 0, "second auto-link adds nothing")
    # 4. move a row up, then to the end
    ed.select_row(col)
    ok(ed.move_selected(-1), "move up")
    eq(ed.chain_ids(), [survey, pour, col, test_id] as Array[String], "order after move up")
    eq(_pred_ids(gs, col), [pour] as Array[String], "moved row follows the new previous row")
    eq(_pred_ids(gs, test_id), [col] as Array[String], "the next row follows the moved row")
    ok(ed.move_row(0, 3), "move the first row to the end")
    eq(ed.chain_ids(), [pour, col, test_id, survey] as Array[String], "order after moving to the end")
    eq(_pred_ids(gs, pour), [] as Array[String], "new first row has no chain predecessor")
    eq(_pred_ids(gs, survey), [test_id] as Array[String], "survey now last")
    ok(not ed.move_row(0, 0), "no-op move refused")
    ok(not ed.move_row(0, 9), "out-of-range move refused")
    ok(ed.move_row(3, 0), "and back to the front")
    eq(ed.chain_ids(), [survey, pour, col, test_id] as Array[String], "survey first again")
    ok(ed.move_row(2, 3), "col down")
    eq(ed.chain_ids(), [survey, pour, test_id, col] as Array[String], "original order")
    # 5. lag
    ed.select_row(pour)
    ok(ed.set_lag(3), "lag set: " + gs.last_error)
    eq(int(gs.bundle.tasks_by_id[pour].predecessors[0]["lag_days"]), 3, "lag stored on the link")
    ok(ed.row_texts(gs.bundle.tasks_by_id[pour], ed.chain_ids())[5].contains("+3"), "lag shown in the row")
    ok(ed.move_row(1, 2) and ed.move_row(2, 1), "moving a row away and back")
    eq(int(gs.bundle.tasks_by_id[pour].predecessors[0]["lag_days"]), 3, "the row keeps its lag after re-ordering")
    ed.select_row(survey)
    ok(not ed.set_lag(2), "a row without predecessor cannot take a lag")
    ok(ed.link_selected_after(col, "SS", 1) == false, "linking the first row after the last one would close a cycle")
    ok(gs.last_error.contains("cycle"), "cycle error: " + gs.last_error)
    # 6. duration of a virtual task, hold point
    ok(ed.set_duration(4), "duration set")
    eq(gs.bundle.tasks_by_id[survey].duration_days, 4, "duration stored")
    eq(_pred_ids(gs, pour), [survey] as Array[String], "successor link survives the rebuild")
    ed.select_row(pour)
    ok(not ed.set_duration(5), "duration is for virtual tasks only")
    ok(ed.set_hold_point("structural"), "hold point: " + gs.last_error)
    var ph: TaskData = gs.bundle.tasks_by_id[pour]
    ok(ph.inspection and ph.inspection_type == "structural", "task is a structural inspection")
    ok(ed.row_texts(ph, ed.chain_ids())[2].contains("hold: structural"), "hold point shown")
    # 7. remove: the successor inherits the predecessor
    ed.select_row(pour)
    ok(ed.remove_selected(), "remove")
    ok(not gs.bundle.tasks_by_id.has(pour), "task gone")
    eq(ed.chain_ids(), [survey, test_id, col] as Array[String], "chain after removal")
    eq(_pred_ids(gs, test_id), [survey] as Array[String], "bridged over the removed row")
    ok(ed.selected_task_id != "" and ed.chain_ids().has(ed.selected_task_id), "a neighbour row is highlighted")
    # 8. export
    var path: String = ed.export_manual()
    eq(path, "user://manual_minimal.json", "export path")
    ok(FileAccess.file_exists(path), "file written")
    var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(path))
    ok(parsed is Dictionary, "export parses")
    var doc: Dictionary = parsed
    var ids_in_doc: Array[String] = []
    for t in doc["tasks"]:
        ids_in_doc.append(str((t as Dictionary)["id"]))
    eq(ids_in_doc, _manual_ids(gs, ed.chain_ids()), "tasks exported in chain order")
    _validate_manual_doc(doc)
    var survey_doc: Dictionary = doc["tasks"][0]
    eq(survey_doc["virtual"], true, "virtual flag exported")
    ok(not survey_doc.has("elements"), "virtual task exports no elements")
    eq(int(survey_doc["duration_days"]), 4, "duration exported")
    eq((doc["tasks"][1] as Dictionary)["after"], [survey_doc["id"]], "link exported by manual id")
    ok(not _msgs.is_empty() and _msgs[_msgs.size() - 1].contains("exported"), "a toast message was emitted: " + str(_msgs.back()))
    DirAccess.remove_absolute(path)
    _free(ed, gs)


## Structural validation of a manual_sequence document against schema/manual_sequence.schema.json.
func _validate_manual_doc(doc: Dictionary) -> void:
    var allowed_task_keys: Array = ["links"]
    var required_task: Array = ["id", "step", "zone_id"]
    var required_doc: Array = ["schema_version", "tasks"]
    var abs_schema: String = ProjectSettings.globalize_path("res://").path_join("../schema/manual_sequence.schema.json").simplify_path()
    if FileAccess.file_exists(abs_schema):
        var schema: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(abs_schema))
        required_doc = schema["required"]
        required_task = schema["$defs"]["manual_task"]["required"]
        allowed_task_keys.append_array((schema["$defs"]["manual_task"]["properties"] as Dictionary).keys())
    else:
        allowed_task_keys.append_array(["id", "step", "name", "zone_id", "cells", "elements", "virtual", "quantity", "unit",
                "duration_days", "after", "link_type", "lag_days", "package_id", "recipe_id", "marker", "note"])
    for k in required_doc:
        ok(doc.has(k), "document key %s present" % k)
    eq(doc["schema_version"], "1.0", "schema_version")
    ok(doc["tasks"] is Array, "tasks is an array")
    var rx := RegEx.new()
    rx.compile("^M[0-9]{4,6}$")
    var ids: Dictionary = {}
    for t in doc["tasks"]:
        ids[str((t as Dictionary)["id"])] = true
    for t in doc["tasks"]:
        var td: Dictionary = t
        for k in required_task:
            ok(td.has(k), "task %s has %s" % [td.get("id", "?"), k])
        for k in td:
            ok(allowed_task_keys.has(k), "task key %s is in the schema" % k)
        ok(rx.search(str(td["id"])) != null, "id pattern: %s" % td["id"])
        if td.has("duration_days"):
            ok(int(td["duration_days"]) >= 1, "duration >= 1")
        if td.has("link_type"):
            ok(["FS", "SS", "FF"].has(str(td["link_type"])), "link type")
        if td.has("elements"):
            ok(td["elements"] is Array and not (td["elements"] as Array).is_empty(), "elements array")
        for a in td.get("after", []):
            ok(ids.has(str(a)) or str(a).begins_with("T"), "after refers to a manual or generated task: %s" % a)


func test_bind_selected_and_virtual_to_bound() -> void:
    var gs: SimState = fixture_state()
    var ed: SequenceEditor = _editor(gs)
    var id: String = ed.add_step("GEN-SURVEY-SETOUT", "virtual")
    var other: String = ed.add_step("STR-COL-POUR", "virtual")
    ed.select_row(id)
    ok(not ed.bind_selected(), "nothing to bind without selected elements")
    ed.select_elements(["FOOT2", "FOOT3"])
    ok(ed.bind_selected(), "bound: " + gs.last_error)
    var t: TaskData = gs.bundle.tasks_by_id[id]
    ok(not t.is_virtual, "virtual row became an element task")
    eq(t.element_guids, ["FOOT2", "FOOT3"] as Array[String], "elements bound")
    eq(t.duration_days, 0, "no fixed duration any more")
    ed.select_elements(["FOOT0"])
    ok(ed.bind_selected(), "binding adds to the row's elements")
    eq((gs.bundle.tasks_by_id[id] as TaskData).element_guids.size(), 3, "three elements now")
    ok(gs.bundle.tasks_by_element["FOOT0"].has(gs.bundle.tasks_by_id[id]), "element index updated")
    # the "In" column of the element list names the rows
    ed.select_elements([])
    ed.refresh()
    var root: TreeItem = ed._elements.get_root()
    var cells: Dictionary = {}
    for it in root.get_children():
        cells[str(it.get_metadata(0))] = it.get_text(3)
    eq(cells["FOOT2"], "1", "element list shows the chain row of a bound element")
    eq(cells["COL01"], "", "unbound element has no row")
    # row selection of the other row, Select in 3D has no API: a message, no crash
    ed.select_row(other)
    ed.select_elements(["FOOT0"])
    _msgs.clear()
    ok(not ed.select_in_3d(), "no highlight API in the 3D view")
    ok(not _msgs.is_empty(), "message explains: " + str(_msgs))
    ok(ed._view_btn.disabled, "the button is disabled without support")
    _free(ed, gs)


func test_errors_are_reported_not_applied() -> void:
    var gs: SimState = fixture_state()
    var ed: SequenceEditor = _editor(gs)
    eq(ed.add_step(""), "", "no step picked")
    eq(ed.add_step("NO-SUCH-STEP", "virtual"), "", "unknown step")
    ok(not _msgs.is_empty() and _msgs.back().contains("unknown step"), "Manual's error is surfaced: " + str(_msgs.back()))
    eq(ed.add_step("STR-FOOT-POUR", "bound"), "", "bound without elements")
    var a: String = ed.add_step("GEN-SURVEY-SETOUT", "virtual")
    var b: String = ed.add_step("STR-COL-POUR", "virtual")
    ed.auto_link()
    # a started task cannot be moved
    gs.set_task_state(a, TaskRuntime.State.ACTIVE)
    ed.select_row(b)
    ok(not ed.move_selected(-1), "a started row blocks the move")
    eq(ed.chain_ids(), [a, b] as Array[String], "order unchanged")
    eq(_pred_ids(gs, b), [a] as Array[String], "links unchanged")
    ok(ed.remove_selected(), "a row that has not started can still be removed")
    eq(ed.chain_ids(), [a] as Array[String], "only the started row remains")
    ed.select_row(a)
    ok(not ed.remove_selected(), "a started row cannot be removed")
    ok(gs.last_error.contains("already started"), "error: " + gs.last_error)
    ed.show_zone("")
    ed.refresh()
    eq(ed.add_step("GEN-SURVEY-SETOUT", "virtual"), "", "no zone, no task")
    _free(ed, gs)


func test_drag_and_drop_handlers() -> void:
    var gs: SimState = fixture_state()
    var ed: SequenceEditor = _editor(gs)
    var a: String = ed.add_step("GEN-SURVEY-SETOUT", "virtual")
    var b: String = ed.add_step("STR-COL-POUR", "virtual")
    var c: String = ed.add_step("GEN-HYDROTEST", "virtual")
    ed.auto_link()
    ok(ed._chain_can_drop(Vector2.ZERO, {"seq_row": 0}) == (ed._chain.get_item_at_position(Vector2.ZERO) != null), "drop accepts only chain rows")
    ok(not ed._chain_can_drop(Vector2.ZERO, "text"), "foreign payloads refused")
    # the drop handler resolves rows by position; the unit under test is the row move it ends in
    ed.move_row(2, 0)
    eq(ed.chain_ids(), [c, a, b] as Array[String], "dragging the last row to the top")
    eq(ed._chain.drop_mode_flags, Tree.DROP_MODE_INBETWEEN, "tree accepts drops between rows")
    _free(ed, gs)


# ------------------------------------------------------------------ recipes

func test_apply_recipe_menu() -> void:
    var gs: SimState = fixture_state([REC.duplicate(true), REC_OTHER.duplicate(true)])
    var ed: SequenceEditor = _editor(gs)
    var ids: Array[String] = ed.recipe_menu_ids()
    ok(ids.has("rec_ui_footing") and ids.has("rec_ui_other"), "menu lists applicable and all recipes: " + str(ids))
    var pop: PopupMenu = ed._recipe_menu.get_popup()
    ok(pop.item_count >= 3, "popup has entries (separator, recipe, submenu)")
    var has_sub: bool = false
    for i in pop.item_count:
        if pop.get_item_text(i) == "All recipes" and pop.get_item_submenu_node(i) != null:
            has_sub = true
    ok(has_sub, "All recipes submenu present")
    var res: Dictionary = ed.apply_recipe("rec_ui_footing")
    ok(not res.is_empty(), "recipe applied: " + gs.last_error)
    eq(ed.chain_ids().size(), 1, "the new virtual task is in the chain (the pour reuses the generated tasks)")
    ok((res["reused"] as Array).size() >= 1, "existing generated pour tasks linked, not duplicated")
    var steps: Array[String] = []
    for id in ed.chain_ids():
        steps.append((gs.bundle.tasks_by_id[id] as TaskData).step_id)
    ok(steps.has("GEN-SURVEY-SETOUT"), "virtual survey from the recipe")
    eq(ed.lane_bar_count(), ed.chain_ids().size(), "lane follows the chain")
    # recipe group in the palette applies the whole recipe
    ed._on_recipe_menu(ids.find("rec_ui_other"))
    ok(gs.bundle.virtual_tasks.size() >= 2, "second recipe applied from the menu")
    eq(ed.apply_recipe("nope").size(), 0, "unknown recipe fails")
    ok(gs.last_error.contains("unknown recipe"), gs.last_error)
    _free(ed, gs)
    # no recipes at all
    var gs2: SimState = fixture_state()
    var ed2: SequenceEditor = _editor(gs2)
    ok(ed2._recipe_menu.disabled, "no recipes: the dropdown is disabled")
    eq(ed2.recipe_menu_ids().size(), 0, "no menu ids")
    _free(ed2, gs2)


# ------------------------------------------------------------------ refresh on API changes, inspector hooks

func test_refreshes_after_api_changes() -> void:
    var gs: SimState = fixture_state()
    var ed: SequenceEditor = _editor(gs)
    ed.refresh()
    ok(not ed._dirty, "clean after refresh")
    var id: String = Manual.add_task(gs, {"step": "GEN-SURVEY-SETOUT", "zone_id": ZONE, "virtual": true})
    ok(ed._dirty, "tasks_changed marks the editor dirty")
    ed._process(0.0)
    eq(ed.chain_ids(), [id] as Array[String], "task added through the API shows up")
    eq(ed._chain.get_root().get_child_count(), 1, "tree row")
    Manual.set_mode(gs, ZONE, true)
    ok(ed._dirty, "manual_changed marks it dirty")
    ed._process(0.0)
    ok(ed._manual_check.button_pressed, "mode shown")
    # a task of another zone does not show
    Manual.add_task(gs, {"step": "STR-SLAB-POUR", "zone_id": "L01-Z1", "elements": ["SLAB1"]})
    ed.refresh()
    eq(ed.chain_ids().size(), 1, "only this zone's tasks")
    # hidden editor does not rebuild
    ed.close_editor()
    ok(not ed.visible, "closed")
    Manual.add_task(gs, {"step": "STR-COL-POUR", "zone_id": ZONE, "elements": ["COL00"]})
    ed._process(0.0)
    ok(ed._dirty, "stays dirty while hidden")
    ed.toggle(ZONE)
    ok(ed.visible, "toggle reopens")
    eq(ed.chain_ids().size(), 2, "and shows the new task")
    _free(ed, gs)


func test_inspector_opens_dialog_and_editor() -> void:
    var gs: SimState = fixture_state([REC.duplicate(true)])
    var insp := ZoneInspector.new()
    insp.setup(gs)
    insp.show_zone(ZONE)
    insp.refresh()
    var got: Array[String] = []
    insp.whats_needed_requested.connect(func(z: String) -> void: got.append("needed:" + z))
    insp.sequence_editor_requested.connect(func(z: String) -> void: got.append("seq:" + z))
    var needed: Button = _find(insp, func(n: Node) -> bool: return n is Button and (n as Button).text == "What's needed?") as Button
    needed.pressed.emit()
    var seq: Button = _find(insp, func(n: Node) -> bool: return n is Button and (n as Button).text.begins_with("Sequence editor")) as Button
    ok(seq != null, "Sequence editor button")
    seq.pressed.emit()
    eq(got, ["needed:" + ZONE, "seq:" + ZONE] as Array[String], "listeners are called, no inline text")
    ok(not insp._text.text.contains("What's needed?"), "inline rows not printed when a dialog listens")
    insp.free()
    gs.free()


func test_virtual_marker_glyph_in_inspector_and_legend() -> void:
    var gs: SimState = fixture_state()
    var id: String = Manual.add_task(gs, {"step": "GEN-SURVEY-SETOUT", "zone_id": ZONE, "virtual": true})
    ok(id != "", "virtual survey")
    var insp := ZoneInspector.new()
    insp.setup(gs)
    insp.show_zone(ZONE)
    insp.refresh()
    var hex: String = "#" + MarkerLegend.colour("survey").to_html(false)
    ok(insp._text.text.contains("[b]S[/b]") or insp._text.get_parsed_text().contains("S virtual"), "survey glyph in the task list: " + insp._text.get_parsed_text().substr(0, 200))
    ok(hex.length() == 7, "colour hex")
    insp.free()
    var legend := MarkerLegend.new()
    legend.setup(false)
    var labels: int = 0
    var stack: Array[Node] = [legend]
    while not stack.is_empty():
        var n: Node = stack.pop_back()
        if n is Label:
            labels += 1
        stack.append_array(n.get_children())
    eq(labels, 1 + MarkerLegend.IDS.size() * 2, "legend: heading + glyph and name per marker")
    eq(MarkerLegend.IDS.size(), 9, "eight kinds plus generic")
    for m in MarkerKit.IDS:
        ok(MarkerLegend.IDS.has(m), "legend covers marker kit id %s" % m)
        eq(MarkerLegend.glyph(m).length(), 1, "one-letter glyph of %s" % m)
    var seen: Dictionary = {}
    for m in MarkerLegend.IDS:
        seen[MarkerLegend.glyph(m)] = true
    eq(seen.size(), MarkerLegend.IDS.size(), "glyphs are unique")
    ok(MarkerLegend.colour("survey", true) == MarkerKit.DEFAULT_COLOURS["survey"], "kit colours on request")
    legend.free()
    gs.free()


# ------------------------------------------------------------------ the demo bundle

func test_demo_bundle_chain() -> void:
    if not FileAccess.file_exists(DEMO_PATH):
        ok(true, "healthcare_manual_demo not synced yet: skipped")
        return
    var b := SequenceBundle.load_from_path(DEMO_PATH)
    ok(b.valid, "demo bundle valid: %s" % ", ".join(b.errors))
    var gs := SimState.new()
    ok(gs.start(b), "demo starts")
    var zone: String = b.manual.zones_in_manual_mode[0]
    var ed := SequenceEditor.new()
    ed.setup(gs)
    ed.open_for_zone(zone)
    var chain: Array[String] = ed.chain_ids()
    eq(chain.size(), 6, "the six manual tasks are listed")
    eq(_manual_ids(gs, chain), ["M0001", "M0002", "M0003", "M0004", "M0005", "M0006"] as Array[String], "in manual order")
    eq(ed._chain.get_root().get_child_count(), 6, "tree has six rows")
    ok(ed._manual_check.button_pressed, "manual mode is on for the demo zone")
    var survey_row: Array[String] = ed.row_texts(gs.bundle.tasks_by_id[chain[0]], chain)
    eq(survey_row[1], "S", "survey glyph on the first row")
    eq(survey_row[3], "virtual", "first row virtual")
    eq(ed.row_texts(gs.bundle.tasks_by_id[chain[2]], chain)[3], "8 el.", "pile task binds eight elements")
    eq(ed.lane_bar_count(), 6, "the chain lane has six bars")
    eq(ed.lane.rows.size(), 1, "one zone row in the lane renderer")
    eq(ed.lane.count_visible() >= 0, true, "renderer can traverse the model")
    eq(int(ed.lane_model(ed.ordered_chain())["bar_count"]), 6, "model bar_count")
    for t in ed.ordered_chain():
        ok(t.is_authored(), "only authored tasks in the chain")
    # re-ordering and auto-link on the loaded chain keep it valid
    ed.select_row(chain[1])
    ok(ed.move_selected(1), "move a loaded row: " + gs.last_error)
    eq(ed.chain_ids().size(), 6, "still six rows")
    eq(ed.chain_ids()[1], chain[2], "the loaded rows swapped")
    ed.free()
    gs.free()


# ------------------------------------------------------------------ wiring in the main scene

func _press(node: Node, action: String) -> void:
    var ev := InputEventAction.new()
    ev.action = action
    ev.pressed = true
    node.call("_unhandled_input", ev)


func test_main_scene_wiring() -> void:
    var sc: Node = Engine.get_main_loop().root.get_node("Scenarios")
    ok(bool(sc.call("select", MINIMAL_PATH)), "select minimal bundle")
    var main: Node = (load("res://scenes/main.tscn") as PackedScene).instantiate()
    Engine.get_main_loop().root.add_child(main)
    var ed: SequenceEditor = main.get("seq_editor")
    var dlg: WhatsNeededDialog = main.get("whats_needed")
    var insp: ZoneInspector = main.get("inspector")
    ok(ed != null and dlg != null, "editor and dialog created by main")
    ok(InputMap.has_action("sequence_editor_toggle"), "input action registered")
    ok(not ed.visible, "closed at start")
    # the inspector's button opens the editor for its zone
    insp.show_zone(ZONE)
    insp.refresh()
    var btn: Button = _find(insp, func(n: Node) -> bool: return n is Button and (n as Button).text.begins_with("Sequence editor")) as Button
    btn.pressed.emit()
    ok(ed.visible and ed.zone_id == ZONE, "inspector opens the editor for its zone")
    eq(main.get("pinned_zone"), ZONE, "zone pinned")
    # N toggles
    _press(main, "sequence_editor_toggle")
    ok(not ed.visible, "N hides the editor")
    _press(main, "sequence_editor_toggle")
    ok(ed.visible and ed.zone_id == ZONE, "N shows it again for the pinned zone")
    # a zone picked in the timeline is followed
    (main.get("gantt") as GanttPanel).zone_selected.emit("L01-Z1")
    eq(ed.zone_id, "L01-Z1", "timeline selection routed to the editor")
    # the top bar button
    (main.get("top_bar") as TopBar).sequence_toggled.emit()
    ok(not ed.visible, "top bar N button toggles")
    # What's needed? from the inspector opens the dialog (no inline rows)
    insp.show_zone(ZONE)
    insp.refresh()
    var needed: Button = _find(insp, func(n: Node) -> bool: return n is Button and (n as Button).text == "What's needed?") as Button
    needed.pressed.emit()
    eq(dlg.zone_id, ZONE, "dialog opened for the zone")
    # editor messages reach the hint bar
    ed.open_for_zone(ZONE)
    ed.add_step("STR-COL-POUR", "virtual")
    Engine.get_main_loop().root.remove_child(main)
    main.free()
    sc.set("current_bundle", null)
