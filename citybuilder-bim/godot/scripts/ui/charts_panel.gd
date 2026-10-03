class_name ChartsPanel
extends PanelContainer
## S-curve (planned cumulative cost from the baseline vs actual cumulative spend) and a crew histogram.

var gs: SimState = null
var _scurve: Control
var _hist: Control
var _hist_title: Label
var compact: bool = false


func setup(state: SimState) -> void:
    gs = state
    var box := VBoxContainer.new()
    add_child(box)
    box.add_child(UiStyle.label("Spend: planned (blue), actual (orange)", 13, UiStyle.MUTED))
    _scurve = Control.new()
    _scurve.custom_minimum_size = Vector2(250, 110)
    _scurve.draw.connect(_draw_scurve)
    box.add_child(_scurve)
    _hist_title = UiStyle.label("Crews hired per week", 13, UiStyle.MUTED)
    box.add_child(_hist_title)
    _hist = Control.new()
    _hist.custom_minimum_size = Vector2(250, 46)
    _hist.draw.connect(_draw_hist)
    box.add_child(_hist)
    gs.week_advanced.connect(func(_w: int) -> void: _redraw())
    gs.level_started.connect(func() -> void: _redraw())


## Compact mode (little vertical room, e.g. the timeline is open on a small screen): only a short S-curve.
func set_compact(on: bool) -> void:
    if on == compact:
        return
    compact = on
    _scurve.custom_minimum_size.y = 64.0 if on else 110.0
    _hist.visible = not on
    _hist_title.visible = not on
    reset_size()
    _redraw()


func _redraw() -> void:
    _scurve.queue_redraw()
    _hist.queue_redraw()


## Plot series for the S-curve: {"planned": Array[Vector2], "actual": Array[Vector2], "max_x", "max_y"}.
func series() -> Dictionary:
    var planned: Array[Vector2] = []
    var actual: Array[Vector2] = []
    var max_y: float = 1.0
    var pw: Array[float] = gs.bundle.baseline_weekly_planned_cost
    planned.append(Vector2(0, 0))
    for i in pw.size():
        planned.append(Vector2(i + 1, pw[i]))
        max_y = maxf(max_y, pw[i])
    actual.append(Vector2(0, 0))
    for i in gs.cumulative_spend_by_week.size():
        actual.append(Vector2(i + 1, gs.cumulative_spend_by_week[i]))
        max_y = maxf(max_y, gs.cumulative_spend_by_week[i])
    var max_x: float = maxf(float(gs.bundle.contract_weeks()), float(maxi(pw.size(), gs.cumulative_spend_by_week.size())))
    return {"planned": planned, "actual": actual, "max_x": maxf(max_x, 1.0), "max_y": max_y}


func _draw_scurve() -> void:
    if gs == null or gs.bundle == null:
        return
    var s: Dictionary = series()
    var sz: Vector2 = _scurve.size
    var pad: float = 4.0
    _scurve.draw_rect(Rect2(Vector2.ZERO, sz), Color(0, 0, 0, 0.25), true)
    _scurve.draw_line(Vector2(pad, sz.y - pad), Vector2(sz.x - pad, sz.y - pad), UiStyle.MUTED, 1.0)
    _scurve.draw_line(Vector2(pad, pad), Vector2(pad, sz.y - pad), UiStyle.MUTED, 1.0)
    var cw_x: float = pad + (sz.x - 2.0 * pad) * float(gs.bundle.contract_weeks()) / float(s["max_x"])
    _scurve.draw_line(Vector2(cw_x, pad), Vector2(cw_x, sz.y - pad), Color(1, 0.4, 0.4, 0.6), 1.0)
    var cur_x: float = pad + (sz.x - 2.0 * pad) * float(gs.week) / float(s["max_x"])
    _scurve.draw_line(Vector2(cur_x, pad), Vector2(cur_x, sz.y - pad), Color(1, 1, 1, 0.25), 1.0)
    _draw_series(_scurve, s["planned"], UiStyle.ACCENT, s, pad)
    _draw_series(_scurve, s["actual"], Color(1.0, 0.6, 0.15), s, pad)


func _draw_series(c: Control, pts: Array, color: Color, s: Dictionary, pad: float) -> void:
    var sz: Vector2 = c.size
    var poly := PackedVector2Array()
    for p in pts:
        var v: Vector2 = p
        poly.append(Vector2(pad + (sz.x - 2.0 * pad) * v.x / float(s["max_x"]),
                (sz.y - pad) - (sz.y - 2.0 * pad) * v.y / float(s["max_y"])))
    if poly.size() >= 2:
        c.draw_polyline(poly, color, 2.0, true)


func _draw_hist() -> void:
    if gs == null:
        return
    var sz: Vector2 = _hist.size
    _hist.draw_rect(Rect2(Vector2.ZERO, sz), Color(0, 0, 0, 0.25), true)
    var data: Array[int] = gs.crew_count_by_week
    if data.is_empty():
        return
    var max_v: int = 1
    for v in data:
        max_v = maxi(max_v, v)
    var slots: int = maxi(data.size(), gs.bundle.contract_weeks())
    var bw: float = (sz.x - 4.0) / float(slots)
    for i in data.size():
        var h: float = (sz.y - 4.0) * float(data[i]) / float(max_v)
        _hist.draw_rect(Rect2(2.0 + bw * float(i) + 1.0, sz.y - 2.0 - h, maxf(bw - 2.0, 1.0), h), Color(0.4, 0.8, 0.5), true)
