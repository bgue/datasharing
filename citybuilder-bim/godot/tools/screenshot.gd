extends SceneTree
## Visual QA harness: loads a scenario in the real game scene, optionally plays the autopilot for N weeks, opens
## panels, frames the camera and saves the viewport as a PNG. Needs a rendering display (xvfb-run with
## --rendering-driver opengl3); `godot/tools/shots.sh` renders the standard set.
##
##   xvfb-run -a -s "-screen 0 1280x720x24" godot --path godot --rendering-driver opengl3 \
##       --script res://tools/screenshot.gd -- --scenario=minimal --weeks=0 --view=overview --out=/tmp/a.png
##
## Arguments after `--`:
##   --scenario=<id>        bundle under res://scenarios/<id>/sequence.json (default minimal)
##   --weeks=<n>            Planner.auto_layout + Planner.autopilot for n weeks, events resolved with choice 0
##   --view=<v>             overview | zone:<id> | storey:<index> | installation:<n>
##   --panels=<list>        comma list of gantt, editor, whats_needed, procurement, report, final, crews, heat, legend,
##                          charts, inspector, toast, installations (opens / shows them; with --view=installation:N the
##                          instance N is selected in the Installations list and outlined in the 3D view)
##   --hide=<list>          comma list of crews, procurement, charts, inspector, gantt, editor to close
##   --zone=<id>            zone used by the editor / whats_needed / inspector (default: first zone with manual tasks, else first)
##   --size=WxH             1280x720 (default) or 1920x1080 (any WxH accepted)
##   --pad=<cells>          margin around the framed cells of --view=zone / installation (default 1.5)
##   --scale=<f>            downscale factor applied to the saved image (default 1.0)
##   --out=<path.png>       output file (required)

const MAIN_SCENE: String = "res://scenes/main.tscn"
const PANELS: Array[String] = ["gantt", "editor", "whats_needed", "procurement", "report", "final", "crews", "heat",
        "legend", "charts", "inspector", "toast", "installations"]
const HIDEABLE: Array[String] = ["crews", "procurement", "charts", "inspector", "gantt", "editor"]
const SETTLE_FRAMES: int = 12


func _initialize() -> void:
    var opts: Dictionary = parse_args(OS.get_cmdline_user_args())
    if not bool(opts["ok"]):
        printerr("screenshot: " + str(opts["error"]))
        printerr("usage: --scenario=<id> --weeks=<n> --view=<overview|zone:<id>|storey:<i>|installation:<n>> " +
                "--panels=<%s> --size=1280x720 --out=<path.png>" % ",".join(PANELS))
        quit(2)
        return
    var res: Dictionary = await render(self, opts)
    if not bool(res["ok"]):
        printerr("screenshot: " + str(res["error"]))
        quit(1)
        return
    print("screenshot: wrote %s (%dx%d, %d bytes)" % [opts["out"], int(res["width"]), int(res["height"]), int(res["bytes"])])
    quit(0)


