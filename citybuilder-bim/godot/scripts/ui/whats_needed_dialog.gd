class_name WhatsNeededDialog
extends AcceptDialog
## "What's needed?" (docs/06 A.2): for a zone or one element, the construction logic recipes that apply, their
## prerequisites (equipment, site, permits, information) and for the chosen recipe a table of steps with status
## chips: covered (green, task id), virtual present (blue, task id), missing (grey), optional steps in italics.
## "Add missing" runs Manual.apply_recipe (the same call as the API's manual.apply_recipe); "Open rationale" shows the
## recipe's summary, logic, checks and references. Data comes from LogicLib.explain_zone / explain_element.

signal applied(result: Dictionary)
signal message(text: String)

const STATUS_LABEL: Dictionary = {"covered": "covered", "virtual_present": "virtual", "missing": "missing"}
const PREREQ_ORDER: Array[String] = ["equipment", "site", "permits", "information"]
const CHIP_GOOD: Color = Color(0.2, 0.55, 0.3, 0.55)
const CHIP_VIRTUAL: Color = Color(0.2, 0.4, 0.8, 0.55)
const CHIP_MISSING: Color = Color(0.4, 0.42, 0.48, 0.55)

var gs: SimState = null
var zone_id: String = ""
var element_guid: String = ""
## Last LogicLib.explain_* result shown.
var explain: Dictionary = {}
## Index into explain["recipes"] of the recipe whose steps are listed (-1 = none).
var selected: int = -1
## Result of the last "Add missing".
var last_result: Dictionary = {}

var _recipes: ItemList
var _heading: Label
var _info: Label
var _empty: Label
var _detail: VBoxContainer
var _title: Label
var _summary: Label
var _meta: Label
var _prereq: Label
var _steps: Tree
var _optional: CheckBox
var _add: Button
var _rationale_btn: Button
var _rationale: RichTextLabel
var _italic: FontVariation = null


func setup(state: SimState) -> void:
    gs = state
    title = "What's needed?"
    ok_button_text = "Close"
    min_size = Vector2i(780, 440)
    exclusive = false
    var root := VBoxContainer.new()
    root.custom_minimum_size = Vector2(760, 360)
    add_child(root)
    _heading = UiStyle.title("What's needed?")
    root.add_child(_heading)
    _info = UiStyle.label("", 13, UiStyle.MUTED)
    _info.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
    _info.custom_minimum_size.x = 240  # an autowrap label with no width would report a huge height
    root.add_child(_info)
    _empty = UiStyle.label("No recipes available", 15, UiStyle.MUTED)
    _empty.visible = false
    root.add_child(_empty)
    var split := HSplitContainer.new()
    split.size_flags_vertical = Control.SIZE_EXPAND_FILL
    split.name = "Split"
    root.add_child(split)
    _recipes = ItemList.new()
    _recipes.custom_minimum_size = Vector2(210, 0)
    _recipes.select_mode = ItemList.SELECT_SINGLE
    _recipes.item_selected.connect(func(i: int) -> void: select_recipe_index(i))
    split.add_child(_recipes)
    _detail = VBoxContainer.new()
    _detail.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    split.add_child(_detail)
    _title = UiStyle.label("", 17, UiStyle.TEXT)
    _detail.add_child(_title)
    _summary = UiStyle.label("", 13, UiStyle.MUTED)
    _summary.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
    _summary.custom_minimum_size.x = 240  # an autowrap label with no width would report a huge height
    _detail.add_child(_summary)
    _meta = UiStyle.label("", 13, UiStyle.TEXT)
    _detail.add_child(_meta)
    _prereq = UiStyle.label("", 13, UiStyle.MUTED)
    _prereq.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
    _prereq.custom_minimum_size.x = 240  # an autowrap label with no width would report a huge height
    _detail.add_child(_prereq)
    _steps = Tree.new()
    _steps.columns = 4
    _steps.column_titles_visible = true
    _steps.hide_root = true
    _steps.select_mode = Tree.SELECT_ROW
    _steps.size_flags_vertical = Control.SIZE_EXPAND_FILL
    _steps.custom_minimum_size = Vector2(0, 150)
    _steps.set_column_title(0, "Step")
    _steps.set_column_title(1, "Status")
    _steps.set_column_title(2, "Task")
    _steps.set_column_title(3, "Note")
    _steps.set_column_expand(0, true)
    _steps.set_column_custom_minimum_width(0, 230)
    _steps.set_column_expand(1, false)
    _steps.set_column_custom_minimum_width(1, 90)
    _steps.set_column_expand(2, false)
    _steps.set_column_custom_minimum_width(2, 90)
    _steps.set_column_expand(3, true)
    _detail.add_child(_steps)
    var row := HBoxContainer.new()
    _optional = CheckBox.new()
    _optional.text = "Include optional steps"
    _optional.focus_mode = Control.FOCUS_NONE
    _optional.tooltip_text = "Also create the optional steps of the recipe when adding the missing ones"
    _optional.toggled.connect(func(_on: bool) -> void: _fill_steps())
    row.add_child(_optional)
    var fill := Control.new()
    fill.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    row.add_child(fill)
    _rationale_btn = UiStyle.button("Open rationale", "Summary, logic, checks and references of the recipe")
    _rationale_btn.pressed.connect(func() -> void: toggle_rationale())
    row.add_child(_rationale_btn)
    _add = UiStyle.button("Add missing", "Create the missing steps (virtual tasks and bound tasks) and link them")
    _add.pressed.connect(func() -> void: add_missing())
    row.add_child(_add)
    _detail.add_child(row)
    _rationale = RichTextLabel.new()
    _rationale.bbcode_enabled = true
    _rationale.scroll_active = true
    _rationale.fit_content = false
    _rationale.custom_minimum_size = Vector2(0, 130)
    _rationale.visible = false
    _detail.add_child(_rationale)
    var f := FontVariation.new()
    f.base_font = UiStyle.font()
    f.variation_transform = Transform2D(Vector2(1.0, 0.0), Vector2(-0.22, 1.0), Vector2.ZERO)
    _italic = f
    gs.manual_changed.connect(func() -> void: _refresh_if_open())
    gs.tasks_changed.connect(func() -> void: _refresh_if_open())
    refresh()


