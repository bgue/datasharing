class_name SequenceEditor
extends PanelContainer
## Sequence editor (docs/06 A.3): a right-side panel to author the manual chain of one zone. Toggle with N.
## Header: zone, Manual mode, Apply recipe, What's needed?, Export, Legend. Left: step palette (library steps by
## phase, recipes as groups of steps, search). Middle: the ordered chain (step, binding, quantity / duration, lag,
## predecessors, state) with Add, Move up / down (rows are also draggable), Remove, Auto-link, Link to..., lag,
## duration (virtual tasks) and hold point controls. Right: the elements of the zone (multi-select) with Bind selected.
## Bottom: a one-lane timeline of the chain and the marker legend.
##
## Every action goes through the Manual API (`Manual.add_task / update_task / remove_task / link / unlink /
## apply_recipe / set_mode / export_doc`), the same code path as the API server, so UI and API cannot diverge.
## The chain order is the dependency order of the zone's authored tasks (ties by creation order): moving a row
## re-links its neighbours (FS, keeping each row's lag), so the order shown is the order that is played.

signal message(text: String)
## The "What's needed?" button (zone scope, or element scope with a selected element).
signal whats_needed_requested(zone_id: String, element_guid: String)
signal closed()

const MAX_ELEMENT_ROWS: int = 400
const DEFAULT_HOLD_POINTS: Array[String] = ["structural", "fire_stopping", "pressure_test", "electrical", "commissioning"]
const LINK_TYPES: Array[String] = ["FS", "SS", "FF"]
const COLS: Array[String] = ["#", "M", "Step", "Binding", "Qty / dur", "Link", "After", "State"]

var gs: SimState = null
var zone_id: String = ""
## Optional 3D view: marker colours follow its kit, "Select in 3D" calls its `highlight_elements` when it has one.
var bim_view: BimView = null
var selected_task_id: String = ""
var palette_step: String = ""
var palette_recipe: String = ""
## guid -> true: elements highlighted in the right column.
var selected_elements: Dictionary = {}
var last_export_path: String = ""
var last_export: Dictionary = {}
## The one-lane timeline of the chain.
var lane: GanttRenderer = null

var _dirty: bool = true
var _elements_dirty: bool = true
var _sync: bool = false
var _title: Label
var _manual_check: CheckButton
var _recipe_menu: MenuButton
var _recipe_ids: Array[String] = []
var _menu_zone: String = "?"
var _all_menu: PopupMenu = null
var _needed_btn: Button
var _legend_btn: CheckButton
var _legend: MarkerLegend
var _search: LineEdit
var _palette: Tree
var _chain: Tree
var _chain_ids: Array[String] = []
var _hint: Label
var _add_btn: Button
var _add_menu: PopupMenu
var _up_btn: Button
var _down_btn: Button
var _remove_btn: Button
var _auto_btn: Button
var _link_btn: Button
var _lag_spin: SpinBox
var _lag_btn: Button
var _dur_spin: SpinBox
var _dur_btn: Button
var _hold_opt: OptionButton
var _hold_btn: Button
var _hold_types: Array[String] = []
var _link_popup: PopupPanel
var _link_pred: OptionButton
var _link_type: OptionButton
var _link_lag: SpinBox
var _elem_search: LineEdit
var _elements: Tree
var _bind_btn: Button
var _view_btn: Button
var _elem_needed: Button
var _elem_info: Label


func setup(state: SimState, view: BimView = null) -> void:
    gs = state
    bim_view = view
    custom_minimum_size = Vector2(860, 380)
    visible = false
    var root := VBoxContainer.new()
    add_child(root)
    _build_header(root)
    var body := HBoxContainer.new()
    body.size_flags_vertical = Control.SIZE_EXPAND_FILL
    body.add_theme_constant_override("separation", 8)
    root.add_child(body)
    _build_palette(body)
    _build_chain(body)
    _build_elements(body)
    lane = GanttRenderer.new()
    lane.gs = gs
    lane.show_labels = false
    lane.show_scrollbars = false
    lane.fit_height = true
    lane.allow_expand = false
    lane.allow_menu = false
    lane.axis_h = 14.0
    lane.custom_minimum_size = Vector2(400, 66)
    root.add_child(lane)
    _legend = MarkerLegend.new()
    _legend.visible = false
    root.add_child(_legend)
    _legend.setup(_kit_colours())
    gs.tasks_changed.connect(func() -> void: _dirty = true; _elements_dirty = true)
    gs.manual_changed.connect(func() -> void: _dirty = true)
    gs.task_state_changed.connect(func(_a: String, _b: int, _c: int) -> void: _dirty = true)
    gs.week_advanced.connect(func(_w: int) -> void: _dirty = true; _elements_dirty = true)
    gs.level_started.connect(_on_level_started)
    _rebuild_palette()
    _rebuild_hold_types()
    refresh()


func _on_level_started() -> void:
    zone_id = ""
    selected_task_id = ""
    selected_elements.clear()
    _dirty = true
    _elements_dirty = true
    _rebuild_palette()
    _rebuild_hold_types()


# ------------------------------------------------------------------ construction of the controls

func _build_header(root: VBoxContainer) -> void:
    var head := HBoxContainer.new()
    root.add_child(head)
    _title = UiStyle.title("Sequence editor")
    head.add_child(_title)
    _manual_check = CheckButton.new()
    _manual_check.text = "Manual mode"
    _manual_check.focus_mode = Control.FOCUS_NONE
    _manual_check.tooltip_text = "Freeze the generated packages of this zone and run only the authored chain"
    _manual_check.toggled.connect(func(on: bool) -> void:
        if not _sync:
            set_manual_mode(on))
    head.add_child(_manual_check)
    _recipe_menu = MenuButton.new()
    _recipe_menu.text = "Apply recipe"
    _recipe_menu.focus_mode = Control.FOCUS_NONE
    _recipe_menu.flat = false
    _recipe_menu.tooltip_text = "Expand a construction logic recipe into this zone (missing steps only)"
    _recipe_menu.get_popup().id_pressed.connect(_on_recipe_menu)
    head.add_child(_recipe_menu)
    _needed_btn = UiStyle.button("What's needed?", "Recipes that apply to this zone and the steps still missing")
    _needed_btn.pressed.connect(func() -> void: whats_needed_requested.emit(zone_id, ""))
    head.add_child(_needed_btn)
    var export_btn := UiStyle.button("Export", "Write user://manual_<scenario>.json (manual_sequence schema)")
    export_btn.pressed.connect(func() -> void: export_manual())
    head.add_child(export_btn)
    _legend_btn = CheckButton.new()
    _legend_btn.text = "Legend"
    _legend_btn.focus_mode = Control.FOCUS_NONE
    _legend_btn.tooltip_text = "Marker glyphs and colours of virtual tasks"
    _legend_btn.toggled.connect(func(on: bool) -> void: _legend.visible = on)
    head.add_child(_legend_btn)
    var fill := Control.new()
    fill.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    head.add_child(fill)
    head.add_child(UiStyle.label("N: toggle", 12, UiStyle.MUTED))
    var close := UiStyle.button("x", "Close the sequence editor (N)")
    close.pressed.connect(func() -> void: close_editor())
    head.add_child(close)


