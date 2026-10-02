class_name ZoneInspector
extends PanelContainer
## Zone name, tags, crews present / max, tasks grouped by state with step, element, quantity
## and blocked reason.

const GROUP_ORDER: Array[String] = ["ACTIVE", "REWORK", "AWAITING_INSPECTION", "READY", "BLOCKED", "DONE", "INSPECTED"]
const MAX_LINES_PER_GROUP: int = 7

var gs: SimState = null
var zone_id: String = ""
var _title: Label
var _info: Label
var _text: RichTextLabel
var _dirty: bool = true


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
    _text = RichTextLabel.new()
    _text.bbcode_enabled = true
    _text.fit_content = false
    _text.scroll_active = true
    _text.custom_minimum_size = Vector2(290, 230)
    _text.size_flags_vertical = Control.SIZE_EXPAND_FILL
    box.add_child(_text)
    gs.task_state_changed.connect(func(_a: String, _b: int, _c: int) -> void: _dirty = true)
    gs.crews_changed.connect(func() -> void: _dirty = true)
    gs.week_advanced.connect(func(_w: int) -> void: _dirty = true)
    gs.tiles_changed.connect(func() -> void: _dirty = true)
    refresh()


func show_zone(id: String) -> void:
    if id == zone_id:
        return
    zone_id = id
    _dirty = true


func _hex(c: Color) -> String:
    return "#" + c.to_html(false)


func refresh() -> void:
    _dirty = false
    if gs == null or gs.bundle == null:
        return
    if zone_id == "" or not gs.bundle.zones_by_id.has(zone_id):
        _title.text = "Zone inspector"
        _info.text = "Hover a zone on the map (Assign mode: click to pin)."
        _text.text = ""
        return
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
            out += "  %s - %s (%s %s)\n" % [st.name if st != null else task.step_id, task.element_name, _num(task.quantity), task.unit]
            if s == "ACTIVE" or s == "REWORK":
                out += "    [color=#9aa5b8]%d%% done[/color]\n" % int(100.0 * rt.progress / maxf(rt.required, 0.0001))
            elif rt.blocked_reason != "":
                out += "    [color=#ffcf5a]%s[/color]\n" % rt.blocked_reason
    _text.text = out


func _num(v: float) -> String:
    if absf(v - round(v)) < 0.005:
        return str(int(round(v)))
    return "%.1f" % v


func _process(_delta: float) -> void:
    if _dirty:
        refresh()
