extends TC
## Zone inspector hooks for manual sequencing: "Manual mode" check button and "What's needed?" button.


const REC: Dictionary = {
    "schema_version": "1.0", "id": "rec_ui_footing", "name": "Footing and column", "sector": "healthcare",
    "applies_to": {"ifc_class": ["IfcFooting"]},
    "steps": [{"ref": "GEN-SURVEY-SETOUT", "virtual": true, "duration_days": 2}, {"ref": "STR-FOOT-POUR"}],
}


func _find(node: Node, pred: Callable) -> Node:
    if pred.call(node):
        return node
    for c in node.get_children():
        var r: Node = _find(c, pred)
        if r != null:
            return r
    return null


func test_inspector_manual_mode_and_whats_needed() -> void:
    var gs: SimState = fixture_state([REC.duplicate(true)])
    var insp := ZoneInspector.new()
    insp.setup(gs)
    insp.show_zone("L00-Z1")
    insp.refresh()
    var check: CheckButton = _find(insp, func(n: Node) -> bool: return n is CheckButton) as CheckButton
    ok(check != null, "Manual mode check button")
    ok(not check.button_pressed, "off at start")
    var needed: Button = _find(insp, func(n: Node) -> bool: return n is Button and (n as Button).text == "What's needed?") as Button
    ok(needed != null, "What's needed? button")
    check.button_pressed = true  # emits toggled
    ok(gs.manual_zones.has("L00-Z1"), "toggling the check button switches the manual mode")
    insp.refresh()
    check = _find(insp, func(n: Node) -> bool: return n is CheckButton) as CheckButton
    ok(check.button_pressed, "check button reflects the state after a refresh")
    needed = _find(insp, func(n: Node) -> bool: return n is Button and (n as Button).text == "What's needed?") as Button
    needed.pressed.emit()
    insp.refresh()
    var text: String = insp._text.text
    ok(text.contains("What's needed?"), "heading printed: " + text.substr(0, 80))
    ok(text.contains("Footing and column"), "recipe name listed")
    ok(text.contains("Setting-out survey"), "virtual step listed")
    ok(text.contains("(virtual)"), "virtual mark")
    ok(text.contains("[lb]x]") or text.contains("[lb] ]"), "status marks are escaped for BBCode")
    needed = _find(insp, func(n: Node) -> bool: return n is Button and (n as Button).text == "What's needed?") as Button
    needed.pressed.emit()
    insp.refresh()
    ok(not insp._text.text.contains("What's needed?"), "pressing again hides the rows")
    insp.free()
    gs.free()
