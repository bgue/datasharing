class_name ApiServer
extends Node
## Control API (docs/05 section 6): JSON-RPC 2.0 over WebSocket on 127.0.0.1.
## TCPServer + WebSocketPeer.accept_stream; message framing and responses use Godot's JSONRPC class.
## Method names contain dots ("crew.hire"), which JSONRPC's own scope dispatch cannot route, so
## requests are dispatched here and JSONRPC builds the responses and notifications.
## Requests run on the main thread between frames; a request that advances time runs synchronously.

const DEFAULT_PORT: int = 8765
const ERR_GAME: int = -32000
const ERR_AUTH: int = -32001
const BUFFER_BYTES: int = 16 * 1024 * 1024

var gs: SimState = null
var token: String = ""
var port: int = 0

var _tcp: TCPServer = TCPServer.new()
var _peers: Array[WebSocketPeer] = []
var _rpc: JSONRPC = JSONRPC.new()
var _methods: Dictionary = {}
## The 3D view (BimView) when one exists; it registers itself in BimView.setup. view.highlight / view.set_heat need it.
var bim_view: Node = null
## Camera jump to a kit instance (WP-S): Callable(index: int) -> bool, set by the Installations panel.
var jump_handler: Callable = Callable()
## Camera jump to an area: Callable(area_id: String) -> bool, set by the Areas panel.
var area_handler: Callable = Callable()
var _kit_instances: KitInstances = null


func start(state: SimState, listen_port: int = DEFAULT_PORT, api_token: String = "") -> bool:
    gs = state
    token = api_token
    var err: int = _tcp.listen(listen_port, "127.0.0.1")
    if err != OK:
        push_error("API: cannot listen on 127.0.0.1:%d (error %d)" % [listen_port, err])
        return false
    port = _tcp.get_local_port()
    _register()
    gs.week_advanced.connect(func(w: int) -> void: _notify("week.advanced", {"week": w}))
    gs.event_fired.connect(_on_event_fired)
    gs.level_finished.connect(func(r: Dictionary) -> void: _notify("level.finished", {"result": r}))
    gs.package_state_changed.connect(func(id: String, st: String) -> void:
        if not _peers.is_empty():
            _notify("package.state", {"package_id": id, "state": st}))
    print("SiteBuilder API listening on ws://127.0.0.1:%d" % port)
    return true


func stop() -> void:
    for p in _peers:
        p.close()
    _peers.clear()
    _tcp.stop()


func _process(_delta: float) -> void:
    poll()


## Accepts connections, reads frames and answers requests (called every frame; tests call it directly).
func poll() -> void:
    if not _tcp.is_listening():
        return
    while _tcp.is_connection_available():
        var stream: StreamPeerTCP = _tcp.take_connection()
        var ws := WebSocketPeer.new()
        ws.inbound_buffer_size = BUFFER_BYTES
        ws.outbound_buffer_size = BUFFER_BYTES
        ws.max_queued_packets = 4096
        if ws.accept_stream(stream) == OK:
            _peers.append(ws)
    for i in range(_peers.size() - 1, -1, -1):
        var ws2: WebSocketPeer = _peers[i]
        ws2.poll()
        match ws2.get_ready_state():
            WebSocketPeer.STATE_OPEN:
                while ws2.get_available_packet_count() > 0:
                    var pkt: PackedByteArray = ws2.get_packet()
                    if ws2.was_string_packet():
                        var reply: String = handle_text(pkt.get_string_from_utf8())
                        if reply != "":
                            ws2.send_text(reply)
                            ws2.poll()
            WebSocketPeer.STATE_CLOSED:
                _peers.remove_at(i)


func peer_count() -> int:
    return _peers.size()


# ------------------------------------------------------------------ JSON-RPC framing

## Processes one text frame (a request or a batch). Returns the reply text ("" for notifications only).
func handle_text(text: String) -> String:
    var json := JSON.new()
    if json.parse(text) != OK:
        return JSON.stringify(_rpc.make_response_error(-32700, "Parse error"))
    var parsed: Variant = json.data
    if parsed is Array:
        var arr: Array = parsed
        if arr.is_empty():
            return JSON.stringify(_rpc.make_response_error(-32600, "Invalid Request"))
        var out: Array = []
        for item in arr:
            var r: Variant = _handle_one(item)
            if r != null:
                out.append(r)
        return JSON.stringify(out) if not out.is_empty() else ""
    var one: Variant = _handle_one(parsed)
    return JSON.stringify(one) if one != null else ""


func _normalise_id(id: Variant) -> Variant:
    if id is float and absf(id - round(id)) < 0.000001:
        return int(id)
    return id