func _build_palette(body: HBoxContainer) -> void:
    var col := VBoxContainer.new()
    col.custom_minimum_size = Vector2(230, 0)
    col.size_flags_stretch_ratio = 3.0
    col.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    body.add_child(col)
    col.add_child(UiStyle.label("Step palette", 14, UiStyle.MUTED))
    _search = LineEdit.new()
    _search.placeholder_text = "Search steps and recipes"
    _search.clear_button_enabled = true
    _search.text_changed.connect(func(_t: String) -> void: _rebuild_palette())
    col.add_child(_search)
    _palette = Tree.new()
    _palette.columns = 3
    _palette.hide_root = true
    _palette.select_mode = Tree.SELECT_ROW
    _palette.column_titles_visible = true
    _palette.set_column_title(0, "Step")
    _palette.set_column_title(1, "Trade")
    _palette.set_column_title(2, "F")
    _palette.set_column_expand(1, false)
    _palette.set_column_custom_minimum_width(1, 70)
    _palette.set_column_expand(2, false)
    _palette.set_column_custom_minimum_width(2, 22)
    _palette.size_flags_vertical = Control.SIZE_EXPAND_FILL
    _palette.item_selected.connect(_on_palette_selected)
    _palette.item_activated.connect(func() -> void: _on_add_pressed())
    col.add_child(_palette)


func _build_chain(body: HBoxContainer) -> void:
    var col := VBoxContainer.new()
    col.custom_minimum_size = Vector2(400, 0)
    col.size_flags_stretch_ratio = 5.0
    col.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    body.add_child(col)
    col.add_child(UiStyle.label("Chain", 14, UiStyle.MUTED))
    _chain = Tree.new()
    _chain.columns = COLS.size()
    _chain.hide_root = true
    _chain.select_mode = Tree.SELECT_ROW
    _chain.column_titles_visible = true
    for i in COLS.size():
        _chain.set_column_title(i, COLS[i])
    _chain.set_column_expand(0, false)
    _chain.set_column_custom_minimum_width(0, 24)
    _chain.set_column_expand(1, false)
    _chain.set_column_custom_minimum_width(1, 22)
    _chain.set_column_expand(2, true)
    _chain.set_column_custom_minimum_width(2, 120)
    for i in [3, 4, 5, 6, 7]:
        _chain.set_column_expand(i, false)
        _chain.set_column_custom_minimum_width(i, 64)
    _chain.size_flags_vertical = Control.SIZE_EXPAND_FILL
    _chain.drop_mode_flags = Tree.DROP_MODE_INBETWEEN
    _chain.set_drag_forwarding(_chain_drag, _chain_can_drop, _chain_drop)
    _chain.item_selected.connect(_on_chain_selected)
    col.add_child(_chain)
    _hint = UiStyle.label("", 12, UiStyle.MUTED)
    _hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
    col.add_child(_hint)
    var row1 := HBoxContainer.new()
    col.add_child(row1)
    _add_btn = UiStyle.button("Add", "Add the palette step: bound to the selected elements, else virtual")
    _add_btn.pressed.connect(_on_add_pressed)
    row1.add_child(_add_btn)
    _add_menu = PopupMenu.new()
    _add_menu.add_item("Bound to the selected elements", 0)
    _add_menu.add_item("Virtual (no elements)", 1)
    _add_menu.id_pressed.connect(func(id: int) -> void: add_step(palette_step, "bound" if id == 0 else "virtual"))
    add_child(_add_menu)
    _up_btn = UiStyle.button("Up", "Move the row up (or drag rows)")
    _up_btn.pressed.connect(func() -> void: move_selected(-1))
    row1.add_child(_up_btn)
    _down_btn = UiStyle.button("Down", "Move the row down (or drag rows)")
    _down_btn.pressed.connect(func() -> void: move_selected(1))
    row1.add_child(_down_btn)
    _remove_btn = UiStyle.button("Remove", "Remove the row (its successors inherit its predecessors)")
    _remove_btn.pressed.connect(func() -> void: remove_selected())
    row1.add_child(_remove_btn)
    _auto_btn = UiStyle.button("Auto-link", "Link every row FS to the previous row")
    _auto_btn.pressed.connect(func() -> void: auto_link())
    row1.add_child(_auto_btn)
    _link_btn = UiStyle.button("Link to...", "Make the row follow another row (FS / SS / FF with lag)")
    _link_btn.pressed.connect(_open_link_popup)
    row1.add_child(_link_btn)
    var row2 := HBoxContainer.new()
    col.add_child(row2)
    row2.add_child(UiStyle.label("Lag d", 13, UiStyle.MUTED))
    _lag_spin = SpinBox.new()
    _lag_spin.min_value = -30
    _lag_spin.max_value = 365
    _lag_spin.step = 1
    _lag_spin.custom_minimum_size.x = 70
    row2.add_child(_lag_spin)
    _lag_btn = UiStyle.button("Set", "Set the lag of the row's links (days)")
    _lag_btn.pressed.connect(func() -> void: set_lag(int(_lag_spin.value)))
    row2.add_child(_lag_btn)
    row2.add_child(UiStyle.label("Duration d", 13, UiStyle.MUTED))
    _dur_spin = SpinBox.new()
    _dur_spin.min_value = 1
    _dur_spin.max_value = 365
    _dur_spin.step = 1
    _dur_spin.value = 1
    _dur_spin.custom_minimum_size.x = 70
    row2.add_child(_dur_spin)
    _dur_btn = UiStyle.button("Set", "Set the duration of a virtual task (days)")
    _dur_btn.pressed.connect(func() -> void: set_duration(int(_dur_spin.value)))
    row2.add_child(_dur_btn)
    var row3 := HBoxContainer.new()
    col.add_child(row3)
    row3.add_child(UiStyle.label("Hold point", 13, UiStyle.MUTED))
    _hold_opt = OptionButton.new()
    _hold_opt.focus_mode = Control.FOCUS_NONE
    row3.add_child(_hold_opt)
    _hold_btn = UiStyle.button("Mark", "Make the task an inspection of this type (none: the step's default)")
    _hold_btn.pressed.connect(func() -> void:
        var i: int = _hold_opt.selected
        set_hold_point(_hold_types[i] if i >= 0 and i < _hold_types.size() else ""))
    row3.add_child(_hold_btn)
    # "Link to..." popup
    _link_popup = PopupPanel.new()
    var lb := VBoxContainer.new()
    lb.add_child(UiStyle.label("This row follows", 13, UiStyle.MUTED))
    _link_pred = OptionButton.new()
    lb.add_child(_link_pred)
    var lrow := HBoxContainer.new()
    _link_type = OptionButton.new()
    for t in LINK_TYPES:
        _link_type.add_item(t)
    lrow.add_child(_link_type)
    lrow.add_child(UiStyle.label("lag d", 13, UiStyle.MUTED))
    _link_lag = SpinBox.new()
    _link_lag.min_value = -30
    _link_lag.max_value = 365
    lrow.add_child(_link_lag)
    lb.add_child(lrow)
    var ok := UiStyle.button("Link")
    ok.pressed.connect(func() -> void:
        var rows: Array[String] = _link_candidates()
        var i: int = _link_pred.selected
        if i >= 0 and i < rows.size():
            link_selected_after(rows[i], LINK_TYPES[_link_type.selected], int(_link_lag.value))
        _link_popup.hide())
    lb.add_child(ok)
    _link_popup.add_child(lb)
    add_child(_link_popup)


