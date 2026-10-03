class_name UiStyle
extends RefCounted
## Code-built theme: dark semi-transparent panels, Kenney's Lilita One font. No theme file needed.

const BG: Color = Color(0.07, 0.08, 0.11, 0.84)
const BORDER: Color = Color(0.32, 0.38, 0.5, 0.9)
const TEXT: Color = Color(0.93, 0.94, 0.96)
const MUTED: Color = Color(0.62, 0.66, 0.74)
const GOOD: Color = Color(0.4, 0.9, 0.5)
const WARN: Color = Color(1.0, 0.8, 0.25)
const BAD: Color = Color(1.0, 0.4, 0.38)
const ACCENT: Color = Color(0.35, 0.6, 1.0)

const STATE_COLORS: Dictionary = {
    "READY": Color(0.4, 0.9, 0.5),
    "ACTIVE": Color(0.4, 0.65, 1.0),
    "REWORK": Color(1.0, 0.45, 0.4),
    "AWAITING_INSPECTION": Color(0.95, 0.75, 0.3),
    "BLOCKED": Color(0.98, 0.85, 0.2),
    "NOT_STARTED": Color(0.6, 0.6, 0.65),
    "DONE": Color(0.7, 0.72, 0.78),
    "INSPECTED": Color(0.3, 0.85, 0.55),
}

static var _font: Font = null


static func font() -> Font:
    if _font == null:
        _font = load("res://fonts/lilita_one_regular.ttf")
    return _font


static func box(bg: Color, border: Color = BORDER, radius: int = 6, margin: int = 8) -> StyleBoxFlat:
    var sb := StyleBoxFlat.new()
    sb.bg_color = bg
    sb.border_color = border
    sb.set_border_width_all(1)
    sb.set_corner_radius_all(radius)
    sb.content_margin_left = margin
    sb.content_margin_right = margin
    sb.content_margin_top = margin - 2
    sb.content_margin_bottom = margin - 2
    return sb


static func make_theme() -> Theme:
    var th := Theme.new()
    th.default_font = font()
    th.default_font_size = 15
    th.set_stylebox("panel", "PanelContainer", box(BG))
    th.set_stylebox("panel", "Panel", box(BG))
    th.set_stylebox("normal", "Button", box(Color(0.16, 0.2, 0.3, 0.95), BORDER, 4, 6))
    th.set_stylebox("hover", "Button", box(Color(0.22, 0.3, 0.45, 0.95), ACCENT, 4, 6))
    th.set_stylebox("pressed", "Button", box(Color(0.3, 0.45, 0.7, 0.98), ACCENT, 4, 6))
    th.set_stylebox("disabled", "Button", box(Color(0.12, 0.13, 0.17, 0.8), Color(0.2, 0.22, 0.28), 4, 6))
    th.set_stylebox("focus", "Button", StyleBoxEmpty.new())
    # lists and trees sit on an opaque dark ground so the 3D view never shows through the rows
    var tree_bg: StyleBoxFlat = box(Color(0.05, 0.06, 0.09, 0.96), BORDER, 3, 4)
    th.set_stylebox("panel", "Tree", tree_bg)
    th.set_stylebox("panel", "ItemList", tree_bg)
    th.set_stylebox("normal", "LineEdit", box(Color(0.05, 0.06, 0.09, 0.96), BORDER, 3, 6))
    # embedded dialog windows (What's needed?, link popup): dark frame instead of the engine default grey
    var win: StyleBoxFlat = box(Color(0.09, 0.1, 0.14, 0.98), BORDER, 6, 10)
    win.expand_margin_top = 34.0
    th.set_stylebox("panel", "AcceptDialog", box(Color(0.09, 0.1, 0.14, 0.98), BORDER, 4, 8))
    th.set_stylebox("embedded_border", "Window", win)
    th.set_stylebox("embedded_unfocused_border", "Window", win)
    th.set_color("title_color", "Window", Color(0.75, 0.85, 1.0))
    th.set_constant("title_height", "Window", 32)
    th.set_color("font_color", "LineEdit", TEXT)
    th.set_color("font_placeholder_color", "LineEdit", MUTED)
    th.set_color("font_color", "Button", TEXT)
    th.set_color("font_hover_color", "Button", Color.WHITE)
    th.set_color("font_disabled_color", "Button", Color(0.45, 0.48, 0.55))
    th.set_color("font_color", "Label", TEXT)
    th.set_color("default_color", "RichTextLabel", TEXT)
    th.set_font_size("font_size", "Button", 14)
    th.set_font_size("normal_font_size", "RichTextLabel", 14)
    th.set_font_size("bold_font_size", "RichTextLabel", 14)
    th.set_constant("separation", "VBoxContainer", 4)
    th.set_constant("separation", "HBoxContainer", 6)
    return th


static func label(text: String, size: int = 15, color: Color = TEXT) -> Label:
    var l := Label.new()
    l.text = text
    l.add_theme_font_size_override("font_size", size)
    l.add_theme_color_override("font_color", color)
    return l


static func button(text: String, tooltip: String = "") -> Button:
    var b := Button.new()
    b.text = text
    b.tooltip_text = tooltip
    b.focus_mode = Control.FOCUS_NONE
    return b


static func title(text: String) -> Label:
    return label(text, 19, Color(0.75, 0.85, 1.0))


static func swatch(color: Color, size: Vector2 = Vector2(10, 18)) -> ColorRect:
    var r := ColorRect.new()
    r.color = color
    r.custom_minimum_size = size
    return r


static func clear_children(node: Node) -> void:
    for c in node.get_children():
        node.remove_child(c)
        c.queue_free()


## Anchors a control: `anchors` holds (left, top, right, bottom) anchor fractions as Rect2(l, t, r, b), NOT a
## position and size (a Rect2 `end` would add them up and push the right / bottom anchors off screen, which made panels
## larger than the screen). `offsets` are the pixel offsets (left, top, right, bottom) as in Control offsets.
static func place(c: Control, anchors: Rect2, offsets: Vector4) -> void:
    c.anchor_left = anchors.position.x
    c.anchor_top = anchors.position.y
    c.anchor_right = anchors.size.x
    c.anchor_bottom = anchors.size.y
    c.offset_left = offsets.x
    c.offset_top = offsets.y
    c.offset_right = offsets.z
    c.offset_bottom = offsets.w
