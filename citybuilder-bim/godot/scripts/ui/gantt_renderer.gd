class_name GanttRenderer
extends Control
## The timeline: one Control that draws everything with _draw, culled to the visible window
## (docs/05 section 5). The data model comes from outside (`set_model`, see GanttModel); the
## renderer owns zoom, scroll, collapsed storey groups and the expanded (task level) zone.
##
## Coordinates: the plot area starts at `label_w`; `day_to_x` / `x_to_day` convert working days.
## Rows are laid out in content space (`_row_y`) and scrolled by `scroll_y`; `_paint(false)` runs the
## same traversal as `_draw` without drawing, so culling counts work without a renderer.

signal zone_selected(zone_id: String)
## Emitted when the model must be rebuilt (expand / collapse of a zone, an action changed the sim).
signal model_invalidated()
signal zoom_changed(weeks: int)

const ZOOMS: Array[int] = [12, 26, 52]
const DAYS_PER_WEEK: int = 5
const GROUP_H: float = 20.0
const TASK_H: float = 16.0
const BRACKET_H: float = 12.0
const MARKER_H: float = 6.0
const PAD: float = 2.0
const LANE_H_DEFAULT: float = 14.0
const SB_W: float = 10.0

const COL_BG: Color = Color(0.06, 0.07, 0.1, 0.92)
const COL_ROW_A: Color = Color(1, 1, 1, 0.025)
const COL_GROUP: Color = Color(0.2, 0.26, 0.38, 0.9)
const COL_GRID: Color = Color(1, 1, 1, 0.07)
const COL_BASELINE: Color = Color(0.62, 0.65, 0.72, 0.85)
const COL_NOW: Color = Color(1.0, 1.0, 1.0, 0.85)
const COL_CONTRACT: Color = Color(1.0, 0.4, 0.38, 0.6)
const COL_DELIVERY: Color = Color(0.4, 0.9, 1.0)
const COL_INCIDENT: Color = Color(1.0, 0.25, 0.2)
const COL_AMBER: Color = Color(1.0, 0.72, 0.1)
const COL_RED: Color = Color(1.0, 0.3, 0.28)
const COL_BRACKET: Color = Color(0.7, 0.78, 0.95, 0.8)
const COL_SELECTED: Color = Color(0.35, 0.6, 1.0, 0.16)

## Same palette as BimView, so a bar has the colour of the elements it builds.
const DISCIPLINE_COLORS: Dictionary = {
    "general": Color(0.62, 0.62, 0.64),
    "civil": Color(0.55, 0.38, 0.22),
    "structure": Color(0.55, 0.56, 0.58),
    "architecture": Color(0.86, 0.78, 0.6),
    "mechanical": Color(0.25, 0.5, 0.95),
    "electrical": Color(0.98, 0.85, 0.2),
    "plumbing": Color(0.1, 0.65, 0.65),
    "fire": Color(0.9, 0.25, 0.2),
    "process": Color(0.95, 0.5, 0.1),
    "instrumentation": Color(0.6, 0.4, 0.85),
    "medical": Color(0.85, 0.2, 0.7),
    "commissioning": Color(0.35, 0.75, 0.4),
}

enum Menu { HOLD, PRIORITY, CLEAR_CARD }

var gs: SimState = null
var rows: Array = []
var current_day: int = 0
var week: int = 0
var max_day: int = 0
var contract_day: int = 0
var zoom_weeks: int = 26
## Window start (working day, may be fractional while scrolling).
var from_day: float = 0.0
## True until the user scrolls: the window then follows max(0, week - 2) as the game advances.
var follow: bool = true
var scroll_y: float = 0.0
var collapsed: Dictionary = {}  # storey_id -> true
var expanded: Dictionary = {}  # zone_id -> true (task rows)
var selected_zone_id: String = ""
var allow_expand: bool = true
var allow_menu: bool = true
var show_labels: bool = true
var show_scrollbars: bool = true
## Squeeze the lanes of a single zone into the control height (lane view).
var fit_height: bool = false
var axis_h: float = 18.0
var label_w: float = 150.0

## Measured by _draw (milliseconds), plus the culling counters of the last paint.
var draw_time_ms: float = 0.0
var drawn_rows: int = 0
var drawn_bars: int = 0
## Number of times _draw ran (stays 0 where the canvas never draws).
var draw_count: int = 0

var _vis: Array[int] = []  # indices into rows, in display order
var _row_y: PackedFloat32Array = PackedFloat32Array()
var _row_h: PackedFloat32Array = PackedFloat32Array()
var _content_h: float = 0.0
var _lane_h: float = LANE_H_DEFAULT
var _hover_pid: String = ""
var _hbar: HScrollBar = null
var _vbar: VScrollBar = null
var _menu: PopupMenu = null
var _card_menu: PopupMenu = null
var _card_ids: Array[String] = []
var _prio_popup: PopupPanel = null
var _prio_spin: SpinBox = null
var _ctx_bar: Dictionary = {}
## Lazy models (GanttModel._build_lazy): fills the bars of a zone row when it is first drawn or hit-tested.
var detail: Callable = Callable()
var _zone_row: Dictionary = {}  # zone id -> index into rows
## Rows whose bars were filled since the model was set (tests: only the visible rows are built).
var detail_built: int = 0