func _build_elements(body: HBoxContainer) -> void:
    var col := VBoxContainer.new()
    col.custom_minimum_size = Vector2(220, 0)
    col.size_flags_stretch_ratio = 3.0
    col.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    body.add_child(col)
    col.add_child(UiStyle.label("Elements in the zone", 14, UiStyle.MUTED))
    _elem_search = LineEdit.new()
    _elem_search.placeholder_text = "Filter elements"
    _elem_search.clear_button_enabled = true
    _elem_search.text_changed.connect(func(_t: String) -> void: _elements_dirty = true)
    col.add_child(_elem_search)
    _elements = Tree.new()
    _elements.columns = 4
    _elements.hide_root = true
    _elements.select_mode = Tree.SELECT_MULTI
    _elements.column_titles_visible = true
    _elements.set_column_title(0, "Element")
    _elements.set_column_title(1, "Class")
    _elements.set_column_title(2, "State")
    _elements.set_column_title(3, "In")
    _elements.set_column_expand(1, false)
    _elements.set_column_custom_minimum_width(1, 70)
    _elements.set_column_expand(2, false)
    _elements.set_column_custom_minimum_width(2, 56)
    _elements.set_column_expand(3, false)
    _elements.set_column_custom_minimum_width(3, 30)
    _elements.size_flags_vertical = Control.SIZE_EXPAND_FILL
    _elements.multi_selected.connect(_on_element_selected)
    col.add_child(_elements)
    _elem_info = UiStyle.label("", 12, UiStyle.MUTED)
    col.add_child(_elem_info)
    var row := HBoxContainer.new()
    col.add_child(row)
    _bind_btn = UiStyle.button("Bind selected", "Bind the selected elements to the highlighted chain row")
    _bind_btn.pressed.connect(func() -> void: bind_selected())
    row.add_child(_bind_btn)
    _view_btn = UiStyle.button("Select in 3D", "Highlight the selected elements in the 3D view")
    _view_btn.pressed.connect(func() -> void: select_in_3d())
    row.add_child(_view_btn)
    _elem_needed = UiStyle.button("What's needed?", "Recipes that apply to the first selected element")
    _elem_needed.pressed.connect(func() -> void:
        var g: Array[String] = selected_element_guids()
        if not g.is_empty():
            whats_needed_requested.emit(zone_id, g[0]))
    col.add_child(_elem_needed)


# ------------------------------------------------------------------ opening and closing

## Shows the editor for a zone (switching zone resets the row and element selection).
func open_for_zone(id: String) -> void:
    show_zone(id)
    visible = true
    refresh()


## Follows another zone without changing the visibility.
func show_zone(id: String) -> void:
    if id == zone_id:
        return
    zone_id = id
    selected_task_id = ""
    selected_elements.clear()
    _dirty = true
    _elements_dirty = true
    _rebuild_recipe_menu()


func close_editor() -> void:
    visible = false
    closed.emit()


## N key / top bar button: hides the editor, or shows it for `id` (the zone selected in the inspector).
func toggle(id: String = "") -> void:
    if visible:
        close_editor()
    else:
        open_for_zone(id if id != "" else zone_id)


# ------------------------------------------------------------------ data helpers

func _kit_colours() -> bool:
    return bim_view != null and bim_view.marker_mesh_provider.is_valid()


## The authored tasks of the zone in chain order: dependency order, ties by creation order.
func ordered_chain() -> Array[TaskData]:
    var out: Array[TaskData] = []
    if gs == null or gs.bundle == null or zone_id == "":
        return out
    var members: Array[TaskData] = []
    var index: Dictionary = {}  # task id -> creation index among the members
    for t in gs.bundle.tasks:
        if t.zone_id == zone_id and t.is_authored():
            index[t.task_id] = members.size()
            members.append(t)
    var indeg: Dictionary = {}
    for t in members:
        var n: int = 0
        for p in t.predecessors:
            if index.has(str(p["task_id"])):
                n += 1
        indeg[t.task_id] = n
    var done: Dictionary = {}
    while out.size() < members.size():
        var pick: TaskData = null
        for t in members:
            if done.has(t.task_id) or int(indeg[t.task_id]) > 0:
                continue
            pick = t
            break  # members are in creation order: the first ready one wins
        if pick == null:  # a cycle cannot be authored, but never loop forever
            for t in members:
                if not done.has(t.task_id):
                    pick = t
                    break
        done[pick.task_id] = true
        out.append(pick)
        for s in gs.bundle.successors_by_task.get(pick.task_id, []):
            if indeg.has(str(s)):
                indeg[str(s)] = int(indeg[str(s)]) - 1
    return out


func chain_ids() -> Array[String]:
    var out: Array[String] = []
    for t in ordered_chain():
        out.append(t.task_id)
    return out


func selected_task() -> TaskData:
    if gs == null or gs.bundle == null or selected_task_id == "":
        return null
    return gs.bundle.tasks_by_id.get(selected_task_id, null)


func selected_index() -> int:
    return chain_ids().find(selected_task_id)


