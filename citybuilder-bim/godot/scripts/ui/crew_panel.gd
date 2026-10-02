class_name CrewPanel
extends PanelContainer
## Per trade: crew count, hire / fire; list of crews with their zone (click to select for
## assignment, x to unassign); equipment placement.

signal crew_selected(crew_id: int)
signal equipment_arm_requested(equipment_id: String)
signal message(text: String)

var gs: SimState = null
var selected_crew_id: int = -1
var _body: VBoxContainer
var _dirty: bool = true


func setup(state: SimState) -> void:
    gs = state
    custom_minimum_size = Vector2(270, 0)
    var scroll := ScrollContainer.new()
    scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
    scroll.custom_minimum_size = Vector2(260, 330)
    add_child(scroll)
    _body = VBoxContainer.new()
    _body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    scroll.add_child(_body)
    gs.crews_changed.connect(func() -> void: _dirty = true)
    gs.week_advanced.connect(func(_w: int) -> void: _dirty = true)
    gs.tiles_changed.connect(func() -> void: _dirty = true)
    gs.cash_changed.connect(func(_c: float) -> void: _dirty = true)
    gs.level_started.connect(func() -> void: _dirty = true)
    refresh()


func select_crew(id: int) -> void:
    selected_crew_id = id if id != selected_crew_id else -1
    crew_selected.emit(selected_crew_id)
    _dirty = true


func _zone_name(zone_id: String) -> String:
    if zone_id == "":
        return "unassigned"
    var z: ZoneData = gs.bundle.zones_by_id.get(zone_id, null)
    return z.name if z != null else zone_id


func refresh() -> void:
    _dirty = false
    UiStyle.clear_children(_body)
    if gs == null or gs.bundle == null:
        return
    _body.add_child(UiStyle.title("Crews"))
    for t in gs.bundle.trades:
        var row := HBoxContainer.new()
        row.add_child(UiStyle.swatch(t.color))
        var name_l := UiStyle.label("%s  %d/%d" % [t.name, gs.crew_count(t.id), gs.crews_cap(t.id)], 14)
        name_l.size_flags_horizontal = Control.SIZE_EXPAND_FILL
        name_l.tooltip_text = "%s per week, up to %d hires per week (%d left)" % [Fmt.money(t.weekly_cost), t.max_hire_per_week, gs.hires_left_this_week(t.id)]
        name_l.mouse_filter = Control.MOUSE_FILTER_PASS
        row.add_child(name_l)
        var hire := UiStyle.button("+", "Hire a %s (%s / week)" % [t.name, Fmt.money(t.weekly_cost)])
        hire.disabled = gs.crew_count(t.id) >= gs.crews_cap(t.id) or gs.hires_left_this_week(t.id) <= 0
        hire.pressed.connect(func() -> void:
            if gs.hire(t.id) < 0:
                message.emit(gs.last_error))
        row.add_child(hire)
        var fire := UiStyle.button("-", "Fire one %s" % t.name)
        fire.disabled = gs.crew_count(t.id) == 0
        fire.pressed.connect(func() -> void: _fire_one(t.id))
        row.add_child(fire)
        _body.add_child(row)
        _body.add_child(UiStyle.label("   %s / week" % Fmt.money(t.weekly_cost), 12, UiStyle.MUTED))
    if not gs.crews.is_empty():
        _body.add_child(UiStyle.label("Select a crew, then click a zone (Assign mode)", 12, UiStyle.MUTED))
    for c in gs.crews:
        var crew_id: int = int(c["id"])
        var td: TradeDef = gs.bundle.trades_by_id.get(str(c["trade"]), null)
        var row2 := HBoxContainer.new()
        var b := UiStyle.button("%s #%d: %s" % [td.name if td != null else str(c["trade"]), crew_id, _zone_name(str(c["zone_id"]))])
        b.size_flags_horizontal = Control.SIZE_EXPAND_FILL
        b.alignment = HORIZONTAL_ALIGNMENT_LEFT
        b.clip_text = true
        if crew_id == selected_crew_id:
            b.add_theme_stylebox_override("normal", UiStyle.box(Color(0.3, 0.45, 0.7, 0.98), UiStyle.WARN, 4, 8))
        b.pressed.connect(func() -> void: select_crew(crew_id))
        row2.add_child(b)
        var un := UiStyle.button("x", "Unassign this crew")
        un.disabled = str(c["zone_id"]) == ""
        un.pressed.connect(func() -> void: gs.assign_crew(crew_id, ""))
        row2.add_child(un)
        _body.add_child(row2)
    if not gs.scenario.equipment.is_empty():
        _body.add_child(UiStyle.title("Equipment"))
        for e in gs.scenario.equipment:
            var eb := UiStyle.button("Place %s" % e.name, "Mobilise %s, weekly %s, reach %d cells. Needs a crane pad." % [
                Fmt.money(e.mobilisation_cost), Fmt.money(e.weekly_cost), e.reach_cells])
            eb.disabled = gs.equipment_count(e.id) >= e.max_count
            eb.pressed.connect(func() -> void: equipment_arm_requested.emit(e.id))
            _body.add_child(eb)
        for i in gs.equipment_placed.size():
            var pe: Dictionary = gs.equipment_placed[i]
            var def: EquipmentDef = gs.equipment_def(str(pe["id"]))
            var rr := HBoxContainer.new()
            var l := UiStyle.label("%s at %s" % [def.name if def != null else str(pe["id"]), str(pe["cell"])], 13)
            l.size_flags_horizontal = Control.SIZE_EXPAND_FILL
            rr.add_child(l)
            var rb := UiStyle.button("x", "Demobilise")
            rb.pressed.connect(func() -> void: gs.remove_equipment(i))
            rr.add_child(rb)
            _body.add_child(rr)


func _fire_one(trade: String) -> void:
    var pick: int = -1
    for c in gs.crews:
        if str(c["trade"]) == trade:
            pick = int(c["id"])
            if str(c["zone_id"]) == "":
                break
    if pick >= 0:
        if pick == selected_crew_id:
            selected_crew_id = -1
            crew_selected.emit(-1)
        gs.fire(pick)


func _process(_delta: float) -> void:
    if _dirty:
        refresh()