func _init() -> void:
    clip_contents = true
    mouse_filter = Control.MOUSE_FILTER_STOP
    focus_mode = Control.FOCUS_NONE
    custom_minimum_size = Vector2(200, 40)
    _hbar = HScrollBar.new()
    _hbar.value_changed.connect(_on_hbar)
    add_child(_hbar)
    _vbar = VScrollBar.new()
    _vbar.value_changed.connect(_on_vbar)
    add_child(_vbar)


# ------------------------------------------------------------------ model and window

func set_model(model: Dictionary) -> void:
    rows = model.get("rows", [])
    detail = model.get("detail", Callable()) if model.get("detail", null) is Callable else Callable()
    _zone_row.clear()
    for i in rows.size():
        if str((rows[i] as Dictionary)["kind"]) == "zone":
            _zone_row[str((rows[i] as Dictionary)["zone_id"])] = i
    current_day = int(model.get("current_day", 0))
    week = int(model.get("week", 0))
    max_day = int(model.get("max_day", 0))
    contract_day = int(model.get("contract_day", 0))
    if follow:
        from_day = default_from_day()
    _clamp_window()
    _layout()
    queue_redraw()


func default_from_day() -> float:
    return float(maxi(0, week - 2) * DAYS_PER_WEEK)


func window_days() -> float:
    return float(zoom_weeks * DAYS_PER_WEEK)


func to_day() -> float:
    return from_day + window_days()


func set_zoom(weeks: int) -> void:
    if not ZOOMS.has(weeks):
        return
    zoom_weeks = weeks
    follow = true
    from_day = default_from_day()
    _clamp_window()
    _sync_bars()
    zoom_changed.emit(weeks)
    queue_redraw()


## Scrolls the time window by whole days (negative = earlier) and stops following the game week.
func scroll_days(delta: float) -> void:
    follow = false
    from_day += delta
    _clamp_window()
    _sync_bars()
    queue_redraw()


func scroll_rows(delta: float) -> void:
    scroll_y = clampf(scroll_y + delta, 0.0, _max_scroll_y())
    _sync_bars()
    queue_redraw()


func _max_from_day() -> float:
    return maxf(0.0, float(maxi(max_day, current_day + 25)) - window_days() * 0.5)


func _clamp_window() -> void:
    var hi: float = _max_from_day()
    if follow:
        hi = maxf(hi, default_from_day())  # following the game week is always allowed
    from_day = clampf(from_day, 0.0, hi)


func plot_width() -> float:
    return maxf(size.x - _label_w() - (SB_W if show_scrollbars else 0.0), 1.0)


func _label_w() -> float:
    return label_w if show_labels else 0.0


func pixels_per_day() -> float:
    return plot_width() / window_days()


func day_to_x(day: float) -> float:
    return _label_w() + (day - from_day) * pixels_per_day()


func x_to_day(x: float) -> float:
    return from_day + (x - _label_w()) / pixels_per_day()


func _view_height() -> float:
    return maxf(size.y - axis_h - (SB_W if show_scrollbars else 0.0), 1.0)


func _max_scroll_y() -> float:
    return maxf(0.0, _content_h - _view_height())


# ------------------------------------------------------------------ layout

func _zone_row_height(r: Dictionary) -> float:
    var h: float = PAD * 2.0 + float(r["lanes"]) * _lane_h + MARKER_H
    if not (r["stations"] as Array).is_empty():
        h += BRACKET_H
    return h


func _layout() -> void:
    _vis.clear()
    _row_y.clear()
    _row_h.clear()
    _lane_h = LANE_H_DEFAULT
    if fit_height:
        var lanes: int = 1
        var overhead: float = PAD * 2.0 + MARKER_H
        for r in rows:
            if r["kind"] == "zone":
                lanes = maxi(lanes, int(r["lanes"]))
                if not (r["stations"] as Array).is_empty():
                    overhead += BRACKET_H
                break
        _lane_h = clampf((size.y - axis_h - overhead) / float(lanes), 3.0, 12.0)
    var y: float = 0.0
    var group_collapsed: bool = false
    for i in rows.size():
        var r: Dictionary = rows[i]
        var h: float = 0.0
        match str(r["kind"]):
            "group":
                group_collapsed = bool(collapsed.get(r["storey_id"], false))
                h = GROUP_H
            "zone":
                if group_collapsed:
                    continue
                h = _zone_row_height(r)
            "task":
                if group_collapsed:
                    continue
                h = TASK_H
        _vis.append(i)
        _row_y.append(y)
        _row_h.append(h)
        y += h
    _content_h = y
    scroll_y = clampf(scroll_y, 0.0, _max_scroll_y())
    _sync_bars()


