class_name AreasPanel
extends PanelContainer
## Camera bookmarks for big sites: lists "Whole site" and every area of `project.areas` with its progress; a click (or
## `jump_to`, or the API `view.jump_to_area`) moves the storey focus to the area and frames the camera on its cells.
## Toggled with key B (action `areas_toggle`) or the top bar "B" button. The panel lives in the left dock.

signal area_selected(area_id: String)

const REFRESH_S: float = 1.0
const MAX_LIST_H: float = 300.0

var gs: SimState = null
var view: Node3D = null
## Callable(storey_index: int) that moves the storey focus (Main._set_focus); optional.
var focus_setter: Callable = Callable()
var selected: String = ""
## Pixel padding / building height used when framing an area's cells.
var frame_pad_cells: float = 1.5
var frame_height: float = 3.0

var _list: VBoxContainer = null
var _scroll: ScrollContainer = null
var _rows: Dictionary = {}  # area id -> Button
var _since: float = 0.0


## Creates the panel as the first child of `dock` (the left dock) and registers the camera jump with the API.
static func create(dock: Control, state: SimState, v: Node3D, focus: Callable) -> AreasPanel:
    var p := AreasPanel.new()
    p.name = "AreasPanel"
    dock.add_child(p)
    dock.move_child(p, 0)
    p.setup(state, v, focus)
    return p


func setup(state: SimState, v: Node3D, focus: Callable = Callable()) -> void:
    gs = state
    view = v
    focus_setter = focus
    visible = false
    custom_minimum_size = Vector2(0, 0)
    var box := VBoxContainer.new()
    box.add_theme_constant_override("separation", 3)
    add_child(box)
    var head := HBoxContainer.new()
    box.add_child(head)
    head.add_child(UiStyle.title("Areas"))
    var fill := Control.new()
    fill.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    head.add_child(fill)
    head.add_child(UiStyle.label("B: toggle", 12, UiStyle.MUTED))
    _scroll = ScrollContainer.new()
    _scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
    _scroll.custom_minimum_size = Vector2(0, 40)
    box.add_child(_scroll)
    _list = VBoxContainer.new()
    _list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    _list.add_theme_constant_override("separation", 2)
    _scroll.add_child(_list)
    if gs != null:
        gs.level_started.connect(func() -> void:
            selected = ""
            rebuild()
            _register_api.call_deferred())
        _register_api()
    rebuild()


func _register_api() -> void:
    if gs != null and gs.api != null:
        gs.api.area_handler = Callable(self, "jump_to")


func toggle() -> void:
    visible = not visible
    if visible:
        refresh()


## The rows of the panel: [{id, name, text}] with "Whole site" first.
func entries() -> Array[Dictionary]:
    var out: Array[Dictionary] = [{"id": Areas.SITE_ID, "name": "Whole site", "text": "Whole site"}]
    for a in Areas.list(gs):
        out.append({"id": a["id"], "name": a["name"], "text": "%s   %d zones, %d%%" % [
                a["name"], int(a["zones"]), roundi(float(a["done_share"]) * 100.0)]})
    return out


func rebuild() -> void:
    UiStyle.clear_children(_list)
    _rows.clear()
    if gs == null or gs.bundle == null:
        return
    for e in entries():
        var b := Button.new()
        b.focus_mode = Control.FOCUS_NONE
        b.alignment = HORIZONTAL_ALIGNMENT_LEFT
        b.text = str(e["text"])
        b.toggle_mode = true
        b.tooltip_text = "Jump to %s" % str(e["name"])
        var id: String = str(e["id"])
        b.pressed.connect(func() -> void: jump_to(id))
        _list.add_child(b)
        _rows[id] = b
    _scroll.custom_minimum_size.y = minf(float(_rows.size()) * 30.0 + 4.0, MAX_LIST_H)
    _mark_selected()


func refresh() -> void:
    if gs == null or gs.bundle == null:
        return
    for e in entries():
        var b: Button = _rows.get(str(e["id"]), null)
        if b != null:
            b.text = str(e["text"])
    _mark_selected()


func _mark_selected() -> void:
    for id in _rows:
        (_rows[id] as Button).set_pressed_no_signal(str(id) == selected)


func _process(delta: float) -> void:
    if not visible:
        return
    _since += delta
    if _since >= REFRESH_S:
        _since = 0.0
        refresh()


## Moves the storey focus to the area and frames the camera on its cells ("site" / "": the whole site). An optional
## `camera_bookmark` of the area may carry `zoom` (camera distance) and `yaw` (degrees). Returns false when the area is
## unknown or there is no camera to move.
func jump_to(area_id: String) -> bool:
    if gs == null or gs.bundle == null:
        return false
    var tgt: Dictionary = Areas.target(gs, area_id)
    if tgt.is_empty() or view == null:
        return false
    if focus_setter.is_valid():
        focus_setter.call(int(tgt["storey_index"]))
    elif gs.focus_storey_index != int(tgt["storey_index"]):
        gs.focus_storey_index = int(tgt["storey_index"])
        gs.focus_changed.emit(gs.focus_storey_index)
    var cam: Dictionary = tgt["camera"]
    if bool(tgt["site"]):
        view.call("frame_site", gs.bundle.site_rect, false)
    else:
        var cells: Array = tgt["cells"]
        if cells.is_empty():
            return false
        view.call("frame_cells", cells, frame_height, frame_pad_cells, false)
    if cam.has("yaw"):
        view.set("camera_rotation", Vector3((view.get("camera_rotation") as Vector3).x, float(cam["yaw"]), (view.get("camera_rotation") as Vector3).z))
        view.set("yaw_fixed", true)
    if cam.has("zoom"):
        view.set("zoom", clampf(float(cam["zoom"]), float(view.get("zoom_min")), float(view.get("zoom_max"))))
    selected = str(tgt["id"])
    _mark_selected()
    area_selected.emit(selected)
    return true
