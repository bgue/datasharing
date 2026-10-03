class_name GanttPanel
extends PanelContainer
## The hideable timeline docked at the bottom of the screen (docs/05 section 5): header row (title,
## zoom 12 / 26 / 52 weeks, storey and discipline filters, "my crews only", close), a draggable
## splitter on its top edge and the GanttRenderer. Toggled with T (action `gantt_toggle`) or the top
## bar button; hiding frees the screen area (see `bottom_inset`, main.gd reflows the other panels).

signal zone_selected(zone_id: String)
## Emitted when the panel is shown / hidden or resized, so the owner can reflow the screen.
signal layout_changed()

const MIN_FRACTION: float = 0.15
const MAX_FRACTION: float = 0.7
const MARGIN: float = 8.0

var gs: SimState = null
var renderer: GanttRenderer = null
var height_fraction: float = 0.3
var storey_filter: String = ""
var discipline_filter: String = ""
## Area filter (project.areas): only the zones of the area; "" = all. The control exists only when the bundle has areas.
var area_filter: String = ""
var mine_only: bool = false
## Wall-clock cost of the last model rebuild, milliseconds.
var model_build_ms: float = 0.0
var rebuild_count: int = 0

var _dirty: bool = true
var _handle: Control = null
var _dragging: bool = false
var _storey_opt: OptionButton = null
var _disc_opt: OptionButton = null
var _area_opt: OptionButton = null
var _area_ids: Array[String] = []
var _mine_box: CheckBox = null
var _zoom_buttons: Dictionary = {}
var _storey_ids: Array[String] = []
var _disc_ids: Array[String] = []


func setup(state: SimState, open: bool) -> void:
    gs = state
    UiStyle.place(self, Rect2(0, 1.0 - height_fraction, 1, 1), Vector4(MARGIN, 0, -MARGIN, -MARGIN))
    grow_vertical = Control.GROW_DIRECTION_BEGIN
    var box := VBoxContainer.new()
    box.add_theme_constant_override("separation", 3)
    add_child(box)
    _handle = Control.new()
    _handle.custom_minimum_size = Vector2(0, 7)
    _handle.mouse_default_cursor_shape = Control.CURSOR_VSPLIT
    _handle.tooltip_text = "Drag to resize the timeline"
    _handle.gui_input.connect(_on_handle_input)
    _handle.draw.connect(func() -> void:
        var w: float = _handle.size.x
        _handle.draw_rect(Rect2(w * 0.5 - 24.0, 2.0, 48.0, 3.0), Color(1, 1, 1, 0.35 if not _dragging else 0.7), true))
    box.add_child(_handle)
    var head := HBoxContainer.new()
    box.add_child(head)
    head.add_child(UiStyle.title("Timeline"))
    var spacer := Control.new()
    spacer.custom_minimum_size.x = 12
    head.add_child(spacer)
    for w in GanttRenderer.ZOOMS:
        var zb := UiStyle.button("%d wk" % w, "Show %d weeks" % w)
        zb.toggle_mode = true
        zb.pressed.connect(func() -> void: set_zoom(w))
        head.add_child(zb)
        _zoom_buttons[w] = zb
    _storey_opt = OptionButton.new()
    _storey_opt.focus_mode = Control.FOCUS_NONE
    _storey_opt.tooltip_text = "Filter by storey"
    _storey_opt.item_selected.connect(func(i: int) -> void: set_storey_filter(_storey_ids[i]))
    head.add_child(_storey_opt)
    _disc_opt = OptionButton.new()
    _disc_opt.focus_mode = Control.FOCUS_NONE
    _disc_opt.tooltip_text = "Filter by discipline"
    _disc_opt.item_selected.connect(func(i: int) -> void: set_discipline_filter(_disc_ids[i]))
    head.add_child(_disc_opt)
    _area_opt = OptionButton.new()
    _area_opt.focus_mode = Control.FOCUS_NONE
    _area_opt.tooltip_text = "Filter by area (B: areas panel)"
    _area_opt.item_selected.connect(func(i: int) -> void: set_area_filter(_area_ids[i]))
    head.add_child(_area_opt)
    _mine_box = CheckBox.new()
    _mine_box.text = "My crews only"
    _mine_box.focus_mode = Control.FOCUS_NONE
    _mine_box.tooltip_text = "Only packages in zones where you have a crew of that trade"
    _mine_box.toggled.connect(func(on: bool) -> void: set_mine_only(on))
    head.add_child(_mine_box)
    var fill := Control.new()
    fill.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    head.add_child(fill)
    head.add_child(UiStyle.label("T: toggle", 12, UiStyle.MUTED))
    var close := UiStyle.button("x", "Close the timeline (T)")
    close.pressed.connect(func() -> void: set_open(false))
    head.add_child(close)
    renderer = GanttRenderer.new()
    renderer.gs = gs
    renderer.size_flags_vertical = Control.SIZE_EXPAND_FILL
    renderer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    renderer.zone_selected.connect(func(z: String) -> void: zone_selected.emit(z))
    renderer.model_invalidated.connect(invalidate)
    renderer.zoom_changed.connect(func(_w: int) -> void: _sync_zoom_buttons())
    box.add_child(renderer)
    gs.week_advanced.connect(func(_w: int) -> void: invalidate())
    gs.task_state_changed.connect(func(_a: String, _b: int, _c: int) -> void: invalidate())
    gs.crews_changed.connect(invalidate)
    gs.package_state_changed.connect(func(_i: String, _s: String) -> void: invalidate())
    gs.level_started.connect(_on_level_started)
    _fill_filters()
    _sync_zoom_buttons()
    visible = open
    if open:
        rebuild()
    else:
        set_process(false)