func _sync_bars() -> void:
    if _hbar == null:
        return
    _hbar.visible = show_scrollbars
    _vbar.visible = show_scrollbars and _max_scroll_y() > 0.0
    _hbar.position = Vector2(_label_w(), size.y - SB_W)
    _hbar.size = Vector2(maxf(size.x - _label_w() - SB_W, 1.0), SB_W)
    _vbar.position = Vector2(size.x - SB_W, axis_h)
    _vbar.size = Vector2(SB_W, maxf(size.y - axis_h - SB_W, 1.0))
    _hbar.min_value = 0.0
    _hbar.max_value = _max_from_day() + window_days()
    _hbar.page = window_days()
    _hbar.set_value_no_signal(from_day)
    _vbar.min_value = 0.0
    _vbar.max_value = _content_h
    _vbar.page = _view_height()
    _vbar.set_value_no_signal(scroll_y)


func _on_hbar(v: float) -> void:
    follow = false
    from_day = v
    _clamp_window()
    queue_redraw()


func _on_vbar(v: float) -> void:
    scroll_y = clampf(v, 0.0, _max_scroll_y())
    queue_redraw()


func _notification(what: int) -> void:
    if what == NOTIFICATION_RESIZED:
        _layout()
        queue_redraw()


## Index (into _vis) of the first row that ends below `content_y`.
func _first_visible(content_y: float) -> int:
    var lo: int = 0
    var hi: int = _vis.size()
    while lo < hi:
        var mid: int = (lo + hi) >> 1
        if _row_y[mid] + _row_h[mid] <= content_y:
            lo = mid + 1
        else:
            hi = mid
    return lo


## Builds the bars of a lazy zone row (a no-op for other rows). Returns the row.
func ensure_row(r: Dictionary) -> Dictionary:
    if detail.is_valid() and bool(r.get("lazy", false)) and not bool(r.get("built", false)):
        detail.call(r)
        detail_built += 1
    return r


## Builds every lazy row (tests, exports).
func ensure_all_rows() -> void:
    for r in rows:
        ensure_row(r)


## Number of zone rows whose bars exist.
func built_row_count() -> int:
    var n: int = 0
    for r in rows:
        if str(r["kind"]) == "zone" and (not bool(r.get("lazy", false)) or bool(r.get("built", false))):
            n += 1
    return n


## Row dictionaries in display order (rows of collapsed groups excluded).
func display_rows() -> Array:
    var out: Array = []
    for i in _vis:
        out.append(rows[i])
    return out


func is_collapsed(storey_id: String) -> bool:
    return bool(collapsed.get(storey_id, false))


func toggle_collapsed(storey_id: String) -> void:
    if collapsed.has(storey_id):
        collapsed.erase(storey_id)
    else:
        collapsed[storey_id] = true
    _layout()
    queue_redraw()


## Task-level view of one zone at a time. Asks the owner to rebuild the model.
func toggle_expanded(zone_id: String) -> void:
    if not allow_expand:
        return
    var was: bool = bool(expanded.get(zone_id, false))
    expanded.clear()
    if not was:
        expanded[zone_id] = true
    model_invalidated.emit()


# ------------------------------------------------------------------ styles

static func discipline_color(discipline: String) -> Color:
    return DISCIPLINE_COLORS.get(discipline, DISCIPLINE_COLORS["general"])


## How a bar is drawn, from its flags (pure, tested): base / fill colours, hatching when held, red
## outline when understaffed, amber fill when behind takt, dimmed when done.
static func style_for(bar: Dictionary) -> Dictionary:
    var disc: Color = discipline_color(str(bar.get("discipline", "general")))
    var state: String = str(bar.get("state", ""))
    var done: bool = state == "done" or state == "inspected"
    var held: bool = bool(bar.get("held", false))
    var under: bool = bool(bar.get("understaffed", false))
    var behind: bool = bool(bar.get("behind_takt", false))
    var fill_col: Color = COL_AMBER if behind else disc
    var alpha: float = 0.55 if done else 1.0
    if held:
        alpha *= 0.7
    return {
        "base_color": Color(disc.r * 0.35, disc.g * 0.35, disc.b * 0.35, 0.9 * alpha),
        "fill_color": Color(fill_col.r, fill_col.g, fill_col.b, alpha),
        "baseline_color": COL_BASELINE,
        "fill": clampf(float(bar.get("progress", 0.0)), 0.0, 1.0),
        "hatched": held,
        "hatch_color": Color(1, 1, 1, 0.55),
        "outline_color": COL_RED if under else Color(0, 0, 0, 0),
        "outline_width": 2.0 if under else 0.0,
        "behind_takt": behind,
        "done": done,
    }