func _handle_one(req: Variant) -> Variant:
    if not (req is Dictionary) or not (req as Dictionary).has("method"):
        return _rpc.make_response_error(-32600, "Invalid Request")
    var d: Dictionary = req
    var has_id: bool = d.has("id") and d["id"] != null
    var id: Variant = _normalise_id(d.get("id", null))
    var params: Dictionary = {}
    var rp: Variant = d.get("params", {})
    if rp is Dictionary:
        params = (rp as Dictionary).duplicate()
    if token != "" and str(params.get("token", "")) != token:
        return _rpc.make_response_error(ERR_AUTH, "unauthorized: missing or wrong token", id) if has_id else null
    params.erase("token")
    var method: String = str(d["method"])
    if not _methods.has(method):
        return _rpc.make_response_error(-32601, "Method not found: %s" % method, id) if has_id else null
    gs.last_error = ""
    var result: Variant = (_methods[method] as Callable).call(params)
    if result is Dictionary and (result as Dictionary).has("__error"):
        var e: Dictionary = result
        if not has_id:
            return null
        return _rpc.make_response_error(int(e.get("code", ERR_GAME)), str(e["__error"]), id)
    if not has_id:
        return null
    return _rpc.make_response(result, id)


func _notify(method: String, params: Dictionary) -> void:
    if _peers.is_empty():
        return
    var text: String = JSON.stringify(_rpc.make_notification(method, params))
    for ws in _peers:
        if ws.get_ready_state() == WebSocketPeer.STATE_OPEN:
            ws.send_text(text)


func _on_event_fired(ev: EventDef, choices: Array) -> void:
    var ch: Array = []
    for i in choices.size():
        ch.append({"id": i, "text": str((choices[i] as Dictionary)["label"])})
    _notify("event.fired", {"event_id": ev.id, "text": ev.text, "name": ev.name, "choices": ch})


# ------------------------------------------------------------------ helpers

func _err(msg: String, code: int = ERR_GAME) -> Dictionary:
    return {"__error": msg if msg != "" else "request failed", "code": code}


func _fail() -> Dictionary:
    return _err(gs.last_error)


func _need(params: Dictionary, keys: Array) -> String:
    for k in keys:
        if not params.has(k):
            return "missing parameter: %s" % k
    return ""


func _running() -> bool:
    return gs != null and gs.running and gs.bundle != null


func _cell(v: Variant) -> Vector2i:
    var a: Array = v
    return Vector2i(int(a[0]), int(a[1]))


func _summary() -> Dictionary:
    return ApiViews.summary(gs)


func _zone_or_err(params: Dictionary) -> Variant:
    var z: ZoneData = gs.bundle.zones_by_id.get(str(params.get("zone_id", "")), null)
    return z if z != null else _err("no such zone: %s" % str(params.get("zone_id", "")))


func _pkg_or_err(params: Dictionary) -> Variant:
    var p: PackageData = gs.bundle.packages_by_id.get(str(params.get("package_id", "")), null)
    return p if p != null else _err("no such package: %s" % str(params.get("package_id", "")))


## Wraps a handler so it first checks that a scenario is loaded.
func _reg(name: String, fn: Callable, needs_game: bool = true) -> void:
    if needs_game:
        _methods[name] = func(params: Dictionary) -> Variant:
            if not _running():
                return _err("no scenario loaded (call scenario.load)")
            return fn.call(params)
    else:
        _methods[name] = fn


func method_names() -> Array[String]:
    var out: Array[String] = []
    for k in _methods:
        out.append(str(k))
    out.sort()
    return out


# ------------------------------------------------------------------ methods

