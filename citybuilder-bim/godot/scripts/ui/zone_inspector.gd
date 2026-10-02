class_name ZoneInspector
extends PanelContainer
## Zone name, tags, crews present / max, tasks grouped by state with step, element, quantity
## and blocked reason.

const GROUP_ORDER: Array[String] = ["ACTIVE", "REWORK", "AWAITING_INSPECTION", "READY", "BLOCKED", "DONE", "INSPECTED"]
const MAX_LINES_PER_GROUP: int = 7

signal message(text: String)
## "What's needed?" pressed: the owner opens the dialog. Without a listener the rows are printed inline (see _show_whats_needed).
signal whats_needed_requested(zone_id: String)
## "Sequence" pressed: the owner opens the sequence editor for the zone.
signal sequence_editor_requested(zone_id: String)

const MAX_PACKAGE_ROWS: int = 8

var gs: SimState = null
var zone_id: String = ""
var _controls: VBoxContainer
var _packages: VBoxContainer
var _title: Label
var _info: Label
var _text: RichTextLabel
var _lane: GanttRenderer
var _dirty: bool = true
## True when the 3D view draws marker kit meshes (set by main): the marker colours then follow the kit.
var gs_kit_colours: bool = false
## Explain rows of "What's needed?" shown above the task list until the zone changes or the button is pressed again.
var _explain_text: String = ""


func setup(state: SimState) -> void:
    gs = state
    custom_minimum_size = Vector2(310, 0)
    var box := VBoxContainer.new()
    add_child(box)
    _title = UiStyle.title("Zone")
    box.add_child(_title)
    _info = UiStyle.label("Hover a zone on the map", 13, UiStyle.MUTED)
    _info.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
    box.add_child(_info)
    _controls = VBoxContainer.new()
    box.add_child(_controls)
    _text = RichTextLabel.new()
    _text.bbcode_enabled = true
    _text.fit_content = false
    _text.scroll_active = true
    _text.custom_minimum_size = Vector2(290, 110)
    _text.size_flags_vertical = Control.SIZE_EXPAND_FILL
    box.add_child(_text)
    # lane view: the timeline renderer squeezed into one zone row above the package list
    _lane = GanttRenderer.new()
    _lane.gs = gs
    _lane.show_labels = false
    _lane.show_scrollbars = false
    _lane.fit_height = true
    _lane.allow_expand = false
    _lane.axis_h = 14.0
    _lane.custom_minimum_size = Vector2(290, 64)
    _lane.model_invalidated.connect(func() -> void: _dirty = true)
    box.add_child(_lane)
    var pkg_scroll := ScrollContainer.new()
    pkg_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
    pkg_scroll.custom_minimum_size = Vector2(290, 120)
    box.add_child(pkg_scroll)
    _packages = VBoxContainer.new()
    _packages.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    pkg_scroll.add_child(_packages)
    gs.package_state_changed.connect(func(_i: String, _s: String) -> void: _dirty = true)
    gs.task_state_changed.connect(func(_a: String, _b: int, _c: int) -> void: _dirty = true)
    gs.crews_changed.connect(func() -> void: _dirty = true)
    gs.week_advanced.connect(func(_w: int) -> void: _dirty = true)
    gs.tiles_changed.connect(func() -> void: _dirty = true)
    refresh()


func show_zone(id: String) -> void:
    if id == zone_id:
        return
    zone_id = id
    _explain_text = ""
    _dirty = true


func _hex(c: Color) -> String:
    return "#" + c.to_html(false)