func _refresh_if_open() -> void:
    if visible:
        refresh()


# ------------------------------------------------------------------ opening

## Opens the dialog for a zone (recipes matched by the zone tags or by any element of the zone).
func open_for_zone(id: String) -> void:
    zone_id = id
    element_guid = ""
    selected = -1
    _rationale.visible = false
    refresh()
    _show()


## Opens the dialog for one element (recipes that apply to it, steps bound to it).
func open_for_element(guid: String) -> void:
    element_guid = guid
    var e: ElementData = gs.bundle.elements_by_guid.get(guid, null)
    zone_id = e.zone_id if e != null else ""
    selected = -1
    _rationale.visible = false
    refresh()
    _show()


func _show() -> void:
    if is_inside_tree():
        # fits a 1280x720 screen: the dialog takes at most 92 % of it
        popup_centered_clamped(Vector2i(900, 640), 0.92)


## Rebuilds the recipe list and the step table from LogicLib (keeps the selected recipe when it is still listed).
func refresh() -> void:
    if gs == null or gs.bundle == null:
        return
    var keep: String = recipe_id()
    if element_guid != "" and gs.bundle.elements_by_guid.has(element_guid):
        explain = LogicLib.explain_element(gs, element_guid)
        var e: ElementData = gs.bundle.elements_by_guid[element_guid]
        _heading.text = "What's needed? - %s" % e.name
        _info.text = "%s in zone %s" % [e.ifc_class, e.zone_id]
    elif zone_id != "" and gs.bundle.zones_by_id.has(zone_id):
        explain = LogicLib.explain_zone(gs, zone_id)
        var z: ZoneData = gs.bundle.zones_by_id[zone_id]
        _heading.text = "What's needed? - %s" % z.name
        _info.text = "Zone %s%s" % [z.id, " (manual mode)" if gs.manual_zones.has(z.id) else ""]
    else:
        explain = {}
        _heading.text = "What's needed?"
        _info.text = "Select a zone or an element."
    _recipes.clear()
    var list: Array = recipes()
    var no_library: bool = gs.recipe_list().is_empty()
    _empty.visible = list.is_empty()
    _empty.text = "No recipes available" if no_library else "No recipe applies here."
    _recipes.visible = not list.is_empty()
    _detail.visible = not list.is_empty()
    var pick: int = -1
    for i in list.size():
        var r: Dictionary = list[i]
        var cov: Dictionary = r["coverage"]
        _recipes.add_item("%s  (%d / %d)" % [r["name"], int(cov["covered"]) + int(cov["virtual_present"]), int(cov["total"])])
        if str(r["recipe_id"]) == keep:
            pick = i
    if pick < 0 and not list.is_empty():
        pick = 0
    selected = -1
    if pick >= 0:
        _recipes.select(pick)
        select_recipe_index(pick)
    else:
        _fill_steps()