## Chain predecessors of a task (other authored tasks of the zone).
func _chain_preds(t: TaskData, ids: Array[String]) -> Array[Dictionary]:
    var out: Array[Dictionary] = []
    for p in t.predecessors:
        if ids.has(str(p["task_id"])):
            out.append(p)
    return out


func selected_element_guids() -> Array[String]:
    var out: Array[String] = []
    if gs == null or gs.bundle == null:
        return out
    for e in gs.bundle.elements:  # zone order, deterministic
        if e.zone_id == zone_id and selected_elements.has(e.guid):
            out.append(e.guid)
    return out


func select_palette_step(step_id: String) -> void:
    palette_step = step_id
    palette_recipe = ""


func select_row(task_id: String) -> void:
    selected_task_id = task_id
    _dirty = true
    refresh()


## Highlights elements in the right column (replaces the selection).
func select_elements(guids: Array) -> void:
    selected_elements.clear()
    for g in guids:
        selected_elements[str(g)] = true
    _elements_dirty = true
    _refresh_elements()
    _update_buttons()


## Tasks that have not started (waiting, blocked or ready) can be edited, as in Manual.
func _editable(task_id: String) -> bool:
    var st: int = (gs.runtime[task_id] as TaskRuntime).state
    return st == TaskRuntime.State.NOT_STARTED or st == TaskRuntime.State.READY or st == TaskRuntime.State.BLOCKED


func _num(v: float) -> String:
    if absf(v - round(v)) < 0.005:
        return str(int(round(v)))
    return "%.1f" % v


func _fail() -> void:
    message.emit(gs.last_error)


# ------------------------------------------------------------------ actions (all through Manual)

func set_manual_mode(on: bool) -> bool:
    if zone_id == "":
        return false
    if not Manual.set_mode(gs, zone_id, on):
        _fail()
        return false
    message.emit("%s: manual mode %s" % [zone_id, "on (generated packages frozen)" if on else "off"])
    _dirty = true
    refresh()
    return true


## Adds a task for a library step. mode: "virtual" (no elements), "bound" (the selected elements) or "auto"
## (bound when elements are selected, else virtual). Returns the task id, "" on error.
func add_step(step_id: String, mode: String = "auto") -> String:
    if zone_id == "":
        message.emit("Select a zone first")
        return ""
    if step_id == "":
        message.emit("Pick a step in the palette")
        return ""
    var guids: Array[String] = selected_element_guids()
    var bound: bool = mode == "bound" or (mode == "auto" and not guids.is_empty())
    var spec: Dictionary = {"step": step_id, "zone_id": zone_id}
    if bound:
        if guids.is_empty():
            message.emit("Select elements in the right column to bind")
            return ""
        spec["elements"] = guids
    else:
        spec["virtual"] = true
    var id: String = Manual.add_task(gs, spec)
    if id == "":
        _fail()
        return ""
    selected_task_id = id
    _dirty = true
    _elements_dirty = true
    refresh()
    return id


## Applies a recipe to the zone (Manual.apply_recipe). Returns the result ({} on error).
func apply_recipe(recipe_id: String, include_optional: bool = false) -> Dictionary:
    var res: Dictionary = Manual.apply_recipe(gs, recipe_id, zone_id, "", include_optional)
    if res.is_empty():
        _fail()
        return res
    message.emit("Recipe applied: %d task(s) added, %d existing linked" % [(res["created"] as Array).size(), (res["reused"] as Array).size()])
    _dirty = true
    _elements_dirty = true
    refresh()
    return res


func remove_selected() -> bool:
    if selected_task_id == "":
        return false
    var idx: int = selected_index()
    var ids: Array[String] = chain_ids()
    if not Manual.remove_task(gs, selected_task_id):
        _fail()
        return false
    ids.erase(selected_task_id)
    selected_task_id = ids[clampi(idx, 0, ids.size() - 1)] if not ids.is_empty() else ""
    _dirty = true
    _elements_dirty = true
    refresh()
    return true


## Links each row FS (lag 0) to the previous row, keeping links that already exist. Returns the number of new links.
func auto_link() -> int:
    var ids: Array[String] = chain_ids()
    var made: int = 0
    for i in range(1, ids.size()):
        var t: TaskData = gs.bundle.tasks_by_id[ids[i]]
        var linked: bool = false
        for p in t.predecessors:
            if str(p["task_id"]) == ids[i - 1]:
                linked = true
        if linked:
            continue
        if not Manual.link(gs, ids[i - 1], ids[i], "FS", 0):
            _fail()
            break
        made += 1
    _dirty = true
    refresh()
    return made


## Makes the highlighted row follow `pred_id`.
func link_selected_after(pred_id: String, type: String = "FS", lag_days: int = 0) -> bool:
    if selected_task_id == "":
        message.emit("Highlight a chain row first")
        return false
    if not Manual.link(gs, pred_id, selected_task_id, type, lag_days):
        _fail()
        return false
    _dirty = true
    refresh()
    return true


## Sets the lag of every link into the highlighted row.
func set_lag(days: int) -> bool:
    var t: TaskData = selected_task()
    if t == null:
        return false
    if t.predecessors.is_empty():
        message.emit("The row has no predecessor: link it first")
        return false
    var preds: Array[Dictionary] = t.predecessors.duplicate(true)
    for p in preds:
        if not Manual.link(gs, str(p["task_id"]), t.task_id, str(p["type"]), days):
            _fail()
            return false
    _dirty = true
    refresh()
    return true


## Duration in days of the highlighted virtual task.
func set_duration(days: int) -> bool:
    var t: TaskData = selected_task()
    if t == null:
        return false
    if not t.is_virtual:
        message.emit("Duration is set on virtual tasks; bound tasks follow their quantity")
        return false
    if not Manual.update_task(gs, t.task_id, {"duration_days": maxi(days, 1)}):
        _fail()
        return false
    _dirty = true
    refresh()
    return true


## Makes the highlighted task an inspection hold point of `type` ("" = back to the step's own setting).
func set_hold_point(type: String) -> bool:
    var t: TaskData = selected_task()
    if t == null:
        return false
    if not Manual.update_task(gs, t.task_id, {"hold_point": type}):
        _fail()
        return false
    _dirty = true
    refresh()
    return true


## Binds the highlighted elements to the highlighted chain row (added to its current binding; a virtual row
## becomes an element task).
func bind_selected() -> bool:
    var t: TaskData = selected_task()
    if t == null:
        message.emit("Highlight a chain row first")
        return false
    var guids: Array[String] = selected_element_guids()
    if guids.is_empty():
        message.emit("Select elements in the right column")
        return false
    var all: Array[String] = []
    if not t.is_virtual:
        all.append_array(t.element_guids)
    for g in guids:
        if not all.has(g):
            all.append(g)
    var fields: Dictionary = {"elements": all}
    if t.is_virtual:
        fields["virtual"] = false
        fields["duration_days"] = 0
    if not Manual.update_task(gs, t.task_id, fields):
        _fail()
        return false
    _dirty = true
    _elements_dirty = true
    refresh()
    return true