static func tooltip_text(bar: Dictionary) -> String:
    var lines: Array[String] = [str(bar.get("name", ""))]
    var st: String = str(bar.get("state", ""))
    var flags: String = ""
    if bool(bar.get("behind_takt", false)):
        flags += "  (behind takt)"
    if bool(bar.get("understaffed", false)) and st != "understaffed":
        flags += "  (understaffed)"
    lines.append("State: %s%s" % [st.replace("_", " "), flags])
    lines.append("Crews: now %d / ideal %d / max %d (min %d)" % [int(bar.get("crews_now", 0)), int(bar.get("crew_ideal", 0)),
            int(bar.get("crew_max", 0)), int(bar.get("crew_min", 0))])
    lines.append("Remaining: %.1f crew-days (%d%% done)" % [float(bar.get("remaining_crew_days", 0.0)), int(round(100.0 * float(bar.get("progress", 0.0))))])
    var br: String = str(bar.get("blocked_reason", ""))
    if br != "":
        lines.append("Blocked: %s" % br)
    var stn: String = str(bar.get("station", ""))
    if stn != "":
        lines.append("Station: %s" % stn)
    var plan: String = "Plan: day %d to %d" % [int(bar["planned_start_day"]), int(bar["planned_finish_day"])]
    if bar.get("actual_start_day", null) != null:
        plan += ", started day %d" % int(bar["actual_start_day"])
    if bar.get("actual_finish_day", null) != null:
        plan += ", finished day %d" % int(bar["actual_finish_day"])
    lines.append(plan)
    return "\n".join(lines)


# ------------------------------------------------------------------ geometry and hit testing

func _lane_top(vi: int, r: Dictionary) -> float:
    var y: float = _row_y[vi] + PAD
    if not (r["stations"] as Array).is_empty():
        y += BRACKET_H
    return y


## Control-space rectangle of a bar's drawn extent (its main span) in row `vi`.
func _bar_rect(vi: int, r: Dictionary, bar: Dictionary) -> Rect2:
    var x0: float = day_to_x(float(bar["span_start"]))
    var x1: float = day_to_x(float(bar["span_end"]))
    var y: float
    var h: float
    if str(r["kind"]) == "task":
        y = _row_y[vi] + 3.0
        h = TASK_H - 7.0
    else:
        y = _lane_top(vi, r) + float(bar["lane"]) * _lane_h
        h = _lane_h
    return Rect2(x0, y - scroll_y + axis_h, maxf(x1 - x0, 2.0), h)


## Rectangle of the package's bar in control coordinates (empty when its group is collapsed).
func bar_rect(package_id: String) -> Rect2:
    if detail.is_valid() and gs != null and gs.bundle.packages_by_id.has(package_id):
        var zi: int = int(_zone_row.get(gs.bundle.packages_by_id[package_id].zone_id, -1))
        if zi >= 0:
            ensure_row(rows[zi])
    for vi in _vis.size():
        var r: Dictionary = rows[_vis[vi]]
        if str(r["kind"]) != "zone":
            continue
        for b in r["bars"]:
            if str(b["package_id"]) == package_id:
                return _bar_rect(vi, r, b)
    return Rect2()


## Index into _vis of the row under a control-space point, -1 outside the rows.
func _vis_at(point: Vector2) -> int:
    if point.y < axis_h or point.y >= size.y or _vis.is_empty():
        return -1
    var cy: float = point.y - axis_h + scroll_y
    var vi: int = _first_visible(cy)
    if vi >= _vis.size() or _row_y[vi] > cy:
        return -1
    return vi


func row_at(point: Vector2) -> Dictionary:
    var vi: int = _vis_at(point)
    return ensure_row(rows[_vis[vi]]) if vi >= 0 else {}


## The bar under a control-space point ({} when none): a package bar in a zone row (planned or drawn extent)
## or the single bar of a task row.
func bar_at(point: Vector2) -> Dictionary:
    var vi: int = _vis_at(point)
    if vi < 0 or point.x < _label_w():
        return {}
    var r: Dictionary = ensure_row(rows[_vis[vi]])
    var kind: String = str(r["kind"])
    if kind == "group":
        return {}
    var cy: float = point.y - axis_h + scroll_y
    if kind == "task":
        for b in r["bars"]:
            var rc: Rect2 = _bar_rect(vi, r, b)
            if point.x >= rc.position.x and point.x <= rc.end.x:
                return b
        return {}
    var lane: int = int(floor((cy - _lane_top(vi, r)) / _lane_h))
    if lane < 0:
        return {}
    for b in r["bars"]:
        if int(b["lane"]) != lane:
            continue
        var x0: float = day_to_x(float(mini(int(b["planned_start_day"]), int(b["span_start"]))))
        var x1: float = day_to_x(float(maxi(int(b["planned_finish_day"]), int(b["span_end"]))))
        if point.x >= x0 and point.x <= maxf(x1, x0 + 2.0):
            return b
    return {}


func tooltip_for(point: Vector2) -> String:
    var b: Dictionary = bar_at(point)
    if not b.is_empty():
        return tooltip_text(b)
    var vi: int = _vis_at(point)
    if vi >= 0:
        var r: Dictionary = rows[_vis[vi]]
        if str(r["kind"]) == "group":
            return "%s: %d zones, %d packages (click to collapse)" % [r["name"], int(r["zone_count"]), int(r["bar_count"])]
        if str(r["kind"]) == "zone" and point.x < _label_w():
            return "%s\nClick: select, double-click: show tasks" % str(r["name"])
    return ""