## Recipes of the last explain result (Array of recipe entries).
func recipes() -> Array:
    return explain.get("recipes", []) as Array


func recipe_id() -> String:
    var list: Array = recipes()
    if selected < 0 or selected >= list.size():
        return ""
    return str((list[selected] as Dictionary)["recipe_id"])


func select_recipe_index(i: int) -> void:
    var list: Array = recipes()
    if i < 0 or i >= list.size():
        return
    selected = i
    if _recipes.item_count > i and not _recipes.is_selected(i):
        _recipes.select(i)
    var r: Dictionary = list[i]
    _title.text = str(r["name"])
    _summary.text = str(r["summary"])
    var rec: RecipeData = gs.recipe_by_id(str(r["recipe_id"]))
    var meta: String = "Sector: %s" % str(r["sector"])
    if rec != null and rec.typical_duration_weeks.size() >= 2:
        meta += "   |   typically %s to %s weeks" % [_num(rec.typical_duration_weeks[0]), _num(rec.typical_duration_weeks[1])]
    elif rec != null and rec.typical_duration_weeks.size() == 1:
        meta += "   |   typically %s weeks" % _num(rec.typical_duration_weeks[0])
    meta += "   |   matched by: %s" % ", ".join(r["matched_by"])
    _meta.text = meta
    _prereq.text = prerequisites_text(r["prerequisites"])
    _fill_steps()
    if _rationale.visible:
        _rationale.text = rationale_text()


func select_recipe(id: String) -> bool:
    var list: Array = recipes()
    for i in list.size():
        if str((list[i] as Dictionary)["recipe_id"]) == id:
            select_recipe_index(i)
            return true
    return false


## "Equipment: a, b | Site: ..." lines of a recipe's prerequisites ("No prerequisites" when empty).
static func prerequisites_text(pre: Dictionary) -> String:
    var lines: Array[String] = []
    var keys: Array[String] = []
    for k in PREREQ_ORDER:
        if pre.has(k):
            keys.append(k)
    for k in pre:
        if not keys.has(str(k)):
            keys.append(str(k))
    for k in keys:
        var v: Variant = pre[k]
        var items: Array[String] = []
        if v is Array:
            for x in v:
                items.append(str(x))
        elif v != null:
            items.append(str(v))
        if not items.is_empty():
            lines.append("%s: %s" % [k.capitalize(), ", ".join(items)])
    if lines.is_empty():
        return "No prerequisites"
    return "\n".join(lines)


func _fill_steps() -> void:
    _steps.clear()
    var root: TreeItem = _steps.create_item()
    var list: Array = recipes()
    if selected < 0 or selected >= list.size():
        _add.disabled = true
        _rationale_btn.disabled = true
        return
    _rationale_btn.disabled = false
    var entry: Dictionary = list[selected]
    for row in entry["steps"]:
        var rd: Dictionary = row
        var status: String = str(rd["status"])
        var it: TreeItem = _steps.create_item(root)
        var nm: String = str(rd["name"])
        if bool(rd["virtual"]):
            nm += "  (virtual)"
        if rd["hold_point"] != null:
            nm += "  [hold: %s]" % str(rd["hold_point"])
        it.set_text(0, nm)
        it.set_text(1, str(STATUS_LABEL.get(status, status)))
        it.set_text_alignment(1, HORIZONTAL_ALIGNMENT_CENTER)
        var col: Color = chip_color(status)
        it.set_custom_bg_color(1, chip_background(status))
        it.set_custom_color(1, col)
        var tid: String = ""
        if rd["task_id"] != null:
            tid = str(rd["task_id"])
            if (rd["task_ids"] as Array).size() > 1:
                tid += " +%d" % ((rd["task_ids"] as Array).size() - 1)
        elif rd.has("frozen_task_ids"):
            tid = "frozen"
        it.set_text(2, tid)
        var note: String = str(rd["note"])
        if bool(rd["optional"]):
            note = ("optional. " + note).strip_edges()
        if rd.has("duration_days"):
            note = ("%d d. " % int(rd["duration_days"]) + note).strip_edges()
        it.set_text(3, note)
        it.set_metadata(0, rd)
        if bool(rd["optional"]):
            for c in 4:
                it.set_custom_font(c, _italic)
            it.set_custom_color(0, UiStyle.MUTED)
    _add.disabled = missing_count(_optional.button_pressed) == 0
    _add.text = "Add missing (%d)" % missing_count(_optional.button_pressed)