func _register() -> void:
    # scenarios
    _reg("scenario.list", _m_scenario_list, false)
    _reg("scenario.load", _m_scenario_load, false)
    # state
    _reg("state.summary", func(_p: Dictionary) -> Variant: return _summary())
    _reg("state.zones", _m_state_zones)
    _reg("state.packages", _m_state_packages)
    _reg("state.tasks", _m_state_tasks)
    _reg("state.crews", func(_p: Dictionary) -> Variant: return ApiViews.crews(gs))
    _reg("state.tiles", func(_p: Dictionary) -> Variant: return ApiViews.tiles(gs))
    _reg("state.procurement", func(_p: Dictionary) -> Variant: return ApiViews.procurement(gs))
    _reg("state.gantt", _m_state_gantt)
    _reg("state.areas", _m_state_areas)
    _reg("view.jump_to_area", _m_view_jump_to_area)
    # simulation
    _reg("sim.advance", _m_sim_advance)
    _reg("sim.resolve_event", _m_sim_resolve_event)
    _reg("sim.set_speed", _m_sim_set_speed)
    # crews
    _reg("crew.hire", _m_crew_hire)
    _reg("crew.fire", _m_crew_fire)
    _reg("crew.assign", _m_crew_assign)
    # site
    _reg("tile.place", _m_tile_place)
    _reg("tile.remove", _m_tile_remove)
    _reg("equipment.place", _m_equipment_place)
    _reg("equipment.remove", _m_equipment_remove)
    # procurement
    _reg("procure.order", _m_procure_order)
    # packages, zones, cards
    _reg("package.release", func(p: Dictionary) -> Variant: return _m_package_flag(p, true))
    _reg("package.hold", func(p: Dictionary) -> Variant: return _m_package_flag(p, false))
    _reg("package.priority", _m_package_priority)
    _reg("zone.set_shift", _m_zone_set_shift)
    _reg("card.list", _m_card_list)
    _reg("card.get", _m_card_get)
    _reg("card.apply", _m_card_apply)
    _reg("card.clear", _m_card_clear)
    _reg("card.save", _m_card_save)
    _reg("train.apply", _m_train_apply)
    # files
    _reg("plan.export", _m_plan_export)
    _reg("save.write", _m_save_write)
    _reg("save.read", _m_save_read)
    # planner (high level)
    _reg("zone.staff", _m_zone_staff)
    _reg("zone.clear_crews", _m_zone_clear_crews)
    _reg("site.auto_layout", _m_site_auto_layout)
    _reg("procure.order_all_due", _m_order_all_due)
    _reg("sim.run_until", _m_run_until)
    _reg("sim.autopilot", _m_autopilot)
    _reg("analysis.bottlenecks", func(_p: Dictionary) -> Variant: return Planner.bottlenecks(gs))
    _reg("analysis.critical", func(p: Dictionary) -> Variant: return Planner.critical(gs, int(p.get("top", 20))))
    _reg("analysis.s_curve", func(_p: Dictionary) -> Variant: return Planner.s_curve(gs))
    _reg("analysis.what_if_shift", _m_what_if_shift)
    # manual sequencing and the construction logic library (docs/06 track A)
    _reg("manual.set_mode", _m_manual_set_mode)
    _reg("manual.add_task", _m_manual_add_task)
    _reg("manual.update_task", _m_manual_update_task)
    _reg("manual.remove_task", _m_manual_remove_task)
    _reg("manual.link", _m_manual_link)
    _reg("manual.unlink", _m_manual_unlink)
    _reg("manual.apply_recipe", _m_manual_apply_recipe)
    _reg("manual.export", _m_manual_export)
    _reg("manual.tasks", _m_manual_tasks)
    _reg("logic.list", _m_logic_list)
    _reg("logic.get", _m_logic_get)
    _reg("logic.explain", _m_logic_explain)
    _reg("logic.apply", _m_manual_apply_recipe)
    # 3D view (WP-Q): progress heat overlay and element highlight
    _reg("view.heat", _m_view_heat)
    _reg("view.set_heat", _m_view_set_heat)
    _reg("view.highlight", _m_view_highlight)
    _reg("view.clear_highlight", _m_view_clear_highlight)
    # visual kit installations (WP-S)
    _reg("view.installations", _m_view_installations)
    _reg("view.element_layers", _m_view_element_layers)
    _reg("view.jump_to_installation", _m_view_jump_to_installation)


# ------------------------------------------------------------------ 3D view (WP-Q)

func _view_or_err() -> Variant:
    if bim_view == null or not is_instance_valid(bim_view):
        return _err("no 3D view attached (the game is running headless without a scene)")
    return bim_view


## Per-cell progress shares of a storey (the focused one by default); needs no view.
func _m_view_heat(p: Dictionary) -> Variant:
    var sid: String = str(p.get("storey_id", ""))
    if sid == "":
        for s in gs.bundle.storeys:
            if s.index == gs.focus_storey_index:
                sid = s.id
    if not gs.bundle.storey_index_by_id.has(sid):
        return _err("no such storey: %s" % sid)
    return ApiViews.heat_view(gs, sid)


func _m_view_set_heat(p: Dictionary) -> Variant:
    var v: Variant = _view_or_err()
    if v is Dictionary:
        return v
    var on: bool = bool(p.get("on", true))
    bim_view.call("set_heat_visible", on)
    return {"on": bool(bim_view.call("is_heat_visible"))}


func _m_view_highlight(p: Dictionary) -> Variant:
    var miss: String = _need(p, ["guids"])
    if miss != "":
        return _err(miss)
    if not (p["guids"] is Array):
        return _err("guids must be an array")
    var v: Variant = _view_or_err()
    if v is Dictionary:
        return v
    var known: Array = []
    var unknown: Array = []
    for g in p["guids"]:
        if gs.bundle.elements_by_guid.has(str(g)):
            known.append(str(g))
        else:
            unknown.append(str(g))
    var boxes: int = int(bim_view.call("highlight_elements", known))
    return {"highlighted": known.size(), "boxes": boxes, "unknown": unknown}


