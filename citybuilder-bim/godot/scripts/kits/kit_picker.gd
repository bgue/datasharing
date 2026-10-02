class_name KitPicker
extends Node
## Hover picking of kit instances. A camera ray from the mouse is tested against the world box of every
## KitLayer instance (cell rectangle on its storey plane up to its height); the nearest hit wins. A small tooltip
## follows the mouse (name + zone, variant, overall fill, per-layer fills); a left click selects the instance
## and emits `installation_selected(index)`. Independent of BimView: it only needs a KitLayer.

signal installation_hovered(index: int)
signal installation_selected(index: int)

const TOOLTIP_OFFSET: Vector2 = Vector2(18, 22)

var kit_layer_provider: Callable = Callable()  ## () -> KitLayer, so level restarts are followed
var camera: Camera3D = null  ## the viewport camera when null
var enabled: bool = true
var hovered: int = -1
var selected: int = -1

var _tip_layer: CanvasLayer = null
var _tip_panel: PanelContainer = null
var _tip_label: Label = null
var _boxes_key: int = -1
var _boxes: Array = []


func _ready() -> void:
    _build_tooltip()


func _build_tooltip() -> void:
    _tip_layer = CanvasLayer.new()
    _tip_layer.layer = 30
    add_child(_tip_layer)
    _tip_panel = PanelContainer.new()
    _tip_panel.mouse_filter = Control.MOUSE_FILTER_IGNORE
    _tip_panel.theme = UiStyle.make_theme()
    _tip_panel.visible = false
    _tip_label = Label.new()
    _tip_label.mouse_filter = Control.MOUSE_FILTER_IGNORE
    _tip_panel.add_child(_tip_label)
    _tip_layer.add_child(_tip_panel)


# ------------------------------------------------------------------ pure helpers (unit tested)

## Distance along the ray to the box (slab test); -1 when missed. A ray starting inside returns 0.
static func ray_aabb(origin: Vector3, dir: Vector3, box: AABB) -> float:
    var tmin: float = 0.0
    var tmax: float = INF
    var lo: Vector3 = box.position
    var hi: Vector3 = box.end
    for a in 3:
        var d: float = dir[a]
        if absf(d) < 1e-9:
            if origin[a] < lo[a] or origin[a] > hi[a]:
                return -1.0
        else:
            var t1: float = (lo[a] - origin[a]) / d
            var t2: float = (hi[a] - origin[a]) / d
            tmin = maxf(tmin, minf(t1, t2))
            tmax = minf(tmax, maxf(t1, t2))
            if tmin > tmax:
                return -1.0
    return tmin


## Index of the nearest box hit by the ray, -1 when none. `boxes` holds AABBs (or null for "not pickable").
static func pick(boxes: Array, origin: Vector3, dir: Vector3) -> int:
    var best: int = -1
    var best_t: float = INF
    for i in boxes.size():
        if not (boxes[i] is AABB):
            continue
        var t: float = ray_aabb(origin, dir, boxes[i])
        if t >= 0.0 and t < best_t:
            best_t = t
            best = i
    return best


## Tooltip text of an instance record (KitInstances.instances() entry):
##   "Storage tank TK-1 · L00-Z14" / "Variant rack_ei" / "Overall 40%" / "steel 100% · piping 40% · EI 0%"
static func tooltip_text(inst: Dictionary, registry: KitRegistry) -> String:
    var lines: PackedStringArray = PackedStringArray()
    var head: String = str(inst.get("name", inst.get("kit", "")))
    var zone: String = str(inst.get("zone_id", ""))
    lines.append(head if zone == "" else "%s · %s" % [head, zone])
    var kit: String = str(inst.get("kit", ""))
    var title: String = registry.kit_title(kit) if registry != null else kit
    var variant: String = str(inst.get("variant", ""))
    lines.append("%s%s" % [title, "" if variant == "" else " (%s)" % variant])
    lines.append("Overall %d%%" % int(roundf(float(inst.get("overall_fill", 0.0)) * 100.0)))
    var fills: Dictionary = inst.get("layer_fills", {})
    var present: Array = inst.get("present_layers", [])
    var parts: PackedStringArray = PackedStringArray()
    var ids: Array = present if registry == null else registry.layer_ids(kit)
    for id in ids:
        if present.has(id) and fills.has(id):
            parts.append("%s %d%%" % [KitRegistry.layer_label(str(id)), int(roundf(float(fills[id]) * 100.0))])
    if not parts.is_empty():
        lines.append(" · ".join(parts))
    return "\n".join(lines)


# ------------------------------------------------------------------ live picking

func _layer() -> KitLayer:
    return kit_layer_provider.call() if kit_layer_provider.is_valid() else null


func _camera() -> Camera3D:
    if camera != null and is_instance_valid(camera):
        return camera
    return get_viewport().get_camera_3d() if is_inside_tree() else null


## Boxes of the current KitLayer (cached per layer / instance count).
func boxes() -> Array:
    var layer: KitLayer = _layer()
    if layer == null or layer.kit_instances == null:
        return []
    var key: int = layer.get_instance_id() * 1000 + layer.instance_count()
    if key != _boxes_key:
        _boxes_key = key
        _boxes = []
        for i in layer.instance_count():
            _boxes.append(layer.instance_aabb(i))
    return _boxes


## Instance under a screen position (-1 when none).
func pick_at(screen_pos: Vector2) -> int:
    var cam: Camera3D = _camera()
    if cam == null:
        return -1
    return pick(boxes(), cam.project_ray_origin(screen_pos), cam.project_ray_normal(screen_pos))


func _over_ui() -> bool:
    var vp: Viewport = get_viewport()
    if vp == null or not vp.has_method("gui_get_hovered_control"):
        return false
    var c: Control = vp.call("gui_get_hovered_control")
    return c != null and c.is_visible_in_tree() and c.mouse_filter != Control.MOUSE_FILTER_IGNORE


func _process(_delta: float) -> void:
    if not enabled or not is_inside_tree():
        _hide_tip()
        return
    var vp: Viewport = get_viewport()
    var mp: Vector2 = vp.get_mouse_position()
    var idx: int = -1
    if vp.get_visible_rect().has_point(mp) and not _over_ui():
        idx = pick_at(mp)
    if idx != hovered:
        hovered = idx
        installation_hovered.emit(idx)
    var layer: KitLayer = _layer()
    if idx < 0 or layer == null or idx >= layer.instance_count():
        _hide_tip()
        return
    _tip_label.text = tooltip_text(layer.kit_instances.instances()[idx], layer.registry)
    _tip_panel.visible = true
    var size: Vector2 = _tip_panel.get_combined_minimum_size()
    var pos: Vector2 = mp + TOOLTIP_OFFSET
    var vs: Vector2 = vp.get_visible_rect().size
    if pos.x + size.x > vs.x:
        pos.x = mp.x - size.x - 12.0
    if pos.y + size.y > vs.y:
        pos.y = mp.y - size.y - 12.0
    _tip_panel.position = pos


func _hide_tip() -> void:
    if _tip_panel != null:
        _tip_panel.visible = false


func _unhandled_input(event: InputEvent) -> void:
    if not enabled:
        return
    if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
        var idx: int = pick_at((event as InputEventMouseButton).position)
        if idx >= 0:
            select(idx)


func select(index: int) -> void:
    selected = index
    var layer: KitLayer = _layer()
    if layer != null:
        layer.set_selected(index)
    installation_selected.emit(index)