static func chip_color(status: String) -> Color:
    match status:
        "covered":
            return UiStyle.GOOD
        "virtual_present":
            return Color(0.55, 0.78, 1.0)
    return UiStyle.MUTED


static func chip_background(status: String) -> Color:
    match status:
        "covered":
            return CHIP_GOOD
        "virtual_present":
            return CHIP_VIRTUAL
    return CHIP_MISSING


## Steps of the selected recipe that are missing (optional ones only with `with_optional`).
func missing_count(with_optional: bool = false) -> int:
    var list: Array = recipes()
    if selected < 0 or selected >= list.size():
        return 0
    var n: int = 0
    for row in (list[selected] as Dictionary)["steps"]:
        var rd: Dictionary = row
        if str(rd["status"]) != "missing":
            continue
        if bool(rd["optional"]) and not with_optional:
            continue
        n += 1
    return n


## Status chip texts of the step rows in order (for the tests and tooltips).
func step_statuses() -> Array[String]:
    var out: Array[String] = []
    var root: TreeItem = _steps.get_root()
    if root == null:
        return out
    for it in root.get_children():
        out.append(it.get_text(1))
    return out


func step_rows() -> Array[Dictionary]:
    var out: Array[Dictionary] = []
    var list: Array = recipes()
    if selected < 0 or selected >= list.size():
        return out
    for row in (list[selected] as Dictionary)["steps"]:
        out.append(row)
    return out


func set_include_optional(on: bool) -> void:
    _optional.button_pressed = on
    _fill_steps()


# ------------------------------------------------------------------ actions

## Creates the missing steps of the selected recipe (Manual.apply_recipe). Returns the apply result ({} on error).
func add_missing() -> Dictionary:
    var rid: String = recipe_id()
    if rid == "":
        return {}
    var res: Dictionary = Manual.apply_recipe(gs, rid, zone_id, element_guid, _optional.button_pressed)
    last_result = res
    if res.is_empty():
        message.emit(gs.last_error)
        return res
    var made: int = (res["created"] as Array).size()
    var reused: int = (res["reused"] as Array).size()
    message.emit("%s: %d task%s added, %d existing linked" % [_title.text, made, "" if made == 1 else "s", reused])
    applied.emit(res)
    refresh()
    return res


func toggle_rationale() -> void:
    _rationale.visible = not _rationale.visible
    _rationale_btn.text = "Hide rationale" if _rationale.visible else "Open rationale"
    if _rationale.visible:
        _rationale.text = rationale_text()


func rationale_visible() -> bool:
    return _rationale.visible


## BBCode text of the selected recipe: summary, logic, checks, references (what the recipe says and why).
func rationale_text() -> String:
    var rec: RecipeData = gs.recipe_by_id(recipe_id())
    if rec == null:
        return "No recipe selected."
    var out: String = "[b]%s[/b]\n" % _esc(rec.name)
    if rec.summary != "":
        out += "%s\n" % _esc(rec.summary)
    if not rec.logic.is_empty():
        out += "\n[b]Ordering logic[/b]\n"
        for l in rec.logic:
            var ld: Dictionary = l
            out += "- %s then %s (%s, lag %d d)%s\n" % [_esc(str(ld["after"])), _esc(str(ld["before"])), str(ld["type"]),
                    int(ld["lag_days"]), (": " + _esc(str(ld["reason"]))) if str(ld["reason"]) != "" else ""]
    if not rec.checks.is_empty():
        out += "\n[b]Checks[/b]\n"
        for c in rec.checks:
            out += "- %s\n" % _esc(c)
    if not rec.references.is_empty():
        out += "\n[b]References[/b]\n"
        for r in rec.references:
            out += "- %s\n" % _esc(r)
    if rec.summary == "" and rec.logic.is_empty() and rec.checks.is_empty() and rec.references.is_empty():
        out += "\nThis recipe carries no rationale text."
    return out


static func _esc(s: String) -> String:
    return s.replace("[", "[lb]")


static func _num(v: float) -> String:
    if absf(v - round(v)) < 0.005:
        return str(int(round(v)))
    return "%.1f" % v