func _get_tooltip(at_position: Vector2) -> String:
    return tooltip_for(at_position)


# ------------------------------------------------------------------ input

func _gui_input(event: InputEvent) -> void:
    if event is InputEventMouseMotion:
        var b: Dictionary = bar_at((event as InputEventMouseMotion).position)
        var pid: String = str(b.get("package_id", "")) if not b.is_empty() else ""
        if pid != _hover_pid:
            _hover_pid = pid
            queue_redraw()
    elif event is InputEventMouseButton:
        _on_mouse_button(event as InputEventMouseButton)


func _on_mouse_button(e: InputEventMouseButton) -> void:
    if not e.pressed:
        return
    match e.button_index:
        MOUSE_BUTTON_WHEEL_UP:
            if e.shift_pressed:
                scroll_days(-float(DAYS_PER_WEEK))
            else:
                scroll_rows(-30.0)
            accept_event()
        MOUSE_BUTTON_WHEEL_DOWN:
            if e.shift_pressed:
                scroll_days(float(DAYS_PER_WEEK))
            else:
                scroll_rows(30.0)
            accept_event()
        MOUSE_BUTTON_WHEEL_LEFT:
            scroll_days(-float(DAYS_PER_WEEK))
            accept_event()
        MOUSE_BUTTON_WHEEL_RIGHT:
            scroll_days(float(DAYS_PER_WEEK))
            accept_event()
        MOUSE_BUTTON_LEFT:
            click_at(e.position, e.double_click)
            accept_event()
        MOUSE_BUTTON_RIGHT:
            var b: Dictionary = bar_at(e.position)
            if not b.is_empty() and allow_menu and gs != null and not bool(b.get("is_task", false)):
                show_context_menu(b, e.position)
                accept_event()


## Left click / double click at a control-space point (also used by tests).
func click_at(point: Vector2, double_click: bool = false) -> void:
    var vi: int = _vis_at(point)
    if vi < 0:
        return
    var r: Dictionary = rows[_vis[vi]]
    match str(r["kind"]):
        "group":
            toggle_collapsed(str(r["storey_id"]))
        "zone", "task":
            var zid: String = str(r["zone_id"])
            selected_zone_id = zid
            queue_redraw()
            zone_selected.emit(zid)
            if double_click:
                toggle_expanded(zid)


# ------------------------------------------------------------------ actions (context menu)

func toggle_hold(package_id: String) -> bool:
    if gs == null or not gs.package_runtime.has(package_id):
        return false
    var released: bool = (gs.package_runtime[package_id] as PackageRuntime).released
    var ok: bool = gs.hold_package(package_id) if released else gs.release_package(package_id)
    model_invalidated.emit()
    return ok


func set_priority(package_id: String, value: int) -> bool:
    if gs == null:
        return false
    var ok: bool = gs.set_package_priority(package_id, value)
    model_invalidated.emit()
    return ok


func apply_card(zone_id: String, card_id: String) -> bool:
    if gs == null:
        return false
    var ok: bool = Cards.apply(gs, zone_id, card_id)
    model_invalidated.emit()
    return ok


func clear_card(zone_id: String) -> bool:
    if gs == null:
        return false
    var ok: bool = Cards.clear(gs, zone_id)
    model_invalidated.emit()
    return ok


func show_context_menu(bar: Dictionary, local_pos: Vector2) -> void:
    _ensure_menus()
    _ctx_bar = bar
    var held: bool = not bool(bar.get("released", true))
    _menu.set_item_text(_menu.get_item_index(Menu.HOLD), "Release package" if held else "Hold package")
    var zr: ZoneRuntime = gs.zone_runtime.get(str(bar["zone_id"]), null)
    _menu.set_item_disabled(_menu.get_item_index(Menu.CLEAR_CARD), zr == null or zr.card_id == "")
    _menu.position = Vector2i(get_screen_transform() * local_pos)
    _menu.popup()


func _ensure_menus() -> void:
    if _menu != null:
        return
    _menu = PopupMenu.new()
    _menu.add_item("Hold package", Menu.HOLD)
    _menu.add_item("Set priority...", Menu.PRIORITY)
    _card_menu = PopupMenu.new()
    _card_menu.name = "ApplyCard"
    for c in gs.bundle.card_list():
        _card_menu.add_item(c.name)
        _card_ids.append(c.id)
    _card_menu.id_pressed.connect(_on_card_menu)
    _menu.add_child(_card_menu)
    _menu.add_submenu_node_item("Apply card", _card_menu)
    _menu.add_item("Clear card", Menu.CLEAR_CARD)
    _menu.id_pressed.connect(_on_menu)
    add_child(_menu)
    _prio_popup = PopupPanel.new()
    var box := HBoxContainer.new()
    box.add_child(UiStyle.label("Priority (lower first)", 13))
    _prio_spin = SpinBox.new()
    _prio_spin.min_value = -1000
    _prio_spin.max_value = 100000
    _prio_spin.step = 1
    box.add_child(_prio_spin)
    var okb := UiStyle.button("OK")
    okb.pressed.connect(func() -> void:
        _prio_popup.hide()
        if not _ctx_bar.is_empty():
            set_priority(str(_ctx_bar["package_id"]), int(_prio_spin.value)))
    box.add_child(okb)
    _prio_popup.add_child(box)
    add_child(_prio_popup)


