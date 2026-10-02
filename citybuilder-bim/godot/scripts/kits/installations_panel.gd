class_name InstallationsPanel
extends PanelContainer
## Right-side list of all kit instances ("installations"), grouped by kit type (toggle with action
## `installations_toggle`, key I). Row: name, variant, fill bar, storey. Click = select the instance and frame the
## camera on it (`view.frame_cells`). Also owns the hover picker (tooltip + click selection) so picking works while the
## panel is closed, and registers the camera jump with the API (`view.jump_to_installation`).

signal installation_selected(index: int)

const REFRESH_S: float = 0.5
const PANEL_W: float = 330.0
const RIGHT_DOCK_W: float = 318.0
const ROW_SELECTED: Color = Color(0.25, 0.42, 0.7, 0.9)
const ROW_NORMAL: Color = Color(0.0, 0.0, 0.0, 0.0)
const ROW_HOVER: Color = Color(0.2, 0.26, 0.38, 0.8)

var gs: SimState = null
var bim_view: BimView = null
var view: Node3D = null
var picker: KitPicker = null
var selected: int = -1

var _header: Label = null
var _filter: LineEdit = null
var _list: VBoxContainer = null
var _scroll: ScrollContainer = null
var _rows: Dictionary = {}  # instance index -> {panel, bar, name}
var _group_headers: Array[Label] = []
var _since: float = 0.0
var _last_layer_id: int = 0


## Creates the panel in `parent` (the UI root) and wires picking: one line in main.gd.
static func create(parent: Control, state: SimState, bv: BimView, v: Node3D, cam: Camera3D) -> InstallationsPanel:
    var p := InstallationsPanel.new()
    p.name = "InstallationsPanel"
    parent.add_child(p)
    p.setup(state, bv, v, cam)
    return p


func setup(state: SimState, bv: BimView, v: Node3D, cam: Camera3D = null) -> void:
    gs = state
    bim_view = bv
    view = v
    anchor_left = 1.0
    anchor_right = 1.0
    anchor_top = 0.0
    anchor_bottom = 1.0
    offset_left = -(PANEL_W + RIGHT_DOCK_W + 16.0)  # the slot of the sequence editor, left of the right dock
    offset_right = -(RIGHT_DOCK_W + 16.0)
    offset_top = 74.0
    offset_bottom = -8.0
    grow_horizontal = Control.GROW_DIRECTION_BEGIN
    visible = false
    var box := VBoxContainer.new()
    box.add_theme_constant_override("separation", 4)
    add_child(box)
    var title := Label.new()
    title.text = "Installations"
    title.add_theme_font_size_override("font_size", 18)
    box.add_child(title)
    _header = Label.new()
    _header.add_theme_color_override("font_color", UiStyle.MUTED)
    box.add_child(_header)
    _filter = LineEdit.new()
    _filter.placeholder_text = "Filter (name, kit, zone)"
    _filter.clear_button_enabled = true
    _filter.text_changed.connect(func(_t: String) -> void: rebuild())
    box.add_child(_filter)
    _scroll = ScrollContainer.new()
    _scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
    _scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
    box.add_child(_scroll)
    _list = VBoxContainer.new()
    _list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    _list.add_theme_constant_override("separation", 1)
    _scroll.add_child(_list)
    picker = KitPicker.new()
    picker.name = "KitPicker"
    picker.kit_layer_provider = Callable(self, "kit_layer")
    picker.camera = cam
    add_child(picker)
    picker.installation_selected.connect(func(i: int) -> void: select(i, false, true))
    if gs != null:
        gs.level_started.connect(func() -> void: _on_level_started.call_deferred())
        if gs.api != null:
            gs.api.jump_handler = Callable(self, "jump_to")
    rebuild()


func _on_level_started() -> void:
    selected = -1
    rebuild()
    if gs != null and gs.api != null:
        gs.api.jump_handler = Callable(self, "jump_to")


func kit_layer() -> KitLayer:
    return bim_view.kit_layer() if bim_view != null else null


