extends Node3D
## Root of the game scene. Keeps the Kenney View / Camera / GridMap / Sun / CanvasLayer nodes from
## main.tscn and builds the rest in code: ground, SiteBuilder, BimView, ZoneOverlay and the UI.

enum Mode { BUILD, ASSIGN }

const MENU_SCENE: String = "res://scenes/menu.tscn"
const SECONDS_PER_WEEK: float = 3.0

@onready var view: Node3D = $View
@onready var camera: Camera3D = $View/Camera
@onready var grid: GridMap = $GridMap
@onready var canvas: CanvasLayer = $CanvasLayer

var gs: SimState = null
var builder: SiteBuilder = null
var bim_view: BimView = null
var overlay: ZoneOverlay = null
var ui_root: Control = null
var top_bar: TopBar = null
var crew_panel: CrewPanel = null
var inspector: ZoneInspector = null
var procurement: ProcurementPanel = null
var charts: ChartsPanel = null
var toast: EventToast = null
var report: Report = null
var hint_bar: HintBar = null

var mode: int = Mode.BUILD
var pinned_zone: String = ""
var _accum: float = 0.0
var _last_speed: int = 1
var _ground: Node3D = null


func _ready() -> void:
    gs = get_node("/root/GameState")
    var scenarios: Node = get_node("/root/Scenarios")
    if scenarios.current_bundle == null:
        for a in OS.get_cmdline_user_args():
            if a.begins_with("--scenario="):
                scenarios.call("select", "res://scenarios/%s/sequence.json" % a.substr(11))
    if scenarios.current_bundle == null:
        get_tree().change_scene_to_file.call_deferred(MENU_SCENE)
        return
    if not gs.start(scenarios.current_bundle):
        push_error("cannot start scenario: %s" % gs.last_error)
        get_tree().change_scene_to_file.call_deferred(MENU_SCENE)
        return
    _build_world()
    _build_ui()
    _wire()
    _set_mode(Mode.BUILD)
    _set_focus(0)


func _exit_tree() -> void:
    if gs != null and gs.speed != 0:
        gs.speed = 0


# ------------------------------------------------------------------ construction

func _build_world() -> void:
    var b: SequenceBundle = gs.bundle
    view.call("frame_site", b.site_rect)
    _build_ground(b)
    builder = SiteBuilder.new()
    builder.name = "SiteBuilder"
    add_child(builder)
    builder.setup(gs, grid, camera)
    bim_view = BimView.new()
    bim_view.name = "BimView"
    add_child(bim_view)
    bim_view.setup(gs)
    overlay = ZoneOverlay.new()
    overlay.name = "ZoneOverlay"
    add_child(overlay)
    overlay.setup(gs, camera)


func _build_ground(b: SequenceBundle) -> void:
    _ground = Node3D.new()
    _ground.name = "Ground"
    add_child(_ground)
    var plane := MeshInstance3D.new()
    var pm := PlaneMesh.new()
    pm.size = Vector2(b.site_rect.size.x + 1.0, b.site_rect.size.y + 1.0)
    var mat := StandardMaterial3D.new()
    mat.albedo_color = Color(0.42, 0.62, 0.36)
    pm.material = mat
    plane.mesh = pm
    plane.position = Vector3(b.site_rect.position.x + (b.site_rect.size.x - 1) * 0.5, -0.02,
            b.site_rect.position.y + (b.site_rect.size.y - 1) * 0.5)
    _ground.add_child(plane)
    # blocked cells read as dark pads
    for c in gs.scenario.blocked_cells:
        var mi := MeshInstance3D.new()
        var bm := BoxMesh.new()
        bm.size = Vector3(0.96, 0.08, 0.96)
        var bmat := StandardMaterial3D.new()
        bmat.albedo_color = Color(0.2, 0.2, 0.22)
        bm.material = bmat
        mi.mesh = bm
        mi.position = Vector3(c.x, 0.0, c.y)
        _ground.add_child(mi)