func _m_view_clear_highlight(_p: Dictionary) -> Variant:
    var v: Variant = _view_or_err()
    if v is Dictionary:
        return v
    bim_view.call("clear_highlight")
    return {"cleared": true}


# ------------------------------------------------------------------ visual kit installations (WP-S)

## Kit instances of the loaded bundle with fills refreshed from the simulation (needs no view).
func _kits() -> KitInstances:
    if _kit_instances == null or _kit_instances.bundle != gs.bundle:
        _kit_instances = KitInstances.new(gs.bundle, _kit_registry())
    _kit_instances.refresh_from(gs)
    return _kit_instances


func _kit_registry() -> KitRegistry:
    var r := KitRegistry.new()
    r.load_manifest()
    return r


func _m_view_installations(_p: Dictionary) -> Variant:
    return ApiViews.installations(_kits())


func _m_view_element_layers(p: Dictionary) -> Variant:
    var miss: String = _need(p, ["guid"])
    if miss != "":
        return _err(miss, -32602)
    var guid: String = str(p["guid"])
    if not gs.bundle.elements_by_guid.has(guid):
        return _err("no such element: %s" % guid)
    return ApiViews.element_layers(_kits(), guid)


func _m_view_jump_to_installation(p: Dictionary) -> Variant:
    var miss: String = _need(p, ["index"])
    if miss != "":
        return _err(miss, -32602)
    var ki: KitInstances = _kits()
    var i: int = int(p["index"])
    if i < 0 or i >= ki.count():
        return _err("no such installation: %d" % i)
    var framed: bool = jump_handler.is_valid() and bool(jump_handler.call(i))
    return {"index": i, "framed": framed, "installation": ApiViews.installation_view(ki, i)}


func _m_scenario_list(_p: Dictionary) -> Variant:
    var out: Array = []
    for info in (get_node("/root/Scenarios") as Node).call("list_bundles"):
        var d: Dictionary = info
        out.append({"id": d["id"], "name": d["name"], "sector": d["sector"], "difficulty": d["difficulty"], "tasks": d["tasks"]})
    return out


func _m_scenario_load(p: Dictionary) -> Variant:
    var m: String = _need(p, ["id"])
    if m != "":
        return _err(m, -32602)
    var scenarios: Node = get_node("/root/Scenarios")
    var path: String = ""
    for info in scenarios.call("list_bundles"):
        if str((info as Dictionary)["id"]) == str(p["id"]):
            path = str((info as Dictionary)["path"])
    if path == "":
        return _err("unknown scenario: %s" % str(p["id"]))
    if not bool(scenarios.call("select", path)):
        return _err("scenario failed to load: %s" % str(p["id"]))
    if not gs.start(scenarios.get("current_bundle")):
        return _fail()
    var cur: Node = get_tree().current_scene if get_tree() != null else null
    if cur != null and cur.name == "Menu":
        get_tree().change_scene_to_file.call_deferred("res://scenes/main.tscn")
    return _summary()


## project.areas with their status (tasks, done share, crews); `with_cells: true` adds the cell lists.
func _m_state_areas(p: Dictionary) -> Variant:
    return Areas.list(gs, bool(p.get("with_cells", false)))


## Frames the camera on an area ("" or "site": the whole site) and moves the storey focus; {framed: false} without a view.
func _m_view_jump_to_area(p: Dictionary) -> Variant:
    var id: String = str(p.get("id", ""))
    var tgt: Dictionary = Areas.target(gs, id)
    if tgt.is_empty():
        return _err("no such area: %s" % id)
    var framed: bool = area_handler.is_valid() and bool(area_handler.call(str(tgt["id"])))
    return {"id": tgt["id"], "name": tgt["name"], "framed": framed, "storey_index": tgt["storey_index"]}


func _m_state_zones(p: Dictionary) -> Variant:
    var out: Array = []
    var area_zones: Dictionary = {}
    if p.has("area_id") and str(p["area_id"]) != "":
        for zid in (gs.bundle.areas_by_id.get(str(p["area_id"]), {}) as Dictionary).get("zone_ids", []):
            area_zones[str(zid)] = true
    for z in gs.bundle.zones:
        if p.has("storey_id") and z.storey_id != str(p["storey_id"]):
            continue
        if p.has("area_id") and str(p["area_id"]) != "" and not area_zones.has(z.id):
            continue
        out.append(ApiViews.zone_view(gs, z))
    return out