func toggle() -> void:
    visible = not visible
    if visible:
        var ed: Node = _sequence_editor()
        if ed != null and ed.visible and ed.has_method("close_editor"):
            ed.call("close_editor")  # same screen slot
        refresh()


func _sequence_editor() -> Control:
    var p: Node = get_parent()
    return p.get_node_or_null("SequenceEditor") as Control if p != null else null


# ------------------------------------------------------------------ data

## Rows of the current filter, grouped by kit: [{kit, title, items: [index, ...]}].
func groups() -> Array[Dictionary]:
    var layer: KitLayer = kit_layer()
    var out: Array[Dictionary] = []
    if layer == null or layer.kit_instances == null:
        return out
    var needle: String = _filter.text.strip_edges().to_lower() if _filter != null else ""
    var by_kit: Dictionary = {}
    var order: Array[String] = []
    var list: Array[Dictionary] = layer.kit_instances.instances()
    for i in list.size():
        var inst: Dictionary = list[i]
        if needle != "":
            var hay: String = ("%s %s %s %s %s" % [inst["name"], inst["kit"], inst["title"], inst["zone_id"], inst["variant"]]).to_lower()
            if not hay.contains(needle):
                continue
        var kit: String = inst["kit"]
        if not by_kit.has(kit):
            by_kit[kit] = [] as Array[int]
            order.append(kit)
        (by_kit[kit] as Array[int]).append(i)
    order.sort_custom(func(a: String, b: String) -> bool:
        return layer.registry.kit_title(a) < layer.registry.kit_title(b))
    for kit in order:
        out.append({"kit": kit, "title": layer.registry.kit_title(kit), "items": by_kit[kit]})
    return out


func row_count() -> int:
    return _rows.size()


func total_count() -> int:
    var layer: KitLayer = kit_layer()
    return layer.instance_count() if layer != null else 0


func complete_count() -> int:
    var layer: KitLayer = kit_layer()
    var n: int = 0
    if layer != null and layer.kit_instances != null:
        for inst in layer.kit_instances.instances():
            if float(inst["overall_fill"]) >= 0.999:
                n += 1
    return n


func header_text() -> String:
    return "%d installations · %d complete" % [total_count(), complete_count()]


# ------------------------------------------------------------------ list

func rebuild() -> void:
    if _list == null:
        return
    for c in _list.get_children():
        c.queue_free()
        _list.remove_child(c)
    _rows.clear()
    _group_headers.clear()
    var layer: KitLayer = kit_layer()
    if layer == null or layer.kit_instances == null:
        _header.text = "No installations"
        return
    var list: Array[Dictionary] = layer.kit_instances.instances()
    for g in groups():
        var h := Label.new()
        h.text = "%s (%d)" % [g["title"], (g["items"] as Array).size()]
        h.add_theme_color_override("font_color", UiStyle.ACCENT)
        _list.add_child(h)
        _group_headers.append(h)
        for i in g["items"]:
            _list.add_child(_make_row(int(i), list[i]))
    _header.text = header_text()
    _apply_selection()


