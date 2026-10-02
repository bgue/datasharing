class_name MarkerLegend
extends PanelContainer
## Legend of the virtual-task markers (docs/06 A.3): glyph letter, colour and name of the eight marker kinds (plus
## the generic one). The same glyph and colour are used by the zone inspector and the sequence editor rows, and the
## colours match what BimView draws (the placeholder colours, or the marker kit colours when kits are active).

const IDS: Array[String] = ["survey", "dewatering", "scaffold", "lift_plan", "permit", "test", "shoring", "crane", "generic"]
const GLYPHS: Dictionary = {
    "survey": "S", "dewatering": "D", "scaffold": "C", "lift_plan": "L", "permit": "P", "test": "T", "shoring": "R",
    "crane": "K", "generic": "V",
}
const LABELS: Dictionary = {
    "survey": "Survey", "dewatering": "Dewatering", "scaffold": "Scaffold cage", "lift_plan": "Lift plan",
    "permit": "Permit", "test": "Test / commissioning", "shoring": "Shoring", "crane": "Crane", "generic": "Other virtual task",
}

## True when the 3D view draws marker kit meshes (their colours differ from the placeholder cylinders).
var kit_colours: bool = false


## One-letter glyph of a marker kind ("V" for unknown kinds).
static func glyph(marker: String) -> String:
    return str(GLYPHS.get(marker, GLYPHS["generic"]))


## Colour of a marker kind in the 3D view: the placeholder colour, or the kit colour with `kit`.
static func colour(marker: String, kit: bool = false) -> Color:
    if kit and MarkerKit.DEFAULT_COLOURS.has(marker):
        return MarkerKit.DEFAULT_COLOURS[marker]
    return BimView.marker_colour(marker)


static func label_of(marker: String) -> String:
    return str(LABELS.get(marker, LABELS["generic"]))


func setup(use_kit_colours: bool = false) -> void:
    kit_colours = use_kit_colours
    refresh()


func refresh() -> void:
    UiStyle.clear_children(self)
    var box := VBoxContainer.new()
    box.add_theme_constant_override("separation", 2)
    add_child(box)
    box.add_child(UiStyle.label("Virtual task markers", 13, UiStyle.MUTED))
    for id in IDS:
        var row := HBoxContainer.new()
        row.add_theme_constant_override("separation", 6)
        row.add_child(UiStyle.swatch(colour(id, kit_colours), Vector2(12, 14)))
        var g := UiStyle.label(glyph(id), 13, colour(id, kit_colours))
        g.custom_minimum_size.x = 14
        row.add_child(g)
        row.add_child(UiStyle.label(label_of(id), 12))
        box.add_child(row)