func refresh() -> void:
    _dirty = false
    if gs == null or gs.bundle == null:
        return
    UiStyle.clear_children(_controls)
    UiStyle.clear_children(_packages)
    if zone_id == "" or not gs.bundle.zones_by_id.has(zone_id):
        _title.text = "Zone inspector"
        _info.text = "Hover a zone on the map (Assign mode: click to pin)."
        _text.text = ""
        _lane.visible = false
        return
    _lane.visible = true
    _lane.set_model(GanttModel.build(gs, {"zone_ids": [zone_id], "group": false}))
    var z: ZoneData = gs.bundle.zones_by_id[zone_id]
    var storey: StoreyData = gs.bundle.storeys_by_id.get(z.storey_id, null)
    _title.text = z.name
    var crews_here: int = Productivity.zone_crew_count(gs, zone_id)
    var cong: float = Productivity.congestion_factor(crews_here, z.max_crews)
    var paused: String = ""
    if int(gs.zone_paused_until.get(zone_id, 0)) > gs.week:
        paused = "  PAUSED until week %d" % int(gs.zone_paused_until[zone_id])
    _info.text = "%s | tags: %s\nCrews %d/%d (congestion x%.2f) | access: %s%s" % [
        storey.name if storey != null else z.storey_id,
        ", ".join(z.tags) if not z.tags.is_empty() else "none",
        crews_here, z.max_crews, cong, "yes" if gs.zone_access(zone_id) else "NO", paused]
    var groups: Dictionary = {}
    for s in GROUP_ORDER:
        groups[s] = [] as Array[TaskData]
    for t in gs.bundle.tasks_by_zone.get(zone_id, []):
        var task: TaskData = t
        var sname: String = TaskRuntime.state_name((gs.runtime[task.task_id] as TaskRuntime).state)
        if groups.has(sname):
            (groups[sname] as Array[TaskData]).append(task)
    var out: String = ""
    for s in GROUP_ORDER:
        var list: Array[TaskData] = groups[s]
        if list.is_empty():
            continue
        out += "[color=%s][b]%s (%d)[/b][/color]\n" % [_hex(UiStyle.STATE_COLORS[s]), s.replace("_", " ").capitalize(), list.size()]
        if s == "DONE" or s == "INSPECTED":
            continue
        var n: int = 0
        for task in list:
            if n >= MAX_LINES_PER_GROUP:
                out += "  ... %d more\n" % (list.size() - n)
                break
            n += 1
            var st: StepDef = gs.bundle.step_of(task)
            var rt: TaskRuntime = gs.runtime[task.task_id]
            var vtag: String = ""
            if task.is_virtual:  # marker glyph and colour, as in the sequence editor and the 3D view
                var mk: String = gs.marker_of(task)
                vtag = "  [color=%s][b]%s[/b][/color] virtual" % [_hex(MarkerLegend.colour(mk, gs_kit_colours)), MarkerLegend.glyph(mk)]
            out += "  %s - %s (%s %s)%s\n" % [st.name if st != null else task.step_id, task.element_name, _num(task.quantity), task.unit, vtag]
            if s == "ACTIVE" or s == "REWORK":
                out += "    [color=#9aa5b8]%d%% done[/color]\n" % int(100.0 * rt.progress / maxf(rt.required, 0.0001))
            elif rt.blocked_reason != "":
                out += "    [color=#ffcf5a]%s[/color]\n" % rt.blocked_reason
    if _explain_text != "":
        out = _explain_text + "\n" + out
    _text.text = out
    _build_controls(z)
    _build_packages(z)


## Shift toggle, staffing and takt card controls for the zone.
func _build_controls(z: ZoneData) -> void:
    var zr: ZoneRuntime = gs.zone_runtime[z.id]
    var row := HBoxContainer.new()
    var shift := UiStyle.button("Shift: %s" % zr.shift_mode, "Toggle double shift (x%.1f output, x%.1f crew cost)" % [
        gs.scenario.shift_productivity_factor, gs.scenario.shift_cost_factor])
    shift.pressed.connect(func() -> void:
        if not gs.set_shift(z.id, "single" if zr.shift_mode == "double" else "double"):
            message.emit(gs.last_error)
        _dirty = true)
    row.add_child(shift)
    var staff := UiStyle.button("Staff", "Move idle crews here so released packages have their ideal crew")
    staff.pressed.connect(func() -> void:
        var r: Dictionary = Planner.staff_zone(gs, z.id, "ideal", false)
        if int(r["unmet"]) > 0:
            message.emit("%d crews short: hire more or free some" % int(r["unmet"]))
        _dirty = true)
    row.add_child(staff)
    _controls.add_child(row)
    # manual sequencing hooks (docs/06 A.3): the sequence editor panel builds on the same SimState / Manual calls
    var manual_row := HBoxContainer.new()
    var manual := CheckButton.new()
    manual.text = "Manual mode"
    manual.focus_mode = Control.FOCUS_NONE
    manual.tooltip_text = "Freeze the generated packages of this zone and run only manual / recipe tasks"
    manual.button_pressed = gs.manual_zones.has(z.id)
    manual.toggled.connect(func(on: bool) -> void:
        if not Manual.set_mode(gs, z.id, on):
            message.emit(gs.last_error)
        _dirty = true)
    manual_row.add_child(manual)
    var needed := UiStyle.button("What's needed?", "List the construction logic recipes that apply to this zone and which steps are missing")
    needed.pressed.connect(func() -> void: _show_whats_needed(z.id))
    manual_row.add_child(needed)
    _controls.add_child(manual_row)
    var seq := UiStyle.button("Sequence editor (N)", "Author the manual chain of this zone: steps, links, bound elements")
    seq.pressed.connect(func() -> void: sequence_editor_requested.emit(z.id))
    _controls.add_child(seq)
    var card_row := HBoxContainer.new()
    var opt := OptionButton.new()
    opt.focus_mode = Control.FOCUS_NONE
    opt.add_item("(no card)")
    var selected: int = 0
    var ids: Array[String] = [""]
    for c in gs.bundle.card_list():
        opt.add_item(c.name)
        ids.append(c.id)
        if c.id == zr.card_id:
            selected = ids.size() - 1
    opt.select(selected)
    opt.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    card_row.add_child(opt)
    var apply := UiStyle.button("Apply")
    apply.pressed.connect(func() -> void:
        var id: String = ids[opt.selected]
        if id == "":
            Cards.clear(gs, z.id)
        elif not Cards.apply(gs, z.id, id):
            message.emit(gs.last_error)
        _dirty = true)
    card_row.add_child(apply)
    var clear := UiStyle.button("Clear")
    clear.disabled = zr.card_id == ""
    clear.pressed.connect(func() -> void:
        Cards.clear(gs, z.id)
        _dirty = true)
    card_row.add_child(clear)
    _controls.add_child(card_row)
    if zr.card_id != "":
        var txt: String = "Station: %s" % Cards.station_name(gs, zr, zr.station_index)
        if zr.behind_takt:
            txt += "  (behind takt)"
        _controls.add_child(UiStyle.label(txt, 12, UiStyle.WARN if zr.behind_takt else UiStyle.MUTED))