func _make_row(index: int, inst: Dictionary) -> Control:
    var row := PanelContainer.new()
    row.mouse_filter = Control.MOUSE_FILTER_STOP
    row.add_theme_stylebox_override("panel", UiStyle.box(ROW_NORMAL, ROW_NORMAL, 3, 4))
    var hb := HBoxContainer.new()
    hb.mouse_filter = Control.MOUSE_FILTER_IGNORE
    hb.add_theme_constant_override("separation", 6)
    row.add_child(hb)
    var nm := Label.new()
    nm.text = str(inst["name"])
    nm.clip_text = true
    nm.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    nm.custom_minimum_size = Vector2(120, 0)
    nm.mouse_filter = Control.MOUSE_FILTER_IGNORE
    hb.add_child(nm)
    var vr := Label.new()
    vr.text = str(inst["variant"])
    vr.add_theme_color_override("font_color", UiStyle.MUTED)
    vr.mouse_filter = Control.MOUSE_FILTER_IGNORE
    vr.visible = str(inst["variant"]) != ""
    hb.add_child(vr)
    var bar := ProgressBar.new()
    bar.min_value = 0.0
    bar.max_value = 1.0
    bar.value = float(inst["overall_fill"])
    bar.show_percentage = false
    bar.custom_minimum_size = Vector2(56, 10)
    bar.size_flags_vertical = Control.SIZE_SHRINK_CENTER
    bar.mouse_filter = Control.MOUSE_FILTER_IGNORE
    hb.add_child(bar)
    var st := Label.new()
    st.text = str(inst["storey_id"])
    st.add_theme_color_override("font_color", UiStyle.MUTED)
    st.mouse_filter = Control.MOUSE_FILTER_IGNORE
    hb.add_child(st)
    row.gui_input.connect(func(ev: InputEvent) -> void:
        if ev is InputEventMouseButton and ev.pressed and ev.button_index == MOUSE_BUTTON_LEFT:
            select(index, true, true))
    row.tooltip_text = KitPicker.tooltip_text(inst, kit_layer().registry)
    _rows[index] = {"panel": row, "bar": bar, "name": nm}
    return row


## Updates the fill bars and header from the current instance fills (no rebuild).
func refresh() -> void:
    var layer: KitLayer = kit_layer()
    if layer == null or layer.kit_instances == null:
        return
    if layer.get_instance_id() != _last_layer_id:
        _last_layer_id = layer.get_instance_id()
        rebuild()
        return
    var list: Array[Dictionary] = layer.kit_instances.instances()
    for i in _rows:
        if i < list.size():
            ((_rows[i] as Dictionary)["bar"] as ProgressBar).value = float(list[i]["overall_fill"])
            ((_rows[i] as Dictionary)["panel"] as Control).tooltip_text = KitPicker.tooltip_text(list[i], layer.registry)
    _header.text = header_text()


func _process(delta: float) -> void:
    if not visible:
        return
    var ed: Control = _sequence_editor()
    if ed != null and ed.visible:
        visible = false  # the sequence editor took the slot
        return
    _since += delta
    if _since >= REFRESH_S:
        _since = 0.0
        refresh()


# ------------------------------------------------------------------ selection

## Selects instance `index` (highlights its row, outlines it in the world). `frame` also moves the camera.
func select(index: int, frame: bool = false, emit: bool = true) -> void:
    selected = index
    var layer: KitLayer = kit_layer()
    if layer != null:
        layer.set_selected(index)
    _apply_selection()
    if frame:
        jump_to(index)
    if picker != null:
        picker.selected = index
    if emit:
        installation_selected.emit(index)


func _apply_selection() -> void:
    for i in _rows:
        var sel: bool = int(i) == selected
        ((_rows[i] as Dictionary)["panel"] as PanelContainer).add_theme_stylebox_override("panel",
                UiStyle.box(ROW_SELECTED if sel else ROW_NORMAL, ROW_NORMAL, 3, 4))
    if selected >= 0 and _rows.has(selected) and visible and _scroll != null:
        _scroll.ensure_control_visible.call_deferred((_rows[selected] as Dictionary)["panel"])


## Frames the camera on instance `index` through the view's `frame_cells` (ground height = its storey).
## Returns true when a view moved. `snap` skips the camera smoothing.
func jump_to(index: int, snap: bool = false) -> bool:
    var layer: KitLayer = kit_layer()
    if layer == null or index < 0 or index >= layer.instance_count() or view == null:
        return false
    var inst: Dictionary = layer.kit_instances.instances()[index]
    if not view.has_method("frame_cells"):
        return false
    var cp: Vector3 = view.get("camera_position")
    cp.y = layer.gs.bundle.storey_y_for_index(int(inst["storey_index"]))
    view.set("camera_position", cp)
    view.call("frame_cells", inst["cells"], float(inst["height_m"]) / layer.gs.bundle.cell_size_m, 1.5, snap)
    return true