func _build_ui() -> void:
    ui_root = Control.new()
    ui_root.name = "UI"
    ui_root.set_anchors_preset(Control.PRESET_FULL_RECT)
    ui_root.mouse_filter = Control.MOUSE_FILTER_IGNORE
    ui_root.theme = UiStyle.make_theme()
    canvas.add_child(ui_root)

    top_bar = _scene("res://scenes/ui/top_bar.tscn") as TopBar
    UiStyle.place(top_bar, Rect2(0, 0, 1, 0), Vector4(8, 6, -8, 0))
    ui_root.add_child(top_bar)
    top_bar.setup(gs)

    crew_panel = _scene("res://scenes/ui/crew_panel.tscn") as CrewPanel
    UiStyle.place(crew_panel, Rect2(0, 0, 0, 0), Vector4(8, 96, 8, 96))
    ui_root.add_child(crew_panel)
    crew_panel.setup(gs)

    inspector = _scene("res://scenes/ui/zone_inspector.tscn") as ZoneInspector
    UiStyle.place(inspector, Rect2(1, 0, 1, 0), Vector4(-318, 96, -8, 96))
    inspector.grow_horizontal = Control.GROW_DIRECTION_BEGIN
    ui_root.add_child(inspector)
    inspector.setup(gs)

    procurement = _scene("res://scenes/ui/procurement_panel.tscn") as ProcurementPanel
    UiStyle.place(procurement, Rect2(1, 0, 1, 0), Vector4(-318, 440, -8, 440))
    procurement.grow_horizontal = Control.GROW_DIRECTION_BEGIN
    ui_root.add_child(procurement)
    procurement.setup(gs)

    charts = _scene("res://scenes/ui/charts_panel.tscn") as ChartsPanel
    UiStyle.place(charts, Rect2(0, 1, 0, 1), Vector4(8, -8, 8, -8))
    charts.grow_vertical = Control.GROW_DIRECTION_BEGIN
    ui_root.add_child(charts)
    charts.setup(gs)

    hint_bar = _scene("res://scenes/ui/hint_bar.tscn") as HintBar
    UiStyle.place(hint_bar, Rect2(0.5, 1, 0.5, 1), Vector4(-290, -8, 290, -8))
    hint_bar.grow_horizontal = Control.GROW_DIRECTION_BOTH
    hint_bar.grow_vertical = Control.GROW_DIRECTION_BEGIN
    ui_root.add_child(hint_bar)
    hint_bar.setup(gs)

    toast = _scene("res://scenes/ui/event_toast.tscn") as EventToast
    UiStyle.place(toast, Rect2(0.5, 0, 0.5, 0), Vector4(-210, 110, 210, 110))
    toast.grow_horizontal = Control.GROW_DIRECTION_BOTH
    ui_root.add_child(toast)
    toast.setup(gs)

    report = _scene("res://scenes/ui/report.tscn") as Report
    ui_root.add_child(report)
    report.setup(gs)


func _scene(path: String) -> Control:
    return (load(path) as PackedScene).instantiate() as Control


func _wire() -> void:
    top_bar.speed_requested.connect(_set_speed)
    top_bar.next_week_requested.connect(func() -> void: _advance())
    top_bar.panel_toggled.connect(_toggle_panel)
    top_bar.export_requested.connect(_export)
    top_bar.menu_requested.connect(_to_menu)
    crew_panel.crew_selected.connect(_on_crew_selected)
    crew_panel.equipment_arm_requested.connect(func(id: String) -> void:
        _set_mode(Mode.BUILD)
        builder.arm_equipment(id)
        _update_tool_text())
    crew_panel.message.connect(hint_bar.show_message)
    procurement.message.connect(hint_bar.show_message)
    builder.message.connect(hint_bar.show_message)
    builder.palette_changed.connect(func(_t: String) -> void: _update_tool_text())
    builder.equipment_armed_changed.connect(func(_id: String) -> void: _update_tool_text())
    overlay.zone_hovered.connect(_on_zone_hovered)
    overlay.zone_clicked.connect(_on_zone_clicked)
    report.export_requested.connect(_export)
    report.menu_requested.connect(_to_menu)
    report.restart_requested.connect(func() -> void:
        gs.start(gs.bundle)
        crew_panel.selected_crew_id = -1
        pinned_zone = ""
        _set_mode(Mode.BUILD)
        _set_focus(0))
    gs.event_fired.connect(func(_e: EventDef, choices: Array) -> void:
        if not choices.is_empty():
            _set_speed(0))
    gs.incident_occurred.connect(func(_z: String) -> void: hint_bar.show_message("Incident! The zone is stopped for a week."))


# ------------------------------------------------------------------ mode / focus / speed

func _set_mode(m: int) -> void:
    mode = m
    builder.active = (m == Mode.BUILD)
    overlay.pick_enabled = (m == Mode.ASSIGN)
    top_bar.set_mode_text("Mode: Build (Tab)" if m == Mode.BUILD else "Mode: Assign crews (Tab)")
    _update_tool_text()


func _update_tool_text() -> void:
    if mode == Mode.BUILD:
        if builder.pending_equipment != "":
            var def: EquipmentDef = gs.equipment_def(builder.pending_equipment)
            hint_bar.set_tool_text("[Build] Place %s on a crane pad (Esc cancels)" % (def.name if def != null else ""))
        else:
            var t: String = builder.current_tile()
            hint_bar.set_tool_text("[Build] %s: %s, rent %s/wk | LMB place, RMB rotate, DEL demolish, Q/E tile | Tab: assign" % [
                str(SiteTiles.DISPLAY_NAMES.get(t, t)), Fmt.money(gs.scenario.tile_place_cost(t)),
                Fmt.money(gs.scenario.tile_weekly_cost(t))])
    else:
        hint_bar.set_tool_text("[Assign] Select a crew, click a zone | PgUp/PgDn storey | G ghost | Tab: build")


