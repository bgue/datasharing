class_name Report
extends Control
## Weekly summary panel (non-modal) and the end-of-level score breakdown with grade and export.

signal export_requested()
signal menu_requested()
signal restart_requested()

const MAX_LOG_LINES: int = 9

var gs: SimState = null
var _week_panel: PanelContainer
var _week_text: RichTextLabel
var _final_panel: PanelContainer
var _final_box: VBoxContainer
var _week_timer: float = 0.0


func setup(state: SimState) -> void:
    gs = state
    mouse_filter = Control.MOUSE_FILTER_IGNORE
    set_anchors_preset(Control.PRESET_FULL_RECT)

    _week_panel = PanelContainer.new()
    _week_panel.custom_minimum_size = Vector2(330, 0)
    UiStyle.place(_week_panel, Rect2(1, 1, 0, 0), Vector4(-338, -250, -8, -64))
    var wb := VBoxContainer.new()
    _week_panel.add_child(wb)
    var head := HBoxContainer.new()
    var wt := UiStyle.title("Weekly report")
    wt.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    head.add_child(wt)
    var close := UiStyle.button("x")
    close.pressed.connect(func() -> void: _week_panel.visible = false)
    head.add_child(close)
    wb.add_child(head)
    _week_text = RichTextLabel.new()
    _week_text.bbcode_enabled = true
    _week_text.custom_minimum_size = Vector2(310, 140)
    wb.add_child(_week_text)
    _week_panel.visible = false
    add_child(_week_panel)

    _final_panel = PanelContainer.new()
    _final_panel.custom_minimum_size = Vector2(460, 0)
    UiStyle.place(_final_panel, Rect2(0.5, 0.5, 0.5, 0.5), Vector4(-240, -230, 240, 230))
    _final_box = VBoxContainer.new()
    _final_panel.add_child(_final_box)
    _final_panel.visible = false
    add_child(_final_panel)

    gs.week_advanced.connect(func(_w: int) -> void: show_week(gs.last_report))
    gs.level_finished.connect(show_final)
    gs.level_started.connect(func() -> void:
        _final_panel.visible = false
        _week_panel.visible = false)


func show_week(r: Dictionary) -> void:
    if r.is_empty() or gs.finished:
        return
    var txt: String = "[b]Week %d done[/b]  cash %s (%s)\nTasks finished: %d | crew utilisation %s (%s overall)\n" % [
        int(r["week"]), Fmt.money(float(r["cash"])), _signed(float(r["cash_change"])),
        int(r["tasks_finished"]), Fmt.pct(float(r.get("utilisation_week", 0.0))), Fmt.pct(float(r.get("utilisation", 0.0)))]
    var lines: Array = r["log"]
    var n: int = 0
    for l in lines:
        if n >= MAX_LOG_LINES:
            txt += "... and %d more\n" % (lines.size() - n)
            break
        n += 1
        var s: String = str(l)
        if s.begins_with("INCIDENT") or s.contains("FAILED"):
            txt += "[color=#ff6a60]%s[/color]\n" % s
        elif s.begins_with("EVENT"):
            txt += "[color=#ffcf5a]%s[/color]\n" % s
        else:
            txt += "%s\n" % s
    _week_text.text = txt
    _week_panel.visible = true
    _week_timer = 12.0


func _signed(v: float) -> String:
    return ("+" if v >= 0.0 else "") + Fmt.money(v)


func show_final(res: Dictionary) -> void:
    _week_panel.visible = false
    UiStyle.clear_children(_final_box)
    var won: bool = bool(res["won"])
    _final_box.add_child(UiStyle.label("Handover achieved" if won else "Level failed", 26, UiStyle.GOOD if won else UiStyle.BAD))
    _final_box.add_child(UiStyle.label(str(res.get("reason", "")), 14, UiStyle.MUTED))
    var grade := UiStyle.label("Grade %s   (%d / 100)" % [str(res["grade"]), int(round(float(res["total"])))], 30, UiStyle.WARN)
    _final_box.add_child(grade)
    var grid := GridContainer.new()
    grid.columns = 3
    grid.add_theme_constant_override("h_separation", 24)
    for h in ["Metric", "Score", "Weight"]:
        grid.add_child(UiStyle.label(h, 14, UiStyle.MUTED))
    var comps: Dictionary = res["components"]
    var weights: Dictionary = res["weights"]
    var names: Dictionary = {"time": "Finish vs contract", "cost": "Cost vs budget", "safety": "Safety",
        "quality": "Quality (first-pass)", "stability": "Plan stability"}
    for k in ["time", "cost", "safety", "quality", "stability"]:
        grid.add_child(UiStyle.label(str(names[k]), 15))
        grid.add_child(UiStyle.label(Fmt.pct(float(comps[k])), 15))
        grid.add_child(UiStyle.label(str(int(float(weights.get(k, 0.0)))), 15))
    _final_box.add_child(grid)
    if float(res["adjust"]) != 0.0:
        _final_box.add_child(UiStyle.label("Event adjustments: %+d" % int(float(res["adjust"])), 14))
    _final_box.add_child(UiStyle.label("Finished week %d (contract week %d) | spent %s of %s | incidents %d" % [
        int(res["weeks"]), int(res["contract_weeks"]), Fmt.money(float(res["spent"])),
        Fmt.money(float(res["budget"])), int(res["incidents"])], 13, UiStyle.MUTED))
    var row := HBoxContainer.new()
    var ex := UiStyle.button("Export plan (JSON + CSV)")
    ex.pressed.connect(func() -> void: export_requested.emit())
    row.add_child(ex)
    var rs := UiStyle.button("Play again")
    rs.pressed.connect(func() -> void: restart_requested.emit())
    row.add_child(rs)
    var mn := UiStyle.button("Menu")
    mn.pressed.connect(func() -> void: menu_requested.emit())
    row.add_child(mn)
    _final_box.add_child(row)
    _final_panel.visible = true


func _process(delta: float) -> void:
    if _week_panel.visible and not gs.finished:
        _week_timer -= delta
        if _week_timer <= 0.0:
            _week_panel.visible = false