## Parses the arguments (the part after `--`). Returns {ok, error, scenario, weeks, view, panels, hide, size, out, zone, scale}.
static func parse_args(args: PackedStringArray) -> Dictionary:
    var o: Dictionary = {"ok": true, "error": "", "scenario": "minimal", "weeks": 0, "view": "overview",
            "view_kind": "overview", "view_arg": "", "panels": [], "hide": [], "size": Vector2i(1280, 720), "out": "",
            "zone": "", "scale": 1.0, "pad": 1.5}
    for a in args:
        if not a.begins_with("--"):
            return _bad(o, "unexpected argument '%s'" % a)
        var eq: int = a.find("=")
        var key: String = a.substr(2, eq - 2) if eq >= 0 else a.substr(2)
        var val: String = a.substr(eq + 1) if eq >= 0 else ""
        match key:
            "scenario":
                if val == "" or not val.is_valid_filename():
                    return _bad(o, "--scenario needs a bundle id, got '%s'" % val)
                o["scenario"] = val
            "weeks":
                if not val.is_valid_int() or int(val) < 0 or int(val) > 400:
                    return _bad(o, "--weeks needs an integer 0..400, got '%s'" % val)
                o["weeks"] = int(val)
            "view":
                var kind: String = val.get_slice(":", 0)
                var arg: String = val.substr(kind.length() + 1) if val.contains(":") else ""
                if kind == "overview" and arg == "":
                    pass
                elif kind == "zone" and arg != "":
                    pass
                elif (kind == "storey" or kind == "installation") and arg.is_valid_int():
                    pass
                else:
                    return _bad(o, "--view must be overview, zone:<id>, storey:<index> or installation:<n>, got '%s'" % val)
                o["view"] = val
                o["view_kind"] = kind
                o["view_arg"] = arg
            "panels":
                var list: Array = []
                for p in val.split(",", false):
                    if not PANELS.has(p):
                        return _bad(o, "unknown panel '%s' (known: %s)" % [p, ", ".join(PANELS)])
                    list.append(p)
                o["panels"] = list
            "hide":
                var hl: Array = []
                for p in val.split(",", false):
                    if not HIDEABLE.has(p):
                        return _bad(o, "cannot hide '%s' (known: %s)" % [p, ", ".join(HIDEABLE)])
                    hl.append(p)
                o["hide"] = hl
            "size":
                var parts: PackedStringArray = val.split("x")
                if parts.size() != 2 or not parts[0].is_valid_int() or not parts[1].is_valid_int() \
                        or int(parts[0]) < 320 or int(parts[1]) < 200 or int(parts[0]) > 4096 or int(parts[1]) > 4096:
                    return _bad(o, "--size needs WxH between 320x200 and 4096x4096, got '%s'" % val)
                o["size"] = Vector2i(int(parts[0]), int(parts[1]))
            "zone":
                o["zone"] = val
            "pad":
                if not val.is_valid_float() or float(val) < 0.0 or float(val) > 40.0:
                    return _bad(o, "--pad needs a number of cells in [0, 40], got '%s'" % val)
                o["pad"] = float(val)
            "scale":
                if not val.is_valid_float() or float(val) <= 0.05 or float(val) > 1.0:
                    return _bad(o, "--scale needs a number in (0.05, 1], got '%s'" % val)
                o["scale"] = float(val)
            "out":
                if not val.ends_with(".png"):
                    return _bad(o, "--out needs a .png path, got '%s'" % val)
                o["out"] = val
            _:
                return _bad(o, "unknown argument --%s" % key)
    if str(o["out"]) == "":
        return _bad(o, "--out=<path.png> is required")
    return o


static func _bad(o: Dictionary, msg: String) -> Dictionary:
    o["ok"] = false
    o["error"] = msg
    return o