func _set_focus(idx: int) -> void:
    var lo: int = gs.bundle.storeys[0].index
    var hi: int = gs.bundle.storeys[gs.bundle.storeys.size() - 1].index
    idx = clampi(idx, lo, hi)
    gs.focus_storey_index = idx
    bim_view.set_focus_storey(idx)
    overlay.set_focus_storey(idx)
    view.call("set_focus_height", gs.bundle.storey_y_for_index(idx))
    gs.focus_changed.emit(idx)


func _set_speed(s: int) -> void:
    gs.speed = s
    if s > 0:
        _last_speed = s
    _accum = 0.0
    top_bar.refresh()


func _advance() -> void:
    if not gs.pending_event.is_empty():
        hint_bar.show_message("Resolve the event first.")
        return
    gs.advance_week()


func _toggle_panel(n: String) -> void:
    var p: Control = null
    match n:
        "Crews":
            p = crew_panel
        "Zone":
            p = inspector
        "Procure":
            p = procurement
        "Charts":
            p = charts
    if p != null:
        p.visible = not p.visible


func _on_crew_selected(crew_id: int) -> void:
    if crew_id >= 0:
        _set_mode(Mode.ASSIGN)
    inspector.show_zone(overlay.hovered_zone_id if overlay.hovered_zone_id != "" else pinned_zone)


func _on_zone_hovered(zone_id: String) -> void:
    inspector.show_zone(zone_id if zone_id != "" else pinned_zone)


func _on_zone_clicked(zone_id: String) -> void:
    pinned_zone = zone_id
    inspector.show_zone(zone_id)
    if crew_panel.selected_crew_id >= 0:
        if gs.assign_crew(crew_panel.selected_crew_id, zone_id):
            var z: ZoneData = gs.bundle.zones_by_id[zone_id]
            hint_bar.show_message("Crew assigned to %s" % z.name)


# ------------------------------------------------------------------ actions

func _export() -> void:
    var path: String = PlanExport.export_to_user(gs)
    if path == "":
        hint_bar.show_message("Plan export failed")
    else:
        hint_bar.show_message("Plan exported: %s" % ProjectSettings.globalize_path(path))


func _to_menu() -> void:
    get_node("/root/Scenarios").set("current_bundle", null)
    get_tree().change_scene_to_file(MENU_SCENE)


func _process(delta: float) -> void:
    if gs == null or gs.bundle == null:
        return
    if gs.speed > 0 and not gs.finished and gs.pending_event.is_empty():
        _accum += delta * float(gs.speed)
        if _accum >= SECONDS_PER_WEEK:
            _accum = 0.0
            gs.advance_week()


func _unhandled_input(event: InputEvent) -> void:
    if gs == null or gs.bundle == null:
        return
    if event.is_action_pressed("speed_pause"):
        _set_speed(_last_speed if gs.speed == 0 else 0)
    elif event.is_action_pressed("speed_1"):
        _set_speed(1)
    elif event.is_action_pressed("speed_2"):
        _set_speed(2)
    elif event.is_action_pressed("speed_4"):
        _set_speed(4)
    elif event.is_action_pressed("storey_up"):
        _set_focus(gs.focus_storey_index + 1)
    elif event.is_action_pressed("storey_down"):
        _set_focus(gs.focus_storey_index - 1)
    elif event.is_action_pressed("toggle_ghost"):
        bim_view.set_ghost_visible(not bim_view.show_ghost)
        hint_bar.show_message("Ghost elements: %s" % ("shown" if bim_view.show_ghost else "hidden"))
    elif event.is_action_pressed("export_plan"):
        _export()
    elif event.is_action_pressed("toggle_mode"):
        _set_mode(Mode.ASSIGN if mode == Mode.BUILD else Mode.BUILD)
    elif event.is_action_pressed("cancel"):
        crew_panel.selected_crew_id = -1
        crew_panel.select_crew(-1)
        pinned_zone = ""
        _set_mode(Mode.BUILD)
    elif event.is_action_pressed("save"):
        hint_bar.show_message("Game saved" if SaveGame.save(gs) else "Save failed")
    elif event.is_action_pressed("load"):
        if SaveGame.load_into(gs):
            hint_bar.show_message("Game loaded")
        else:
            hint_bar.show_message("Load failed: %s" % gs.last_error)