## Package rows: name, state, crews now / ideal / max and a hold / release button.
func _build_packages(z: ZoneData) -> void:
    var list: Array[PackageData] = []
    for p in gs.bundle.packages_by_zone.get(z.id, []):
        var rt: PackageRuntime = gs.package_runtime[(p as PackageData).package_id]
        if rt.state != "done":
            list.append(p)
    list.sort_custom(func(a: PackageData, b: PackageData) -> bool:
        return (gs.package_runtime[a.package_id] as PackageRuntime).priority < (gs.package_runtime[b.package_id] as PackageRuntime).priority)
    if list.is_empty():
        _packages.add_child(UiStyle.label("No open packages", 12, UiStyle.MUTED))
        return
    var shown: int = 0
    for p in list:
        if shown >= MAX_PACKAGE_ROWS:
            _packages.add_child(UiStyle.label("... %d more packages" % (list.size() - shown), 12, UiStyle.MUTED))
            break
        shown += 1
        var rt2: PackageRuntime = gs.package_runtime[p.package_id]
        var row := HBoxContainer.new()
        var col: Color = UiStyle.WARN if rt2.state in ["understaffed", "held"] else (UiStyle.GOOD if rt2.state in ["active", "over_ideal"] else UiStyle.MUTED)
        var l := UiStyle.label("%s %s  %s %d/%d/%d" % [p.trade, p.work_face.replace("_", " "), rt2.state, rt2.crews_now, p.crew_ideal, p.crew_max], 12, col)
        l.size_flags_horizontal = Control.SIZE_EXPAND_FILL
        l.clip_text = true
        l.tooltip_text = "%s\n%s" % [p.name, rt2.blocked_reason]
        l.mouse_filter = Control.MOUSE_FILTER_PASS
        row.add_child(l)
        var b := UiStyle.button("Release" if not rt2.released else "Hold")
        var pid: String = p.package_id
        b.pressed.connect(func() -> void:
            if gs.package_runtime[pid].released:
                gs.hold_package(pid)
            else:
                gs.release_package(pid)
            _dirty = true)
        row.add_child(b)
        _packages.add_child(row)


## Opens the What's needed? dialog when the owner listens to `whats_needed_requested`, else prints the
## logic.explain rows of the zone into the text area (toggles).
func _show_whats_needed(zid: String) -> void:
    if not whats_needed_requested.get_connections().is_empty():
        whats_needed_requested.emit(zid)
        return
    if _explain_text != "":
        _explain_text = ""
        _dirty = true
        return
    var lines: Array[String] = LogicLib.explain_lines(LogicLib.explain_zone(gs, zid))
    if lines.is_empty():
        lines.append("No recipe applies here.")
    var out: String = "[b]What's needed?[/b]\n"
    for l in lines:
        out += "%s\n" % l.replace("[", "[lb]")  # the rows contain [x] / [ ] marks, not BBCode tags
    _explain_text = out
    _dirty = true


func _num(v: float) -> String:
    if absf(v - round(v)) < 0.005:
        return str(int(round(v)))
    return "%.1f" % v


func _process(_delta: float) -> void:
    if _dirty:
        refresh()