## Renders one screenshot for parsed options. `tree` is the running SceneTree (this script, or a test runner).
## Returns {ok, error, width, height, bytes}. The scene is removed again afterwards.
static func render(tree: SceneTree, opts: Dictionary) -> Dictionary:
    var fail := func(msg: String) -> Dictionary: return {"ok": false, "error": msg, "width": 0, "height": 0, "bytes": 0}
    var root: Window = tree.root
    var scenarios: Node = root.get_node_or_null("Scenarios")
    if scenarios == null:
        return fail.call("autoload Scenarios missing (run with --path godot)")
    var path: String = str(scenarios.call("path_for", str(opts["scenario"])))
    if path == "" or not FileAccess.file_exists(path):
        return fail.call("scenario '%s' not found (res://scenarios/%s/sequence.json[.gz])" % [str(opts["scenario"]), str(opts["scenario"])])
    if not bool(scenarios.call("select", path)):
        return fail.call("scenario '%s' failed to load" % str(opts["scenario"]))
    var size: Vector2i = opts["size"]
    if DisplayServer.get_name() != "headless":
        DisplayServer.window_set_size(size)
    root.size = size
    root.content_scale_size = Vector2i.ZERO
    for i in 3:
        await tree.process_frame
    var main: Node = (load(MAIN_SCENE) as PackedScene).instantiate()
    root.add_child(main)
    await tree.process_frame
    var gs: SimState = main.get("gs")
    if gs == null or not gs.running:
        _cleanup(tree, main)
        return fail.call("game did not start for '%s'" % str(opts["scenario"]))
    gs.speed = 0

    var weeks: int = int(opts["weeks"])
    if weeks > 0:
        _play(gs, weeks, not (opts["panels"] as Array).has("toast"))
        gs.speed = 0
        # the weekly report and the toast are transient in play: only shown when asked for
        (main.get("report") as Report).hide_week()
        if not (opts["panels"] as Array).has("toast"):
            (main.get("toast") as Control).visible = false
    # let the views notice the new state before panels are toggled
    for i in 3:
        await tree.process_frame

    var zone_id: String = _pick_zone(gs, str(opts["zone"]))
    _apply_panels(main, gs, opts, zone_id)
    Input.warp_mouse(Vector2(2, 2))  # no zone hover from a pointer parked mid-screen
    for i in SETTLE_FRAMES:
        await tree.process_frame
    var framed: String = _frame(main, gs, str(opts["view_kind"]), str(opts["view_arg"]), zone_id, float(opts.get("pad", 1.5)))
    if framed != "":
        _cleanup(tree, main)
        return fail.call(framed)
    for i in SETTLE_FRAMES:
        await tree.process_frame
    if str(opts["view_kind"]) != "overview" or bool(opts.get("snap", true)):
        (main.get("view") as Node3D).call("snap")
    await RenderingServer.frame_post_draw
    await tree.process_frame
    await RenderingServer.frame_post_draw

    var img: Image = root.get_texture().get_image()
    if img == null or img.is_empty():
        _cleanup(tree, main)
        return fail.call("viewport returned no image (is a rendering driver active? headless cannot render)")
    var sc: float = float(opts.get("scale", 1.0))
    if sc < 0.999:
        img.resize(maxi(1, int(img.get_width() * sc)), maxi(1, int(img.get_height() * sc)), Image.INTERPOLATE_LANCZOS)
    var out: String = str(opts["out"])
    var dir: String = out.get_base_dir()
    if dir != "" and not DirAccess.dir_exists_absolute(dir):
        DirAccess.make_dir_recursive_absolute(dir)
    var err: int = img.save_png(out)
    _cleanup(tree, main)
    if err != OK:
        return fail.call("cannot save %s (error %d)" % [out, err])
    var f := FileAccess.open(out, FileAccess.READ)
    var bytes: int = f.get_length() if f != null else 0
    return {"ok": true, "error": "", "width": img.get_width(), "height": img.get_height(), "bytes": bytes}


static func _cleanup(tree: SceneTree, main: Node) -> void:
    tree.root.remove_child(main)
    main.free()
    tree.root.get_node("Scenarios").set("current_bundle", null)


## Auto layout, then the autopilot week by week; events are resolved with choice 0.
static func _play(gs: SimState, weeks: int, resolve_last: bool) -> void:
    Planner.auto_layout(gs)
    var guard: int = 0
    while gs.week < weeks and not gs.finished and guard < weeks * 3 + 10:
        guard += 1
        if not gs.pending_event.is_empty():
            gs.resolve_event(0)
        Planner.autopilot(gs, weeks - gs.week)
    if resolve_last and not gs.pending_event.is_empty():
        gs.resolve_event(0)


## First zone with manual tasks, else the first zone with tasks.
static func _pick_zone(gs: SimState, wanted: String) -> String:
    if wanted != "" and gs.bundle.zones_by_id.has(wanted):
        return wanted
    for zid in gs.manual_zones:
        return str(zid)
    var b: SequenceBundle = gs.bundle
    var best: String = ""
    var best_n: int = -1
    for z in b.zones:
        var n: int = 0
        for t in b.tasks:
            if t.zone_id == z.id:
                n += 1
        if n > best_n:
            best_n = n
            best = z.id
    return best