func _m_state_packages(p: Dictionary) -> Variant:
    var out: Array = []
    for pk in gs.bundle.packages:
        if p.has("zone_id") and pk.zone_id != str(p["zone_id"]):
            continue
        var v: Dictionary = ApiViews.package_view(gs, pk)
        if p.has("state") and str(v["state"]) != str(p["state"]):
            continue
        out.append(v)
    return out


func _m_state_tasks(p: Dictionary) -> Variant:
    var out: Array = []
    if p.has("zone_id"):
        gs.ensure_zone_detail(str(p["zone_id"]))  # lazy per-zone detail of split bundles
    for t in gs.bundle.tasks:
        if p.has("zone_id") and t.zone_id != str(p["zone_id"]):
            continue
        if p.has("package_id") and t.package_id != str(p["package_id"]):
            continue
        out.append(ApiViews.task_view(gs, t))
    return out


func _m_state_gantt(p: Dictionary) -> Variant:
    var zones: Array = p.get("zone_ids", []) if p.get("zone_ids", []) is Array else []
    if p.has("area_id") and str(p["area_id"]) != "":
        var area: Dictionary = gs.bundle.areas_by_id.get(str(p["area_id"]), {})
        var members: Array = (area.get("zone_ids", []) as Array).duplicate()
        zones = members if zones.is_empty() else zones.filter(func(z: Variant) -> bool: return members.has(str(z)))
        if zones.is_empty():
            zones = [""]  # an unknown or empty area has no bars
    return ApiViews.gantt(gs, zones, int(p.get("from_week", -1)), int(p.get("to_week", -1)))


func _m_sim_advance(p: Dictionary) -> Variant:
    var weeks: int = int(p.get("weeks", 1))
    var stop: bool = bool(p.get("stop_on_event", true))
    if not gs.pending_event.is_empty():
        return _err("an event awaits a choice: call sim.resolve_event")
    var done: int = 0
    for i in weeks:
        if gs.finished:
            break
        if not gs.advance_week():
            if done == 0 and gs.last_error != "":
                return _fail()
            break
        done += 1
        if stop and not gs.pending_event.is_empty():
            break
    var s: Dictionary = _summary()
    s["weeks_advanced"] = done
    return s


func _m_sim_resolve_event(p: Dictionary) -> Variant:
    if gs.pending_event.is_empty():
        return _err("no event is pending")
    gs.resolve_event(int(p.get("choice", 0)))
    return _summary()


func _m_sim_set_speed(p: Dictionary) -> Variant:
    var s: int = int(p.get("speed", 0))
    if not [0, 1, 2, 4].has(s):
        return _err("speed must be 0, 1, 2 or 4", -32602)
    gs.speed = s
    return {"ok": true, "speed": s}


func _m_crew_hire(p: Dictionary) -> Variant:
    var m: String = _need(p, ["trade"])
    if m != "":
        return _err(m, -32602)
    var n: int = int(p.get("count", 1))
    var hired: Array = []
    for i in n:
        var id: int = gs.hire(str(p["trade"]))
        if id < 0:
            if hired.is_empty():
                return _fail()
            break
        hired.append(id)
    var v: Dictionary = ApiViews.crews(gs)
    v["ok"] = true
    v["hired"] = hired
    return v


func _m_crew_fire(p: Dictionary) -> Variant:
    var m: String = _need(p, ["crew_id"])
    if m != "":
        return _err(m, -32602)
    if not gs.fire(int(p["crew_id"])):
        return _fail()
    var v: Dictionary = ApiViews.crews(gs)
    v["ok"] = true
    return v


func _m_crew_assign(p: Dictionary) -> Variant:
    var m: String = _need(p, ["crew_id", "zone_id"])
    if m != "":
        return _err(m, -32602)
    if not gs.assign_crew(int(p["crew_id"]), str(p["zone_id"])):
        return _fail()
    return {"ok": true, "crew_id": int(p["crew_id"]), "zone_id": str(p["zone_id"])}


func _m_tile_place(p: Dictionary) -> Variant:
    var m: String = _need(p, ["cell", "tile"])
    if m != "":
        return _err(m, -32602)
    if not gs.place_tile(_cell(p["cell"]), str(p["tile"]), int(p.get("orientation", 0))):
        return _fail()
    return {"ok": true, "cash": gs.cash}


func _m_tile_remove(p: Dictionary) -> Variant:
    var m: String = _need(p, ["cell"])
    if m != "":
        return _err(m, -32602)
    if not gs.remove_tile(_cell(p["cell"])):
        return _fail()
    return {"ok": true}


func _m_equipment_place(p: Dictionary) -> Variant:
    var m: String = _need(p, ["equipment_id", "cell"])
    if m != "":
        return _err(m, -32602)
    if not gs.place_equipment(str(p["equipment_id"]), _cell(p["cell"])):
        return _fail()
    return {"ok": true, "cash": gs.cash}


