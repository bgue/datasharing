class_name TopBar
extends PanelContainer
## Week / contract week, cash / budget, speed buttons, mode, panel toggles and the
## phase tracker per storey (row of small coloured boxes).

signal speed_requested(speed: int)
signal next_week_requested()
signal panel_toggled(panel_name: String)
signal export_requested()
signal menu_requested()
signal gantt_toggled()

var gs: SimState = null
var _title: Label
var _week: Label
var _cash: Label
var _mode: Label
var _speed_buttons: Dictionary = {}
var _tracker: HBoxContainer
var _dirty: bool = true


func setup(state: SimState) -> void:
    gs = state
    var root := VBoxContainer.new()
    add_child(root)
    var row := HBoxContainer.new()
    root.add_child(row)
    _title = UiStyle.label("", 18, UiStyle.ACCENT)
    _title.custom_minimum_size.x = 150
    _title.clip_text = true
    row.add_child(_title)
    _week = UiStyle.label("")
    _week.custom_minimum_size.x = 130
    row.add_child(_week)
    _cash = UiStyle.label("")
    _cash.custom_minimum_size.x = 250
    row.add_child(_cash)
    var spacer := Control.new()
    spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    row.add_child(spacer)
    _mode = UiStyle.label("", 14, UiStyle.WARN)
    row.add_child(_mode)
    var labels: Array = [["Pause", 0], ["1x", 1], ["2x", 2], ["4x", 4]]
    for l in labels:
        var b := UiStyle.button(str(l[0]), "Speed (Space = pause, 1/2/3 = 1x/2x/4x)")
        b.pressed.connect(func() -> void: speed_requested.emit(int(l[1])))
        row.add_child(b)
        _speed_buttons[int(l[1])] = b
    var nw := UiStyle.button("Next week", "Advance exactly one week")
    nw.pressed.connect(func() -> void: next_week_requested.emit())
    row.add_child(nw)
    for pn in ["Crews", "Zone", "Procure", "Charts"]:
        var tb := UiStyle.button(pn, "Show / hide the %s panel" % pn)
        tb.pressed.connect(func() -> void: panel_toggled.emit(pn))
        row.add_child(tb)
    var gt := UiStyle.button("T", "Timeline (Gantt) panel")
    gt.pressed.connect(func() -> void: gantt_toggled.emit())
    row.add_child(gt)
    var ex := UiStyle.button("Export", "Export the executed plan (F5)")
    ex.pressed.connect(func() -> void: export_requested.emit())
    row.add_child(ex)
    var mn := UiStyle.button("Menu")
    mn.pressed.connect(func() -> void: menu_requested.emit())
    row.add_child(mn)
    _tracker = HBoxContainer.new()
    _tracker.add_theme_constant_override("separation", 14)
    root.add_child(_tracker)
    gs.task_state_changed.connect(func(_a: String, _b: int, _c: int) -> void: _dirty = true)
    gs.week_advanced.connect(func(_w: int) -> void: _dirty = true)
    gs.cash_changed.connect(func(_c: float) -> void: _dirty = true)
    gs.level_started.connect(func() -> void: _dirty = true)
    gs.focus_changed.connect(func(_i: int) -> void: _dirty = true)
    refresh()


func set_mode_text(t: String) -> void:
    _mode.text = t


func refresh() -> void:
    _dirty = false
    if gs == null or gs.bundle == null:
        return
    _title.text = gs.scenario.name
    _week.text = "Week %d / %d" % [gs.week, gs.bundle.contract_weeks()]
    _week.add_theme_color_override("font_color", UiStyle.BAD if gs.week > gs.bundle.contract_weeks() else UiStyle.TEXT)
    _cash.text = "%s / budget %s" % [Fmt.money(gs.cash), Fmt.money(gs.bundle.contract_budget())]
    _cash.add_theme_color_override("font_color", UiStyle.BAD if gs.cash < 0.0 else UiStyle.TEXT)
    for sp in _speed_buttons:
        (_speed_buttons[sp] as Button).disabled = (int(sp) == gs.speed)
    UiStyle.clear_children(_tracker)
    for st in gs.storey_phase_status():
        var box := HBoxContainer.new()
        box.add_theme_constant_override("separation", 3)
        var is_focus: bool = int(st["index"]) == gs.focus_storey_index
        box.add_child(UiStyle.label(str(st["name"]), 13, UiStyle.WARN if is_focus else UiStyle.MUTED))
        for p in st["phases"]:
            var r := ColorRect.new()
            r.custom_minimum_size = Vector2(15, 10)
            var done: int = int(p["done"])
            var total: int = int(p["total"])
            if done >= total:
                r.color = Color(0.3, 0.8, 0.4)
            elif done > 0 or int(p["started"]) > 0:
                r.color = Color(0.35, 0.6, 1.0)
            else:
                r.color = Color(0.32, 0.34, 0.4)
            r.tooltip_text = "%s: %d/%d tasks complete" % [p["name"], done, total]
            r.mouse_filter = Control.MOUSE_FILTER_PASS
            box.add_child(r)
        _tracker.add_child(box)


func _process(_delta: float) -> void:
    if _dirty:
        refresh()