func select_in_3d() -> bool:
    var guids: Array[String] = selected_element_guids()
    if guids.is_empty():
        message.emit("Select elements in the right column")
        return false
    if bim_view == null or not bim_view.has_method("highlight_elements"):
        message.emit("The 3D view has no highlight support yet")
        return false
    bim_view.call("highlight_elements", guids)
    return true


## Writes the manual_sequence document to user://manual_<scenario>.json. Returns the path ("" on failure).
func export_manual() -> String:
    var doc: Dictionary = Manual.export_doc(gs)
    var path: String = manual_path(gs.scenario.id)
    var f := FileAccess.open(path, FileAccess.WRITE)
    if f == null:
        message.emit("Manual export failed: cannot write %s" % path)
        return ""
    f.store_string(JSON.stringify(doc, " ") + "\n")
    f.close()
    last_export = doc
    last_export_path = path
    message.emit("Manual sequence exported (%d tasks): %s" % [(doc["tasks"] as Array).size(), ProjectSettings.globalize_path(path)])
    return path


static func manual_path(scenario_id: String) -> String:
    return "user://manual_%s.json" % scenario_id


# ------------------------------------------------------------------ reordering

## Moves the highlighted row by `delta` positions.
func move_selected(delta: int) -> bool:
    var idx: int = selected_index()
    if idx < 0:
        return false
    return move_row(idx, idx + delta)


## Moves row `from_i` to position `to_i` of the chain: the rows in between (and the one after the moved range) are
## re-linked FS in the new order; each keeps the type and lag of its previous chain link. Links to tasks outside the
## chain (generated tasks, other zones) are kept. Returns false (nothing changed) when the move is impossible.
func move_row(from_i: int, to_i: int) -> bool:
    var ids: Array[String] = chain_ids()
    var n: int = ids.size()
    if from_i < 0 or from_i >= n or to_i < 0 or to_i >= n or from_i == to_i:
        return false
    var new_ids: Array[String] = ids.duplicate()
    var moved: String = new_ids[from_i]
    new_ids.remove_at(from_i)
    new_ids.insert(to_i, moved)
    var lo: int = mini(from_i, to_i)
    var hi: int = mini(maxi(from_i, to_i) + 1, n - 1)
    # window rows: their chain links are replaced by the new order
    var window: Array[String] = []
    for k in range(lo, hi + 1):
        window.append(new_ids[k])
    var removed: Array[Dictionary] = []  # {from, to, type, lag}
    var attrs: Dictionary = {}  # row id -> {type, lag} of its first chain link
    for id in window:
        var t: TaskData = gs.bundle.tasks_by_id[id]
        if not _editable(id):
            message.emit("%s has already started and cannot be moved" % t.element_name)
            return false
        var cps: Array[Dictionary] = _chain_preds(t, ids)
        attrs[id] = {"type": str(cps[0]["type"]), "lag": int(cps[0]["lag_days"])} if not cps.is_empty() else {"type": "FS", "lag": 0}
        for p in cps:
            removed.append({"from": str(p["task_id"]), "to": id, "type": str(p["type"]), "lag": int(p["lag_days"])})
    for r in removed:
        Manual.unlink(gs, str(r["from"]), str(r["to"]))
    var ok_all: bool = true
    for k in range(lo, hi + 1):
        if k == 0:
            continue
        var id2: String = new_ids[k]
        var a: Dictionary = attrs[id2]
        if not Manual.link(gs, new_ids[k - 1], id2, str(a["type"]), int(a["lag"])):
            ok_all = false
            _fail()
            break
    if not ok_all:  # restore the previous links
        for k in range(lo, hi + 1):
            if k > 0:
                Manual.unlink(gs, new_ids[k - 1], new_ids[k])
        for r in removed:
            Manual.link(gs, str(r["from"]), str(r["to"]), str(r["type"]), int(r["lag"]))
        _dirty = true
        refresh()
        return false
    selected_task_id = moved
    _dirty = true
    refresh()
    return true


func _chain_drag(at: Vector2) -> Variant:
    var it: TreeItem = _chain.get_item_at_position(at)
    if it == null:
        return null
    var idx: int = _chain_ids.find(str(it.get_metadata(0)))
    if idx < 0:
        return null
    var preview := UiStyle.label(it.get_text(2), 13)
    _chain.set_drag_preview(preview)
    return {"seq_row": idx}


func _chain_can_drop(at: Vector2, data: Variant) -> bool:
    if not (data is Dictionary and (data as Dictionary).has("seq_row")):
        return false
    return _chain.get_item_at_position(at) != null


func _chain_drop(at: Vector2, data: Variant) -> void:
    var it: TreeItem = _chain.get_item_at_position(at)
    if it == null or not (data is Dictionary):
        return
    var from_i: int = int((data as Dictionary).get("seq_row", -1))
    var target: int = _chain_ids.find(str(it.get_metadata(0)))
    if target < 0:
        return
    if _chain.get_drop_section_at_position(at) > 0:
        target += 1
    if from_i < target:
        target -= 1
    move_row(from_i, target)


# ------------------------------------------------------------------ palette