func _on_level_started() -> void:
    storey_filter = ""
    discipline_filter = ""
    area_filter = ""
    mine_only = false
    renderer.collapsed.clear()
    renderer.expanded.clear()
    renderer.follow = true
    _fill_filters()
    invalidate()


# ------------------------------------------------------------------ visibility and layout

func set_open(open: bool) -> void:
    if open == visible:
        return
    visible = open
    if open:
        rebuild()
    layout_changed.emit()


func toggle() -> void:
    set_open(not visible)


## Pixels the panel takes from the bottom of the screen (0 while hidden).
func bottom_inset() -> float:
    if not visible:
        return 0.0
    var parent_h: float = _parent_height()
    return parent_h * height_fraction + MARGIN


func _parent_height() -> float:
    var p: Control = get_parent() as Control
    if p != null and p.size.y > 0.0:
        return p.size.y
    return get_viewport_rect().size.y if is_inside_tree() else 0.0


func set_height_fraction(f: float) -> void:
    height_fraction = clampf(f, MIN_FRACTION, MAX_FRACTION)
    anchor_top = 1.0 - height_fraction
    layout_changed.emit()


func _on_handle_input(event: InputEvent) -> void:
    if event is InputEventMouseButton and (event as InputEventMouseButton).button_index == MOUSE_BUTTON_LEFT:
        _dragging = (event as InputEventMouseButton).pressed
        _handle.queue_redraw()
    elif event is InputEventMouseMotion and _dragging:
        var p: Control = get_parent() as Control
        if p == null or p.size.y <= 0.0:
            return
        var y: float = p.get_local_mouse_position().y
        set_height_fraction((p.size.y - y - MARGIN) / p.size.y)


# ------------------------------------------------------------------ filters and zoom

func _fill_filters() -> void:
    _storey_ids.clear()
    _storey_opt.clear()
    _storey_opt.add_item("All storeys")
    _storey_ids.append("")
    for st in gs.bundle.storeys:
        _storey_opt.add_item(st.name)
        _storey_ids.append(st.id)
    _disc_ids.clear()
    _disc_opt.clear()
    _disc_opt.add_item("All disciplines")
    _disc_ids.append("")
    var seen: Dictionary = {}
    for p in gs.bundle.packages:
        seen[p.discipline] = true
    var names: Array = seen.keys()
    names.sort()
    for d in names:
        _disc_opt.add_item(str(d).capitalize())
        _disc_ids.append(str(d))
    _area_ids.clear()
    _area_opt.clear()
    _area_opt.add_item("All areas")
    _area_ids.append("")
    for a in gs.bundle.areas:
        _area_opt.add_item(str(a["name"]))
        _area_ids.append(str(a["id"]))
    _area_opt.visible = not gs.bundle.areas.is_empty()
    _storey_opt.select(maxi(_storey_ids.find(storey_filter), 0))
    _disc_opt.select(maxi(_disc_ids.find(discipline_filter), 0))
    _area_opt.select(maxi(_area_ids.find(area_filter), 0))
    _mine_box.set_pressed_no_signal(mine_only)


func set_storey_filter(storey_id: String) -> void:
    storey_filter = storey_id
    _storey_opt.select(maxi(_storey_ids.find(storey_id), 0))
    rebuild()


func set_discipline_filter(discipline: String) -> void:
    discipline_filter = discipline
    _disc_opt.select(maxi(_disc_ids.find(discipline), 0))
    rebuild()


func set_area_filter(area_id: String) -> void:
    area_filter = area_id
    _area_opt.select(maxi(_area_ids.find(area_id), 0))
    rebuild()


func set_mine_only(on: bool) -> void:
    mine_only = on
    _mine_box.set_pressed_no_signal(on)
    rebuild()


func set_zoom(weeks: int) -> void:
    renderer.set_zoom(weeks)
    _sync_zoom_buttons()


func _sync_zoom_buttons() -> void:
    for w in _zoom_buttons:
        (_zoom_buttons[w] as Button).set_pressed_no_signal(int(w) == renderer.zoom_weeks)


## Highlights the zone the inspector shows.
func select_zone(zone_id: String) -> void:
    renderer.selected_zone_id = zone_id
    renderer.queue_redraw()


# ------------------------------------------------------------------ model refresh

## Marks the model stale; it is rebuilt once per frame (and only while visible).
func invalidate() -> void:
    _dirty = true
    set_process(true)


func rebuild() -> void:
    _dirty = false
    if gs == null or gs.bundle == null or renderer == null:
        return
    var t0: int = Time.get_ticks_usec()
    var model: Dictionary = GanttModel.build(gs, {"storey_id": storey_filter, "discipline": discipline_filter,
            "mine": mine_only, "expanded": renderer.expanded, "area_id": area_filter})
    renderer.set_model(model)
    model_build_ms = float(Time.get_ticks_usec() - t0) / 1000.0
    rebuild_count += 1


func _process(_delta: float) -> void:
    if _dirty and visible:
        rebuild()
    if not _dirty or not visible:
        set_process(false)
