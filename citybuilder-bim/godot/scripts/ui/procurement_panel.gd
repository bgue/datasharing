class_name ProcurementPanel
extends PanelContainer
## Long-lead tasks: lead time, order button, delivery week, late warning.

signal message(text: String)

const MAX_ROWS: int = 12

var gs: SimState = null
var _body: VBoxContainer
var _dirty: bool = true


func setup(state: SimState) -> void:
    gs = state
    custom_minimum_size = Vector2(300, 0)
    var scroll := ScrollContainer.new()
    scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
    scroll.custom_minimum_size = Vector2(280, 72)
    scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
    add_child(scroll)
    _body = VBoxContainer.new()
    _body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    scroll.add_child(_body)
    gs.week_advanced.connect(func(_w: int) -> void: _dirty = true)
    gs.level_started.connect(func() -> void: _dirty = true)
    gs.task_state_changed.connect(func(_a: String, _b: int, _c: int) -> void: _dirty = true)
    refresh()


func long_lead_tasks() -> Array[TaskData]:
    var out: Array[TaskData] = []
    for t in gs.bundle.tasks:
        if t.lead_time_weeks > 0:
            out.append(t)
    out.sort_custom(func(a: TaskData, b: TaskData) -> bool: return a.planned_start_day < b.planned_start_day)
    return out


func refresh() -> void:
    _dirty = false
    UiStyle.clear_children(_body)
    if gs == null or gs.bundle == null:
        return
    _body.add_child(UiStyle.title("Procurement"))
    var list: Array[TaskData] = long_lead_tasks()
    if list.is_empty():
        _body.add_child(UiStyle.label("No long-lead items in this scenario.", 13, UiStyle.MUTED))
        return
    # Scenarios can carry hundreds of long-lead tasks: list the unfinished ones by planned start.
    var shown: int = 0
    for t in list:
        var rt: TaskRuntime = gs.runtime[t.task_id]
        var done: bool = TaskRuntime.is_finished(rt.state)
        if done:
            continue
        if shown >= MAX_ROWS:
            _body.add_child(UiStyle.label("... %d more long-lead items" % (list.size() - shown), 12, UiStyle.MUTED))
            break
        shown += 1
        var st: StepDef = gs.bundle.step_of(t)
        var row := VBoxContainer.new()
        var head := HBoxContainer.new()
        var name_l := UiStyle.label("%s - %s" % [st.name if st != null else t.step_id, t.element_name], 13)
        name_l.size_flags_horizontal = Control.SIZE_EXPAND_FILL
        name_l.clip_text = true
        head.add_child(name_l)
        if not rt.ordered and not done:
            var ob := UiStyle.button("Order", "Delivery in %d weeks" % t.lead_time_weeks)
            ob.pressed.connect(func() -> void:
                if not gs.order(t.task_id):
                    message.emit(gs.last_error))
            head.add_child(ob)
        row.add_child(head)
        var info: String = "lead %d wk, planned start wk %.1f" % [t.lead_time_weeks, float(t.planned_start_day) / 5.0]
        if rt.ordered:
            info += " | delivery wk %d" % rt.delivery_week
        row.add_child(UiStyle.label(info, 12, UiStyle.MUTED))
        if not done and gs.order_is_late(t.task_id):
            var late := "LATE: delivery wk %d is after planned start" % (rt.delivery_week if rt.ordered else gs.week + t.lead_time_weeks)
            row.add_child(UiStyle.label(late, 12, UiStyle.BAD))
        _body.add_child(row)


func _process(_delta: float) -> void:
    if _dirty:
        refresh()