func _m_equipment_remove(p: Dictionary) -> Variant:
    var m: String = _need(p, ["index"])
    if m != "":
        return _err(m, -32602)
    if not gs.remove_equipment(int(p["index"])):
        return _err("no such equipment")
    return {"ok": true}


func _m_procure_order(p: Dictionary) -> Variant:
    if p.has("task_id"):
        if not gs.order(str(p["task_id"])):
            return _fail()
        return {"ok": true, "ordered": [str(p["task_id"])]}
    if p.has("package_id"):
        var pk: Variant = _pkg_or_err(p)
        if pk is Dictionary:
            return pk
        var ordered: Array = []
        for t in (pk as PackageData).tasks:
            if t.lead_time_weeks > 0 and gs.order(t.task_id):
                ordered.append(t.task_id)
        if ordered.is_empty():
            return _err("no unordered long-lead tasks in package")
        return {"ok": true, "ordered": ordered}
    return _err("missing parameter: task_id or package_id", -32602)


func _m_package_flag(p: Dictionary, release: bool) -> Variant:
    var pk: Variant = _pkg_or_err(p)
    if pk is Dictionary:
        return pk
    var ok: bool = gs.release_package((pk as PackageData).package_id) if release else gs.hold_package((pk as PackageData).package_id)
    if not ok:
        return _fail()
    return ApiViews.package_view(gs, pk)


func _m_package_priority(p: Dictionary) -> Variant:
    var pk: Variant = _pkg_or_err(p)
    if pk is Dictionary:
        return pk
    var m: String = _need(p, ["priority"])
    if m != "":
        return _err(m, -32602)
    gs.set_package_priority((pk as PackageData).package_id, int(p["priority"]))
    return ApiViews.package_view(gs, pk)


func _m_zone_set_shift(p: Dictionary) -> Variant:
    var z: Variant = _zone_or_err(p)
    if z is Dictionary:
        return z
    if not gs.set_shift((z as ZoneData).id, str(p.get("mode", "single"))):
        return _fail()
    return ApiViews.zone_view(gs, z)


func _m_card_list(_p: Dictionary) -> Variant:
    var out: Array = []
    for c in gs.bundle.card_list():
        out.append({"id": c.id, "name": c.name, "description": c.description, "stations": c.stations.size(),
                "applies_to_zone_tags": c.applies_to_zone_tags.duplicate(), "auto_staff": c.auto_staff})
    return out


func _m_card_get(p: Dictionary) -> Variant:
    if not p.has("card_id"):
        var all: Array = []
        for c in gs.bundle.card_list():
            all.append(c.to_dict())
        return all
    var card: SequenceCardData = gs.bundle.cards_by_id.get(str(p["card_id"]), null)
    if card == null:
        return _err("unknown card: %s" % str(p["card_id"]))
    return card.to_dict()


func _m_card_apply(p: Dictionary) -> Variant:
    var m: String = _need(p, ["card_id", "zone_id"])
    if m != "":
        return _err(m, -32602)
    if not Cards.apply(gs, str(p["zone_id"]), str(p["card_id"]), str(p.get("auto_staff", ""))):
        return _fail()
    return ApiViews.zone_view(gs, gs.bundle.zones_by_id[str(p["zone_id"])])


func _m_card_clear(p: Dictionary) -> Variant:
    var z: Variant = _zone_or_err(p)
    if z is Dictionary:
        return z
    Cards.clear(gs, (z as ZoneData).id)
    return ApiViews.zone_view(gs, z)


func _m_card_save(p: Dictionary) -> Variant:
    if not p.has("id") or not p.has("stations"):
        return _err("card needs id and stations", -32602)
    var card: SequenceCardData = SequenceCardData.from_dict(p)
    if not card.id.begins_with("card_"):
        return _err("card id must start with card_", -32602)
    var path: String = "user://cards_%s.json" % gs.bundle.sector
    var existing: Array = []
    if FileAccess.file_exists(path):
        var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(path))
        if parsed is Array:
            existing = parsed
    var replaced: bool = false
    for i in existing.size():
        if str((existing[i] as Dictionary).get("id", "")) == card.id:
            existing[i] = card.to_dict()
            replaced = true
    if not replaced:
        existing.append(card.to_dict())
    var f := FileAccess.open(path, FileAccess.WRITE)
    if f == null:
        return _err("cannot write %s" % path)
    f.store_string(JSON.stringify(existing, " "))
    f.close()
    gs.bundle.cards_by_id[card.id] = card
    if not gs.bundle.card_ids.has(card.id):
        gs.bundle.card_ids.append(card.id)
    return {"ok": true, "path": path, "card_id": card.id}


