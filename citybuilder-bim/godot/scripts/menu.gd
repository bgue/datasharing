extends Control
## Scenario picker: lists bundles found under res://scenarios/*/sequence.json.

const MAIN_SCENE: String = "res://scenes/main.tscn"


func _ready() -> void:
    theme = UiStyle.make_theme()
    var bg := ColorRect.new()
    bg.color = Color(0.1, 0.12, 0.17)
    bg.set_anchors_preset(Control.PRESET_FULL_RECT)
    add_child(bg)
    var center := CenterContainer.new()
    center.set_anchors_preset(Control.PRESET_FULL_RECT)
    add_child(center)
    var panel := PanelContainer.new()
    panel.custom_minimum_size = Vector2(620, 0)
    center.add_child(panel)
    var box := VBoxContainer.new()
    box.add_theme_constant_override("separation", 8)
    panel.add_child(box)
    box.add_child(UiStyle.label("SiteBuilder", 40, UiStyle.ACCENT))
    box.add_child(UiStyle.label("Plan the site. Sequence the build. Hand over on time.", 15, UiStyle.MUTED))
    var scenarios: Node = get_node("/root/Scenarios")
    var list: Array = scenarios.call("list_bundles")
    if list.is_empty():
        box.add_child(UiStyle.label("No scenarios found in res://scenarios/<id>/sequence.json", 16, UiStyle.BAD))
    for info in list:
        var d: Dictionary = info
        var b := Button.new()
        b.focus_mode = Control.FOCUS_NONE
        b.alignment = HORIZONTAL_ALIGNMENT_LEFT
        b.text = "%s   [%s, %s]\n%s" % [d["name"], d["sector"], d["difficulty"], d["description"]]
        b.custom_minimum_size = Vector2(580, 56)
        b.clip_text = true
        b.pressed.connect(func() -> void: _start(str(d["path"])))
        box.add_child(b)
    var quit_b := UiStyle.button("Quit")
    quit_b.pressed.connect(func() -> void: get_tree().quit())
    box.add_child(quit_b)


func _start(path: String) -> void:
    var scenarios: Node = get_node("/root/Scenarios")
    if bool(scenarios.call("select", path)):
        get_tree().change_scene_to_file(MAIN_SCENE)