func _rebuild_palette() -> void:
    if _palette == null or gs == null or gs.bundle == null:
        return
    _palette.clear()
    var q: String = _search.text.strip_edges().to_lower()
    var root: TreeItem = _palette.create_item()
    var b: SequenceBundle = gs.bundle
    var by_phase: Dictionary = {}
    for st in b.steps:
        if q != "" and not _step_matches(st, q):
            continue
        if not by_phase.has(st.phase):
            by_phase[st.phase] = [] as Array[StepDef]
        (by_phase[st.phase] as Array).append(st)
    var phase_ids: Array[String] = []
    for p in b.phases:
        phase_ids.append(str(p["id"]))
    for ph in by_phase:
        if not phase_ids.has(str(ph)):
            phase_ids.append(str(ph))
    for pid in phase_ids:
        if not by_phase.has(pid):
            continue
        var steps: Array = by_phase[pid]
        var pname: String = pid
        for p in b.phases:
            if str(p["id"]) == pid:
                pname = str(p["name"])
        var g: TreeItem = _palette.create_item(root)
        g.set_text(0, "%s (%d)" % [pname if pname != "" else "other", steps.size()])
        g.set_selectable(0, false)
        g.set_selectable(1, false)
        g.set_selectable(2, false)
        g.set_custom_color(0, UiStyle.ACCENT)
        g.collapsed = q == ""
        for s in steps:
            _add_step_item(g, s as StepDef)
    # recipes as expandable groups of steps
    var recipes: Array[RecipeData] = gs.recipe_list()
    var shown: Array[RecipeData] = []
    for r in recipes:
        if q == "" or r.name.to_lower().contains(q) or r.id.to_lower().contains(q) or _recipe_has_step(r, q):
            shown.append(r)
    if not shown.is_empty():
        var rg: TreeItem = _palette.create_item(root)
        rg.set_text(0, "Recipes (%d)" % shown.size())
        rg.set_selectable(0, false)
        rg.set_selectable(1, false)
        rg.set_selectable(2, false)
        rg.set_custom_color(0, UiStyle.ACCENT)
        rg.collapsed = q == ""
        for r in shown:
            var ri: TreeItem = _palette.create_item(rg)
            ri.set_text(0, r.name)
            ri.set_metadata(0, {"kind": "recipe", "id": r.id})
            ri.set_tooltip_text(0, r.summary if r.summary != "" else r.id)
            ri.collapsed = true
            for s in r.steps:
                var sd: StepDef = b.steps_by_id.get(s.ref, null)
                var si: TreeItem = _palette.create_item(ri)
                if sd != null:
                    si.set_text(0, sd.name + ("  (virtual)" if s.is_virtual else ""))
                    si.set_text(1, sd.trade)
                    si.set_text(2, sd.work_face.substr(0, 1).to_upper())
                    si.set_metadata(0, {"kind": "step", "id": sd.id})
                else:
                    si.set_text(0, (s.ref if s.ref != "" else s.recipe) + (" (nested recipe)" if s.recipe != "" else ""))
                    si.set_metadata(0, {"kind": "recipe_step", "id": s.ref})
                    si.set_custom_color(0, UiStyle.MUTED)


func _add_step_item(parent: TreeItem, st: StepDef) -> void:
    var it: TreeItem = _palette.create_item(parent)
    it.set_text(0, st.name)
    it.set_tooltip_text(0, "%s\n%s" % [st.id, st.description])
    it.set_text(1, st.trade)
    var td: TradeDef = gs.bundle.trades_by_id.get(st.trade, null)
    if td != null:
        it.set_custom_color(1, td.color)
    it.set_text(2, st.work_face.substr(0, 1).to_upper())
    it.set_tooltip_text(2, "work face: %s" % st.work_face)
    it.set_metadata(0, {"kind": "step", "id": st.id})


func _step_matches(st: StepDef, q: String) -> bool:
    return st.name.to_lower().contains(q) or st.id.to_lower().contains(q) or st.trade.to_lower().contains(q) \
            or st.phase.to_lower().contains(q)


func _recipe_has_step(r: RecipeData, q: String) -> bool:
    for s in r.steps:
        if s.ref.to_lower().contains(q):
            return true
        var sd: StepDef = gs.bundle.steps_by_id.get(s.ref, null)
        if sd != null and sd.name.to_lower().contains(q):
            return true
    return false


## Number of selectable palette entries currently listed (steps and recipe steps / recipes), for the tests.
func palette_entry_count(kind: String = "") -> int:
    var n: int = 0
    var root: TreeItem = _palette.get_root()
    if root == null:
        return 0
    var stack: Array[TreeItem] = [root]
    while not stack.is_empty():
        var it: TreeItem = stack.pop_back()
        var meta: Variant = it.get_metadata(0)
        if meta is Dictionary and (kind == "" or str((meta as Dictionary)["kind"]) == kind):
            n += 1
        for c in it.get_children():
            stack.append(c)
    return n


func set_search(text: String) -> void:
    _search.text = text
    _rebuild_palette()


func _on_palette_selected() -> void:
    var it: TreeItem = _palette.get_selected()
    if it == null:
        return
    var meta: Variant = it.get_metadata(0)
    if not (meta is Dictionary):
        return
    var d: Dictionary = meta
    match str(d["kind"]):
        "step":
            palette_step = str(d["id"])
            palette_recipe = ""
        "recipe":
            palette_recipe = str(d["id"])
            palette_step = ""
        _:
            palette_step = ""
            palette_recipe = ""
    _update_buttons()


func _on_add_pressed() -> void:
    if palette_step == "" and palette_recipe != "":
        apply_recipe(palette_recipe)
        return
    if palette_step == "":
        message.emit("Pick a step or a recipe in the palette")
        return
    if selected_element_guids().is_empty():
        add_step(palette_step, "virtual")
        return
    # elements are highlighted: ask whether to bind them or to add a virtual task
    _add_menu.set_item_text(0, "Bound to %d selected element(s)" % selected_element_guids().size())
    if is_inside_tree():
        _add_menu.popup(Rect2i(Vector2i(_add_btn.get_screen_position()) + Vector2i(0, int(_add_btn.size.y)), Vector2i.ZERO))
    else:
        add_step(palette_step, "bound")


# ------------------------------------------------------------------ recipe menu

func _rebuild_recipe_menu() -> void:
    if _recipe_menu == null or gs == null or gs.bundle == null:
        return
    var pop: PopupMenu = _recipe_menu.get_popup()
    _menu_zone = zone_id
    pop.clear()
    if _all_menu != null:
        pop.remove_child(_all_menu)
        _all_menu.queue_free()
        _all_menu = null
    _recipe_ids.clear()
    var all: Array[RecipeData] = gs.recipe_list()
    if all.is_empty():
        pop.add_item("No recipes available", 0)
        pop.set_item_disabled(0, true)
        return
    var applicable: Array[String] = []
    if zone_id != "" and gs.bundle.zones_by_id.has(zone_id):
        for r in LogicLib.explain_zone(gs, zone_id).get("recipes", []):
            applicable.append(str((r as Dictionary)["recipe_id"]))
    pop.add_separator("Applicable to this zone")
    if applicable.is_empty():
        pop.add_item("(none applies)", 0)
        pop.set_item_disabled(pop.item_count - 1, true)
    for rid in applicable:
        var rec: RecipeData = gs.recipe_by_id(rid)
        _recipe_ids.append(rid)
        pop.add_item(rec.name if rec != null else rid, _recipe_ids.size() - 1)
    _all_menu = PopupMenu.new()
    _all_menu.name = "AllRecipes"
    _all_menu.id_pressed.connect(_on_recipe_menu)
    pop.add_child(_all_menu)
    for r in all:
        _recipe_ids.append(r.id)
        _all_menu.add_item(r.name, _recipe_ids.size() - 1)
    pop.add_submenu_node_item("All recipes", _all_menu)