func _m_train_apply(p: Dictionary) -> Variant:
    var m: String = _need(p, ["card_id", "zone_ids"])
    if m != "":
        return _err(m, -32602)
    var ids: Array = p["zone_ids"] if p["zone_ids"] is Array else []
    if not Cards.train(gs, str(p["card_id"]), ids, int(p.get("stagger_weeks", 1))):
        return _fail()
    var out: Array = []
    for id in ids:
        out.append(ApiViews.zone_view(gs, gs.bundle.zones_by_id[str(id)]))
    return out


func _m_plan_export(p: Dictionary) -> Variant:
    var json_path: String = str(p.get("path", ""))
    var csv_path: String = ""
    if json_path == "":
        json_path = PlanExport.user_json_path(gs.scenario.id)
        csv_path = PlanExport.user_csv_path(gs.scenario.id)
    else:
        csv_path = json_path.get_basename() + ".csv"
    if not PlanExport.export_to_path(gs, json_path, csv_path):
        return _err("plan export failed: %s" % json_path)
    return {"ok": true, "paths": [json_path, csv_path]}


func _slot_path(p: Dictionary) -> String:
    var slot: String = str(p.get("slot", ""))
    if slot == "":
        return SaveGame.path_for(gs.scenario.id)
    return "user://save_%s_%s.json" % [gs.scenario.id, slot]


func _m_save_write(p: Dictionary) -> Variant:
    var path: String = _slot_path(p)
    if not SaveGame.save(gs, path):
        return _err("save failed: %s" % path)
    return {"ok": true, "path": path}


func _m_save_read(p: Dictionary) -> Variant:
    var path: String = _slot_path(p)
    if not SaveGame.load_into(gs, path):
        return _fail()
    var s: Dictionary = _summary()
    s["ok"] = true
    return s


func _m_zone_staff(p: Dictionary) -> Variant:
    var z: Variant = _zone_or_err(p)
    if z is Dictionary:
        return z
    var level: String = str(p.get("level", "ideal"))
    if not Planner.LEVELS.has(level):
        return _err("level must be min, ideal or max", -32602)
    var res: Dictionary = Planner.staff_zone(gs, (z as ZoneData).id, level, bool(p.get("hire", false)), bool(p.get("fire_idle", true)))
    res["ok"] = true
    res["zone"] = ApiViews.zone_view(gs, z)
    return res


func _m_zone_clear_crews(p: Dictionary) -> Variant:
    var z: Variant = _zone_or_err(p)
    if z is Dictionary:
        return z
    return {"ok": true, "unassigned": Planner.clear_crews(gs, (z as ZoneData).id)}


func _m_site_auto_layout(p: Dictionary) -> Variant:
    var res: Dictionary = Planner.auto_layout(gs, int(p.get("laydown", 4)))
    res["ok"] = true
    res["cash"] = gs.cash
    return res


func _m_order_all_due(p: Dictionary) -> Variant:
    var ordered: Array[String] = Planner.order_all_due(gs, int(p.get("horizon_weeks", 8)))
    return {"ok": true, "ordered": ordered}


func _m_run_until(p: Dictionary) -> Variant:
    var cond: Dictionary = {}
    for k in ["week", "package_done", "cash_below", "state_count"]:
        if p.has(k):
            cond[k] = p[k]
    var res: Dictionary = Planner.run_until(gs, cond, int(p.get("max_weeks", 200)))
    var s: Dictionary = _summary()
    s["stopped"] = res["stopped"]
    return s


func _m_autopilot(p: Dictionary) -> Variant:
    var level: String = str(p.get("staff_level", "ideal"))
    if not Planner.LEVELS.has(level):
        return _err("staff_level must be min, ideal or max", -32602)
    var res: Dictionary = Planner.autopilot(gs, int(p.get("weeks", 1)), level, float(p.get("crew_fraction", 1.0)),
            bool(p.get("hire", true)), int(p.get("horizon_weeks", 8)), bool(p.get("fire_idle", true)))
    var s: Dictionary = _summary()
    s["weeks_run"] = res["weeks_run"]
    return s


func _m_what_if_shift(p: Dictionary) -> Variant:
    var z: Variant = _zone_or_err(p)
    if z is Dictionary:
        return z
    return Planner.what_if_shift(gs, (z as ZoneData).id)


# ------------------------------------------------------------------ manual.* and logic.*

func _task_id_param(p: Dictionary, key: String = "task_id") -> String:
    return str(p.get(key, p.get("id", "")))


func _task_view_of(id: String) -> Variant:
    var t: TaskData = gs.bundle.tasks_by_id.get(gs.bundle.resolve_task_id(id), null)
    return ApiViews.task_view(gs, t) if t != null else null