func _on_menu(id: int) -> void:
    if _ctx_bar.is_empty():
        return
    match id:
        Menu.HOLD:
            toggle_hold(str(_ctx_bar["package_id"]))
        Menu.PRIORITY:
            _prio_spin.value = int(_ctx_bar.get("priority", 0))
            _prio_popup.popup(Rect2i(_menu.position, Vector2i(320, 44)))
        Menu.CLEAR_CARD:
            clear_card(str(_ctx_bar["zone_id"]))


func _on_card_menu(index: int) -> void:
    if _ctx_bar.is_empty() or index < 0 or index >= _card_ids.size():
        return
    apply_card(str(_ctx_bar["zone_id"]), _card_ids[index])


# ------------------------------------------------------------------ drawing

func _draw() -> void:
    draw_count += 1
    _paint(true)


## Number of bars the current window would draw (same traversal as _draw, nothing is drawn).
func count_visible() -> int:
    _paint(false)
    return drawn_bars


func _paint(canvas: bool) -> void:
    var t0: int = Time.get_ticks_usec()
    drawn_rows = 0
    drawn_bars = 0
    var font: Font = UiStyle.font()
    var lw: float = _label_w()
    var right: float = size.x - (SB_W if show_scrollbars else 0.0)
    var bottom: float = size.y - (SB_W if show_scrollbars else 0.0)
    var ppd: float = pixels_per_day()
    if canvas:
        draw_rect(Rect2(Vector2.ZERO, size), COL_BG, true)
        _draw_grid(lw, right, bottom)
    var first: int = _first_visible(scroll_y)
    var last: int = first
    for vi in range(first, _vis.size()):
        var ry: float = _row_y[vi] - scroll_y + axis_h
        if ry > bottom:
            break
        last = vi + 1
        drawn_rows += 1
        var r: Dictionary = ensure_row(rows[_vis[vi]])
        var rh: float = _row_h[vi]
        var kind: String = str(r["kind"])
        if kind == "group":
            if canvas:
                draw_rect(Rect2(0, ry, right, rh - 1.0), COL_GROUP, true)
            continue
        if canvas:
            if vi % 2 == 0:
                draw_rect(Rect2(lw, ry, right - lw, rh), COL_ROW_A, true)
            if str(r["zone_id"]) == selected_zone_id:
                draw_rect(Rect2(0, ry, right, rh), COL_SELECTED, true)
        if kind == "task":
            var tb: Dictionary = (r["bars"] as Array)[0]
            if _bar_visible(tb, lw, right):
                drawn_bars += 1
                if canvas:
                    _draw_bar(tb, Rect2(day_to_x(float(tb["span_start"])), ry + 3.0, 0.0, TASK_H - 7.0), font, false)
            continue
        var top: float = ry + PAD
        var stations: Array = r["stations"]
        if not stations.is_empty():
            if canvas:
                _draw_brackets(stations, top, lw, right, font)
            top += BRACKET_H
        for b in r["bars"]:
            var bar: Dictionary = b
            if not _bar_visible(bar, lw, right):
                continue
            drawn_bars += 1
            if canvas:
                _draw_bar(bar, Rect2(day_to_x(float(bar["span_start"])), top + float(bar["lane"]) * _lane_h, 0.0, _lane_h), font, true)
        if canvas:
            _draw_markers(r, ry, rh, lw, right)
    if canvas:
        _draw_labels(first, last, font, lw)
        _draw_axis(font, lw, right)
        _draw_now(lw, bottom)
    if canvas:
        draw_time_ms = float(Time.get_ticks_usec() - t0) / 1000.0


func _bar_visible(bar: Dictionary, lw: float, right: float) -> bool:
    var x0: float = day_to_x(float(mini(int(bar["planned_start_day"]), int(bar["span_start"]))))
    var x1: float = day_to_x(float(maxi(int(bar["planned_finish_day"]), int(bar["span_end"]))))
    return x1 >= lw and x0 <= right