static func _apply_panels(main: Node, gs: SimState, opts: Dictionary, zone_id: String) -> void:
    var panels: Array = opts["panels"]
    var hide: Array = opts["hide"]
    var is_open := func(n: String) -> bool: return panels.has(n)
    for h in hide:
        match str(h):
            "crews": (main.get("crew_panel") as Control).visible = false
            "procurement": (main.get("procurement") as Control).visible = false
            "charts": (main.get("charts") as Control).visible = false
            "inspector": (main.get("inspector") as Control).visible = false
            "gantt": (main.get("gantt") as GanttPanel).set_open(false)
            "editor": (main.get("seq_editor") as SequenceEditor).close_editor()
    # state of the panels at game start unless asked otherwise
    if is_open.call("crews"):
        (main.get("crew_panel") as Control).visible = true
    if is_open.call("procurement"):
        (main.get("procurement") as Control).visible = true
    if is_open.call("charts"):
        (main.get("charts") as Control).visible = true
    var inspector: ZoneInspector = main.get("inspector")
    if is_open.call("inspector") or is_open.call("editor") or is_open.call("whats_needed") or opts["view_kind"] == "zone":
        inspector.visible = true
        inspector.show_zone(zone_id)
        main.set("pinned_zone", zone_id)
    if is_open.call("gantt"):
        (main.get("gantt") as GanttPanel).set_open(true)
    if is_open.call("editor") or is_open.call("legend"):
        var ed: SequenceEditor = main.get("seq_editor")
        main.set("pinned_zone", zone_id)
        ed.open_for_zone(zone_id)
        if is_open.call("legend"):
            var lb: Variant = ed.get("_legend_btn")
            if lb is CheckButton:
                (lb as CheckButton).button_pressed = true
    if is_open.call("whats_needed"):
        (main.get("whats_needed") as WhatsNeededDialog).open_for_zone(zone_id)
    if is_open.call("report"):
        (main.get("report") as Report).show_week(gs.last_report)
    if is_open.call("final"):
        var res: Dictionary = Scoring.compute(gs, false)
        res["reason"] = "Screenshot preview"
        res["weeks"] = gs.week
        res["contract_weeks"] = gs.bundle.contract_weeks()
        res["spent"] = gs.spent_total
        res["budget"] = gs.bundle.contract_budget()
        res["incidents"] = gs.incidents
        (main.get("report") as Report).show_final(res)
    if is_open.call("installations"):
        var ip: Variant = main.get("installations")
        if ip != null and not (ip as Control).visible:
            (ip as Control).call("toggle")
    if is_open.call("heat"):
        _toggle_heat(main)
    if is_open.call("toast"):
        _show_toast(main, gs)
    main.call("_reflow_bottom")


## The per-cell progress heat overlay (key H) lives in BimView.
static func _toggle_heat(main: Node) -> void:
    var bv: Node = main.get("bim_view")
    if bv != null and bv.has_method("set_heat_visible"):
        bv.call("set_heat_visible", true)


## Shows an event toast: the pending event if there is one, else the first scenario event with its choices.
static func _show_toast(main: Node, gs: SimState) -> void:
    var toast: EventToast = main.get("toast")
    if not gs.pending_event.is_empty():
        var ev: EventDef = gs.pending_event["event"]
        toast.show_event(ev, ev.choices)
        return
    if gs.scenario.events.size() > 0:
        var e: EventDef = gs.scenario.events[0]
        toast.show_event(e, e.choices)


## Returns "" on success, else an error message.
static func _frame(main: Node, gs: SimState, kind: String, arg: String, zone_id: String, pad: float = 1.5) -> String:
    var view: Node3D = main.get("view")
    var b: SequenceBundle = gs.bundle
    match kind:
        "overview":
            view.call("frame_site", b.site_rect, false)
        "zone":
            if not b.zones_by_id.has(arg):
                return "unknown zone '%s' (zones: %s)" % [arg, ", ".join(b.zones_by_id.keys().slice(0, 12))]
            var z: ZoneData = b.zones_by_id[arg]
            main.call("_on_gantt_zone", arg)
            view.call("frame_cells", z.cells, 3.0, 1.5, true)
        "storey":
            var idx: int = int(arg)
            var lo: int = b.storeys[0].index
            var hi: int = b.storeys[b.storeys.size() - 1].index
            if idx < lo or idx > hi:
                return "storey index %d out of range %d..%d" % [idx, lo, hi]
            main.call("_set_focus", idx)
            view.call("frame_site", b.site_rect, false)
            view.call("set_focus_height", b.storey_y_for_index(idx))
        "installation":
            var layer: Variant = (main.get("bim_view") as Node).get("_kit_layer")
            if layer == null or (layer as Object).get("kit_instances") == null:
                return "no visual kit layer in this build"
            var inst: Array = (layer.kit_instances as KitInstances).instances()
            var n: int = int(arg)
            if n < 0 or n >= inst.size():
                return "installation %d out of range 0..%d" % [n, inst.size() - 1]
            var d: Dictionary = inst[n]
            main.call("_set_focus", int(d["storey_index"]))
            view.call("frame_cells", d["cells"], float(d["height_m"]) / b.cell_size_m, pad, true)
            var ip: Variant = main.get("installations")
            if ip != null and (ip as Control).visible:
                (ip as Control).call("select", n, false, true)
    return ""
