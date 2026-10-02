class_name EventToast
extends PanelContainer
## Event text; choice buttons call GameState.resolve_event(choice_index).

const AUTO_HIDE_SECONDS: float = 8.0

var gs: SimState = null
var _title: Label
var _text: Label
var _buttons: VBoxContainer
var _timer: float = 0.0
var _has_choices: bool = false


func setup(state: SimState) -> void:
    gs = state
    custom_minimum_size = Vector2(420, 0)
    var box := VBoxContainer.new()
    add_child(box)
    _title = UiStyle.title("")
    box.add_child(_title)
    _text = UiStyle.label("", 14)
    _text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
    _text.custom_minimum_size.x = 400
    box.add_child(_text)
    _buttons = VBoxContainer.new()
    box.add_child(_buttons)
    visible = false
    gs.event_fired.connect(show_event)
    gs.level_started.connect(func() -> void: visible = false)


func show_event(ev: EventDef, choices: Array) -> void:
    _title.text = ev.name
    _text.text = ev.text
    UiStyle.clear_children(_buttons)
    _has_choices = not choices.is_empty()
    for i in choices.size():
        var c: Dictionary = choices[i]
        var b := UiStyle.button(str(c["label"]))
        b.pressed.connect(func() -> void: _choose(i))
        _buttons.add_child(b)
    if not _has_choices:
        var ok_b := UiStyle.button("OK")
        ok_b.pressed.connect(func() -> void: visible = false)
        _buttons.add_child(ok_b)
    _timer = AUTO_HIDE_SECONDS
    visible = true


func _choose(i: int) -> void:
    gs.resolve_event(i)
    visible = false


func _process(delta: float) -> void:
    if visible and not _has_choices:
        _timer -= delta
        if _timer <= 0.0:
            visible = false