## `at.position` = top-left of the drawn span, `at.size.y` = lane height.
func _draw_bar(bar: Dictionary, at: Rect2, font: Font, with_baseline: bool) -> void:
    var st: Dictionary = style_for(bar)
    var x0: float = at.position.x
    var x1: float = day_to_x(float(bar["span_end"]))
    var w: float = maxf(x1 - x0, 2.0)
    var lane_h: float = at.size.y
    var base_h: float = 0.0
    if with_baseline:
        base_h = 2.0 if lane_h >= 8.0 else 1.0
        var px0: float = day_to_x(float(bar["planned_start_day"]))
        var px1: float = day_to_x(float(bar["planned_finish_day"]))
        draw_rect(Rect2(px0, at.position.y + lane_h - base_h - 0.5, maxf(px1 - px0, 2.0), base_h), st["baseline_color"], true)
    var mh: float = maxf(lane_h - base_h - 2.0, 2.0)
    var main := Rect2(x0, at.position.y + 1.0, w, mh)
    draw_rect(main, st["base_color"], true)
    var fw: float = w * float(st["fill"])
    if fw > 0.5:
        draw_rect(Rect2(main.position, Vector2(fw, mh)), st["fill_color"], true)
    if bool(st["hatched"]):
        _draw_hatch(main, st["hatch_color"])
    if float(st["outline_width"]) > 0.0:
        draw_rect(main, st["outline_color"], false, float(st["outline_width"]))
    if _hover_pid != "" and str(bar.get("package_id", "")) == _hover_pid:
        draw_rect(main, Color(1, 1, 1, 0.9), false, 1.0)
    if w > 60.0 and mh >= 10.0:
        var maxc: int = int((w - 6.0) / 6.5)
        var txt: String = str(bar.get("name", ""))
        if txt.length() > maxc:
            txt = txt.substr(0, maxi(maxc - 1, 1)) + "."
        # light text on a dark outline reads on both the bright filled part and the dark unfilled part of the bar
        var tp := Vector2(main.position.x + 3.0, main.position.y + mh - 3.0)
        draw_string_outline(font, tp, txt, HORIZONTAL_ALIGNMENT_LEFT, -1, 11, 4, Color(0.03, 0.03, 0.06, 0.95))
        draw_string(font, tp, txt, HORIZONTAL_ALIGNMENT_LEFT, -1, 11, Color(0.97, 0.97, 1.0))


## Diagonal lines every 6 px, clipped to the rectangle, as one draw call.
func _draw_hatch(rc: Rect2, col: Color) -> void:
    var pts := PackedVector2Array()
    var h: float = rc.size.y
    var o: float = -h
    while o < rc.size.x:
        var xa: float = o
        var ya: float = h
        var xb: float = o + h
        var yb: float = 0.0
        if xa < 0.0:
            ya = h + xa
            xa = 0.0
        if xb > rc.size.x:
            yb = xb - rc.size.x
            xb = rc.size.x
        if xb > xa:
            pts.append(rc.position + Vector2(xa, ya))
            pts.append(rc.position + Vector2(xb, yb))
        o += 6.0
    if not pts.is_empty():
        draw_multiline(pts, col, 1.0)


func _draw_brackets(stations: Array, top: float, lw: float, right: float, font: Font) -> void:
    for s in stations:
        var d: Dictionary = s
        var x0: float = day_to_x(float(d["start_day"]))
        var x1: float = day_to_x(float(d["end_day"]))
        if x1 < lw or x0 > right:
            continue
        var col: Color = COL_AMBER if bool(d["behind_takt"]) else (Color(1, 1, 1, 0.95) if bool(d["current"]) else COL_BRACKET)
        var pts := PackedVector2Array([Vector2(x0 + 1.0, top + BRACKET_H - 2.0), Vector2(x0 + 1.0, top + 2.0),
                Vector2(x1 - 1.0, top + 2.0), Vector2(x1 - 1.0, top + BRACKET_H - 2.0)])
        draw_polyline(pts, col, 1.0)
        if x1 - x0 > 40.0:
            var txt: String = str(d["name"])
            var maxc: int = int((x1 - x0 - 6.0) / 5.5)
            if txt.length() > maxc:
                txt = txt.substr(0, maxi(maxc - 1, 1)) + "."
            draw_string(font, Vector2(maxf(x0, lw) + 4.0, top + BRACKET_H - 3.0), txt, HORIZONTAL_ALIGNMENT_LEFT, -1, 9, col)


func _draw_markers(r: Dictionary, ry: float, rh: float, lw: float, right: float) -> void:
    for d in r["incidents"]:
        var x: float = day_to_x(float(d))
        if x >= lw and x <= right:
            draw_line(Vector2(x, ry), Vector2(x, ry + rh), COL_INCIDENT, 2.0)
    var my: float = ry + rh - MARKER_H * 0.5 - 1.0
    for d in r["deliveries"]:
        var x: float = day_to_x(float(d))
        if x >= lw - 4.0 and x <= right + 4.0:
            draw_colored_polygon(PackedVector2Array([Vector2(x - 4, my), Vector2(x, my - 4), Vector2(x + 4, my), Vector2(x, my + 4)]), COL_DELIVERY)


