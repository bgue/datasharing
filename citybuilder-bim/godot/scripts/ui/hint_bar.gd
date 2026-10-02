class_name HintBar
extends PanelContainer
## Bottom bar: current tool, key help, tutorial hint for the week and transient messages.

const MESSAGE_SECONDS: float = 4.0

var gs: SimState = null
var _tool: Label
var _hint: Label
var _msg: Label
var _msg_timer: float = 0.0
var tool_text: String = ""


func setup(state: SimState) -> void:
    gs = state
    var box := VBoxContainer.new()
    add_child(box)
    _msg = UiStyle.label("", 15, UiStyle.BAD)
    box.add_child(_msg)
    _hint = UiStyle.label("", 14, UiStyle.WARN)
    _hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
    _hint.custom_minimum_size.x = 560
    box.add_child(_hint)
    _tool = UiStyle.label("", 14)
    box.add_child(_tool)
    gs.week_advanced.connect(func(_w: int) -> void: _update_hint())
    gs.level_started.connect(_update_hint)
    _update_hint()


func show_message(text: String) -> void:
    _msg.text = text
    _msg_timer = MESSAGE_SECONDS


func set_tool_text(t: String) -> void:
    tool_text = t
    _tool.text = t


func _update_hint() -> void:
    if gs == null or gs.bundle == null:
        return
    var best: String = ""
    var best_week: int = -1
    for h in gs.scenario.hints:
        var w: int = int(h["week"])
        if w <= gs.week and w >= best_week:
            best_week = w
            best = str(h["text"])
    _hint.text = best
    _hint.visible = best != ""


func _process(delta: float) -> void:
    if _msg_timer > 0.0:
        _msg_timer -= delta
        if _msg_timer <= 0.0:
            _msg.text = ""