func _on_recipe_menu(id: int) -> void:
    if id >= 0 and id < _recipe_ids.size():
        apply_recipe(_recipe_ids[id])


func recipe_menu_ids() -> Array[String]:
    return _recipe_ids


func _rebuild_hold_types() -> void:
    if _hold_opt == null or gs == null or gs.bundle == null:
        return
    _hold_types.clear()
    _hold_opt.clear()
    _hold_types.append("")
    _hold_opt.add_item("(step default)")
    var seen: Array[String] = DEFAULT_HOLD_POINTS.duplicate()
    for st in gs.bundle.steps:
        if st.inspection_type != "" and not seen.has(st.inspection_type):
            seen.append(st.inspection_type)
    for h in seen:
        _hold_types.append(h)
        _hold_opt.add_item(h.replace("_", " "))


# ------------------------------------------------------------------ chain view

func _on_chain_selected() -> void:
    if _sync:
        return
    var it: TreeItem = _chain.get_selected()
    selected_task_id = str(it.get_metadata(0)) if it != null else ""
    _sync_spins()
    _update_buttons()


func _sync_spins() -> void:
    var t: TaskData = selected_task()
    if t == null:
        return
    var ids: Array[String] = chain_ids()
    var cps: Array[Dictionary] = _chain_preds(t, ids)
    if not cps.is_empty():
        _lag_spin.set_value_no_signal(float(int(cps[0]["lag_days"])))
    if t.is_virtual and t.duration_days > 0:
        _dur_spin.set_value_no_signal(float(t.duration_days))
    var hi: int = 0
    if t.inspection and t.inspection_type != "":
        hi = maxi(_hold_types.find(t.inspection_type), 0)
    _hold_opt.select(hi)


func _link_candidates() -> Array[String]:
    var out: Array[String] = []
    for id in chain_ids():
        if id != selected_task_id:
            out.append(id)
    return out


func _open_link_popup() -> void:
    if selected_task_id == "":
        message.emit("Highlight a chain row first")
        return
    _link_pred.clear()
    var ids: Array[String] = chain_ids()
    for id in _link_candidates():
        var t: TaskData = gs.bundle.tasks_by_id[id]
        _link_pred.add_item("%d. %s" % [ids.find(id) + 1, _row_name(t)])
    if _link_pred.item_count == 0:
        message.emit("There is no other row to link to")
        return
    if is_inside_tree():
        _link_popup.popup(Rect2i(Vector2i(_link_btn.get_screen_position()) + Vector2i(0, int(_link_btn.size.y)), Vector2i(260, 120)))


func _row_name(t: TaskData) -> String:
    var st: StepDef = gs.bundle.step_of(t)
    return st.name if st != null else t.step_id


## Row texts of the chain tree: [#, glyph, step, binding, qty / dur, link, after, state].
func row_texts(t: TaskData, ids: Array[String]) -> Array[String]:
    var glyph: String = ""
    if t.is_virtual:
        glyph = MarkerLegend.glyph(gs.marker_of(t))
    else:
        var st0: StepDef = gs.bundle.step_of(t)
        glyph = st0.work_face.substr(0, 1).to_upper() if st0 != null and st0.work_face != "" else ""
    var step_txt: String = _row_name(t)
    if t.inspection and t.inspection_type != "":
        step_txt += "  [hold: %s]" % t.inspection_type
    var binding: String = "virtual"
    if not t.is_virtual:
        var n_el: int = t.element_guids.size() if not t.element_guids.is_empty() else (1 if t.element_guid != "" else 0)
        binding = "%d el." % n_el
    var qd: String = ("%d d" % t.duration_days) if (t.is_virtual or t.duration_days > 0) else ("%s %s" % [_num(t.quantity), t.unit])
    var cps: Array[Dictionary] = _chain_preds(t, ids)
    var link_txt: String = "-"
    if not cps.is_empty():
        link_txt = "%s %+dd" % [str(cps[0]["type"]), int(cps[0]["lag_days"])] if int(cps[0]["lag_days"]) != 0 else str(cps[0]["type"])
    var after: Array[String] = []
    for p in t.predecessors:
        var pid: String = str(p["task_id"])
        var at: int = ids.find(pid)
        if at >= 0:
            after.append("#%d" % (at + 1))
        else:
            var pt: TaskData = gs.bundle.tasks_by_id.get(pid, null)
            after.append(pt.manual_id if pt != null and pt.manual_id != "" else pid)
    var state: String = TaskRuntime.state_name((gs.runtime[t.task_id] as TaskRuntime).state).to_lower().replace("_", " ")
    return [str(ids.find(t.task_id) + 1), glyph, step_txt, binding, qd, link_txt, ", ".join(after), state]


func refresh() -> void:
    _dirty = false
    if gs == null or gs.bundle == null or _chain == null:
        return
    var has_zone: bool = zone_id != "" and gs.bundle.zones_by_id.has(zone_id)
    _sync = true
    if not has_zone:
        _title.text = "Sequence editor"
        _manual_check.button_pressed = false
        _manual_check.disabled = true
        _chain.clear()
        _chain_ids.clear()
        _hint.text = "Select a zone (zone inspector, timeline or map) to author its sequence."
        _elements.clear()
        lane.visible = false
        _sync = false
        _update_buttons()
        return
    _manual_check.disabled = false
    _manual_check.button_pressed = gs.manual_zones.has(zone_id)
    var z: ZoneData = gs.bundle.zones_by_id[zone_id]
    var rows: Array[TaskData] = ordered_chain()
    _title.text = "Sequence: %s" % z.name
    _chain_ids.clear()
    for t in rows:
        _chain_ids.append(t.task_id)
    if not _chain_ids.has(selected_task_id):
        selected_task_id = ""
    _chain.clear()
    var root: TreeItem = _chain.create_item()
    var kit: bool = _kit_colours()
    for t in rows:
        var it: TreeItem = _chain.create_item(root)
        var texts: Array[String] = row_texts(t, _chain_ids)
        for c in texts.size():
            it.set_text(c, texts[c])
        it.set_metadata(0, t.task_id)
        if t.is_virtual:
            var m: String = gs.marker_of(t)
            it.set_custom_color(1, MarkerLegend.colour(m, kit))
            it.set_tooltip_text(1, MarkerLegend.label_of(m))
        else:
            it.set_custom_color(1, UiStyle.MUTED)
        it.set_tooltip_text(2, "%s\n%s" % [t.task_id, t.note])
        it.set_text_alignment(0, HORIZONTAL_ALIGNMENT_RIGHT)
        if t.task_id == selected_task_id:
            it.select(0)
    if rows.is_empty():
        _hint.text = "No manual tasks in this zone yet. Pick a step in the palette and press Add, or apply a recipe."
    else:
        _hint.text = "%d task(s). Manual mode: %s. Drag rows or use Up / Down to re-order." % [rows.size(), "on" if gs.manual_zones.has(zone_id) else "off"]
    _sync_spins()
    _sync = false
    _refresh_lane(rows)
    _recipe_menu.disabled = gs.recipe_list().is_empty()
    if _menu_zone != zone_id:
        _rebuild_recipe_menu()
    if _elements_dirty:
        _refresh_elements()
    _update_buttons()