func _draw_labels(first: int, last: int, font: Font, lw: float) -> void:
    var bottom: float = size.y - (SB_W if show_scrollbars else 0.0)
    if not show_labels:
        for vi in range(first, last):
            var rg: Dictionary = rows[_vis[vi]]
            if str(rg["kind"]) == "group":
                _draw_group_label(rg, _row_y[vi] - scroll_y + axis_h, font, 4.0)
        return
    draw_rect(Rect2(0, axis_h, lw, bottom - axis_h), Color(0.06, 0.07, 0.1, 1.0), true)
    for vi in range(first, last):
        var r: Dictionary = rows[_vis[vi]]
        var ry: float = _row_y[vi] - scroll_y + axis_h
        var rh: float = _row_h[vi]
        match str(r["kind"]):
            "group":
                draw_rect(Rect2(0, ry, lw, rh - 1.0), COL_GROUP, true)
                _draw_group_label(r, ry, font, 4.0)
            "zone":
                if str(r["zone_id"]) == selected_zone_id:
                    draw_rect(Rect2(0, ry, lw, rh), COL_SELECTED, true)
                var txt: String = str(r["name"])
                if txt.length() > 20:
                    txt = txt.substr(0, 19) + "."
                var col: Color = UiStyle.TEXT if str(r["card_id"]) == "" else UiStyle.ACCENT
                draw_string(font, Vector2(14.0, ry + minf(rh * 0.5 + 5.0, 20.0)), ("- " if bool(r["expanded"]) else "") + txt,
                        HORIZONTAL_ALIGNMENT_LEFT, -1, 12, col)
            "task":
                var tt: String = str(r["name"])
                if tt.length() > 24:
                    tt = tt.substr(0, 23) + "."
                draw_string(font, Vector2(26.0, ry + TASK_H - 4.0), tt, HORIZONTAL_ALIGNMENT_LEFT, -1, 10, UiStyle.MUTED)
    draw_line(Vector2(lw, axis_h), Vector2(lw, bottom), UiStyle.BORDER, 1.0)


func _draw_group_label(r: Dictionary, ry: float, font: Font, x: float) -> void:
    var cy: float = ry + GROUP_H * 0.5
    var tri: PackedVector2Array
    if is_collapsed(str(r["storey_id"])):
        tri = PackedVector2Array([Vector2(x + 2, cy - 5), Vector2(x + 2, cy + 5), Vector2(x + 9, cy)])
    else:
        tri = PackedVector2Array([Vector2(x + 1, cy - 3), Vector2(x + 11, cy - 3), Vector2(x + 6, cy + 4)])
    draw_colored_polygon(tri, UiStyle.TEXT)
    draw_string(font, Vector2(x + 16.0, ry + GROUP_H - 6.0), "%s  (%d zones, %d pkgs)" % [r["name"], int(r["zone_count"]), int(r["bar_count"])],
            HORIZONTAL_ALIGNMENT_LEFT, -1, 12, UiStyle.TEXT)


func _week_step() -> int:
    return 1 if zoom_weeks <= 12 else (2 if zoom_weeks <= 26 else 4)


func _draw_grid(lw: float, right: float, bottom: float) -> void:
    var step: int = _week_step()
    var w: int = int(floor(from_day / float(DAYS_PER_WEEK)))
    w -= w % step
    var w1: int = int(ceil(to_day() / float(DAYS_PER_WEEK)))
    while w <= w1:
        var x: float = day_to_x(float(w * DAYS_PER_WEEK))
        if x >= lw and x <= right:
            draw_line(Vector2(x, axis_h), Vector2(x, bottom), COL_GRID, 1.0)
        w += step


func _draw_axis(font: Font, lw: float, right: float) -> void:
    draw_rect(Rect2(0, 0, size.x, axis_h), Color(0.1, 0.12, 0.18, 1.0), true)
    var step: int = _week_step()
    var w: int = int(floor(from_day / float(DAYS_PER_WEEK)))
    w -= w % step
    var w1: int = int(ceil(to_day() / float(DAYS_PER_WEEK)))
    while w <= w1:
        var x: float = day_to_x(float(w * DAYS_PER_WEEK))
        if x >= lw and x <= right - 14.0:
            draw_line(Vector2(x, axis_h - 5.0), Vector2(x, axis_h), UiStyle.MUTED, 1.0)
            draw_string(font, Vector2(x + 2.0, axis_h - 6.0), "w%d" % w, HORIZONTAL_ALIGNMENT_LEFT, -1, 10, UiStyle.MUTED)
        w += step
    draw_line(Vector2(0, axis_h), Vector2(size.x, axis_h), UiStyle.BORDER, 1.0)


func _draw_now(lw: float, bottom: float) -> void:
    var x: float = day_to_x(float(current_day))
    if x >= lw and x <= size.x:
        draw_line(Vector2(x, 0), Vector2(x, bottom), COL_NOW, 1.5)
    if contract_day > 0:
        var cx: float = day_to_x(float(contract_day))
        if cx >= lw and cx <= size.x:
            draw_line(Vector2(cx, axis_h), Vector2(cx, bottom), COL_CONTRACT, 1.0)