func _m_manual_set_mode(p: Dictionary) -> Variant:
    var z: Variant = _zone_or_err(p)
    if z is Dictionary:
        return z
    var on: bool = bool(p.get("on", true))
    if not Manual.set_mode(gs, (z as ZoneData).id, on):
        return _fail()
    return {"ok": true, "zone_id": (z as ZoneData).id, "manual_mode": on, "manual_zones": gs.manual_zones.size(),
            "zone": ApiViews.zone_view(gs, z)}


func _m_manual_add_task(p: Dictionary) -> Variant:
    var id: String = Manual.add_task(gs, p)
    if id == "":
        return _fail()
    return {"ok": true, "task_id": id, "task": _task_view_of(id)}


func _m_manual_update_task(p: Dictionary) -> Variant:
    var id: String = _task_id_param(p)
    if id == "":
        return _err("missing parameter: task_id", -32602)
    var fields: Dictionary = p.get("fields", {}) if p.get("fields", {}) is Dictionary else {}
    if fields.is_empty():
        for k in p:
            if k != "task_id" and k != "id" and k != "fields":
                fields[k] = p[k]
    if not Manual.update_task(gs, id, fields):
        return _fail()
    return {"ok": true, "task_id": gs.bundle.resolve_task_id(id), "task": _task_view_of(id)}


func _m_manual_remove_task(p: Dictionary) -> Variant:
    var id: String = _task_id_param(p)
    if id == "":
        return _err("missing parameter: task_id", -32602)
    if not Manual.remove_task(gs, id, bool(p.get("bridge", true))):
        return _fail()
    return {"ok": true, "removed": id}


func _m_manual_link(p: Dictionary) -> Variant:
    var from_id: String = str(p.get("from_id", p.get("from", "")))
    var to_id: String = str(p.get("to_id", p.get("to", "")))
    if from_id == "" or to_id == "":
        return _err("missing parameter: from_id / to_id", -32602)
    if not Manual.link(gs, from_id, to_id, str(p.get("type", p.get("link_type", "FS"))), int(p.get("lag_days", p.get("lag", 0)))):
        return _fail()
    return {"ok": true, "task": _task_view_of(to_id)}


func _m_manual_unlink(p: Dictionary) -> Variant:
    var from_id: String = str(p.get("from_id", p.get("from", "")))
    var to_id: String = str(p.get("to_id", p.get("to", "")))
    if from_id == "" or to_id == "":
        return _err("missing parameter: from_id / to_id", -32602)
    if not Manual.unlink(gs, from_id, to_id):
        return _fail()
    return {"ok": true, "task": _task_view_of(to_id)}


func _m_manual_apply_recipe(p: Dictionary) -> Variant:
    var m: String = _need(p, ["recipe_id"])
    if m != "":
        return _err(m, -32602)
    var res: Dictionary = Manual.apply_recipe(gs, str(p["recipe_id"]), str(p.get("zone_id", "")),
            str(p.get("element_guid", "")) if p.get("element_guid", null) != null else "", bool(p.get("include_optional", false)))
    if res.is_empty():
        return _fail()
    return res


func _m_manual_export(p: Dictionary) -> Variant:
    var doc: Dictionary = Manual.export_doc(gs)
    var path: String = str(p.get("path", ""))
    if path != "":
        var f := FileAccess.open(path, FileAccess.WRITE)
        if f == null:
            return _err("cannot write %s" % path)
        f.store_string(JSON.stringify(doc, " ") + "\n")
        f.close()
        doc["path"] = path
    return doc


func _m_manual_tasks(p: Dictionary) -> Variant:
    var out: Array = []
    for t in gs.bundle.tasks:
        if not t.is_authored():
            continue
        if p.has("zone_id") and t.zone_id != str(p["zone_id"]):
            continue
        out.append(ApiViews.task_view(gs, t))
    return out


func _m_logic_list(p: Dictionary) -> Variant:
    return LogicLib.list_recipes(gs, str(p.get("sector", "")))


func _m_logic_get(p: Dictionary) -> Variant:
    var id: String = str(p.get("id", p.get("recipe_id", "")))
    if id == "":
        return _err("missing parameter: id", -32602)
    var r: Dictionary = LogicLib.get_recipe(gs, id)
    if r.is_empty():
        return _fail()
    return r


func _m_logic_explain(p: Dictionary) -> Variant:
    var res: Dictionary = {}
    if p.has("element_guid") and p["element_guid"] != null and str(p["element_guid"]) != "":
        res = LogicLib.explain_element(gs, str(p["element_guid"]))
    elif p.has("zone_id"):
        res = LogicLib.explain_zone(gs, str(p["zone_id"]))
    else:
        return _err("missing parameter: element_guid or zone_id", -32602)
    if res.is_empty():
        return _fail()
    return res