## Model of the one-lane timeline: the zone's authored tasks as bars of a single zone row.
func lane_model(rows: Array[TaskData]) -> Dictionary:
    var cur: int = gs.current_day()
    var ids: Dictionary = {}
    for t in rows:
        ids[t.task_id] = true
    var bars: Array = []
    var max_day: int = gs.bundle.contract_weeks() * 5
    if not rows.is_empty():
        for r in GanttModel.task_rows(gs, zone_id, cur):
            var tid: String = str(r["task_id"])
            if not ids.has(tid):
                continue
            var bar: Dictionary = (r["bars"] as Array)[0]
            var t: TaskData = gs.bundle.tasks_by_id[tid]
            if t.is_virtual:
                bar["name"] = "%s %s" % [MarkerLegend.glyph(gs.marker_of(t)), _row_name(t)]
            else:
                bar["name"] = "%s - %s" % [_row_name(t), t.element_name]
            bars.append(bar)
            max_day = maxi(max_day, int(bar["span_end"]))
    var z: ZoneData = gs.bundle.zones_by_id[zone_id]
    var row: Dictionary = {"kind": "zone", "zone_id": zone_id, "name": z.name, "storey_id": z.storey_id, "bars": bars,
            "lanes": GanttModel.pack_lanes(bars), "stations": [], "deliveries": [], "incidents": [], "card_id": "",
            "behind_takt": false, "expanded": false}
    return {"rows": [row], "current_day": cur, "week": gs.week, "max_day": max_day, "bar_count": bars.size(),
            "contract_day": gs.bundle.contract_weeks() * 5}


func _refresh_lane(rows: Array[TaskData]) -> void:
    lane.gs = gs
    lane.visible = true
    lane.selected_zone_id = ""
    lane.set_model(lane_model(rows))


## Number of bars of the lane (one per chain task).
func lane_bar_count() -> int:
    if lane == null or lane.rows.is_empty():
        return 0
    return ((lane.rows[0] as Dictionary)["bars"] as Array).size()


# ------------------------------------------------------------------ elements column

func _on_element_selected(item: TreeItem, _col: int, sel: bool) -> void:
    if _sync:
        return
    var g: String = str(item.get_metadata(0))
    if sel:
        selected_elements[g] = true
    else:
        selected_elements.erase(g)
    _update_buttons()


func _refresh_elements() -> void:
    _elements_dirty = false
    if _elements == null or gs == null or gs.bundle == null:
        return
    var was: bool = _sync
    _sync = true
    _elements.clear()
    if zone_id == "" or not gs.bundle.zones_by_id.has(zone_id):
        _sync = was
        return
    var root: TreeItem = _elements.create_item()
    var q: String = _elem_search.text.strip_edges().to_lower()
    # which chain rows bind each element
    var ids: Array[String] = chain_ids()
    var in_rows: Dictionary = {}
    for i in ids.size():
        for g in (gs.bundle.tasks_by_id[ids[i]] as TaskData).element_guids:
            if not in_rows.has(g):
                in_rows[g] = [] as Array[String]
            (in_rows[g] as Array).append(str(i + 1))
    var shown: int = 0
    var total: int = 0
    for e in gs.bundle.elements:
        if e.zone_id != zone_id:
            continue
        if q != "" and not (e.name.to_lower().contains(q) or e.ifc_class.to_lower().contains(q)):
            continue
        total += 1
        if shown >= MAX_ELEMENT_ROWS and not selected_elements.has(e.guid):
            continue
        shown += 1
        var it: TreeItem = _elements.create_item(root)
        it.set_text(0, e.name)
        it.set_tooltip_text(0, "%s\n%s" % [e.guid, e.ifc_class])
        it.set_text(1, e.ifc_class.trim_prefix("Ifc"))
        it.set_text(2, str(SimState.Visual.keys()[gs.element_visual(e.guid)]).to_lower())
        it.set_text(3, ",".join(in_rows.get(e.guid, [])))
        it.set_metadata(0, e.guid)
        for c in [1, 2, 3]:
            it.set_selectable(c, false)
            it.set_custom_color(c, UiStyle.MUTED)
        if selected_elements.has(e.guid):
            it.select(0)
    _elem_info.text = "%d element(s)%s" % [total, (", showing %d (filter to find others)" % shown) if shown < total else ""]
    _sync = was


func element_row_count() -> int:
    var root: TreeItem = _elements.get_root()
    return root.get_child_count() if root != null else 0


func _update_buttons() -> void:
    if _add_btn == null:
        return
    var has_zone: bool = zone_id != "" and gs != null and gs.bundle != null and gs.bundle.zones_by_id.has(zone_id)
    var t: TaskData = selected_task()
    var idx: int = selected_index()
    var n: int = _chain_ids.size()
    _add_btn.disabled = not has_zone or (palette_step == "" and palette_recipe == "")
    _up_btn.disabled = idx <= 0
    _down_btn.disabled = idx < 0 or idx >= n - 1
    _remove_btn.disabled = t == null
    _auto_btn.disabled = n < 2
    _link_btn.disabled = t == null or n < 2
    _lag_btn.disabled = t == null
    _dur_btn.disabled = t == null or not t.is_virtual
    _hold_btn.disabled = t == null
    var has_el: bool = not selected_elements.is_empty()
    _bind_btn.disabled = t == null or not has_el
    var can3d: bool = bim_view != null and bim_view.has_method("highlight_elements")
    _view_btn.disabled = not has_el or not can3d
    _view_btn.tooltip_text = "Highlight the selected elements in the 3D view" if can3d else "The 3D view has no highlight support yet"
    _elem_needed.disabled = not has_el
    _needed_btn.disabled = not has_zone


func _process(_delta: float) -> void:
    if visible and _dirty:
        refresh()
    elif visible and _elements_dirty:
        _refresh_elements()
