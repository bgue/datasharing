extends TC
## Control API (docs/05 section 6): a real ApiServer on an ephemeral port, driven through a
## WebSocketPeer client inside the test (both sides polled from the test loop).

var _server: ApiServer = null
var _gs: SimState = null
var _client: WebSocketPeer = null
var _next_id: int = 1
var notifications: Array[Dictionary] = []


func _root() -> Window:
    return Engine.get_main_loop().root


func _start_server(token: String = "") -> void:
    _gs = SimState.new()
    _server = ApiServer.new()
    _root().add_child(_server)
    ok(_server.start(_gs, 0, token), "server listens on an ephemeral port")
    ok(_server.port > 0, "port assigned: %d" % _server.port)
    _client = WebSocketPeer.new()
    _client.inbound_buffer_size = 16 * 1024 * 1024
    _client.outbound_buffer_size = 16 * 1024 * 1024
    ok(_client.connect_to_url("ws://127.0.0.1:%d" % _server.port) == OK, "client connects")
    var tries: int = 0
    while _client.get_ready_state() != WebSocketPeer.STATE_OPEN and tries < 2000:
        _client.poll()
        _server.poll()
        OS.delay_msec(1)
        tries += 1
    eq(_client.get_ready_state(), WebSocketPeer.STATE_OPEN, "websocket handshake completed")


func _stop_server() -> void:
    _client.close()
    _server.stop()
    _root().remove_child(_server)
    _server.free()
    _gs.free()
    _root().get_node("Scenarios").set("current_bundle", null)


## Sends one frame and waits for the matching reply frame. Returns the parsed reply.
func _send_frame(payload: Variant) -> Variant:
    _client.send_text(JSON.stringify(payload))
    var tries: int = 0
    while tries < 20000:
        _client.poll()
        _server.poll()
        while _client.get_available_packet_count() > 0:
            var msg: Variant = JSON.parse_string(_client.get_packet().get_string_from_utf8())
            if msg is Dictionary and (msg as Dictionary).has("method") and not (msg as Dictionary).has("id"):
                notifications.append(msg)
                continue
            if msg is Array:
                var only_notes: bool = true
                for m in msg:
                    if not (m is Dictionary and (m as Dictionary).has("method") and not (m as Dictionary).has("id")):
                        only_notes = false
                if only_notes:
                    continue
            return msg
        OS.delay_msec(1)
        tries += 1
    return null


## Calls a method; returns the whole response dictionary.
func rpc(method: String, params: Dictionary = {}, token: String = "") -> Dictionary:
    var p: Dictionary = params.duplicate()
    if token != "":
        p["token"] = token
    var id: int = _next_id
    _next_id += 1
    var reply: Variant = _send_frame({"jsonrpc": "2.0", "id": id, "method": method, "params": p})
    ok(reply is Dictionary, "got a reply to %s" % method)
    if reply is Dictionary:
        eq(int((reply as Dictionary).get("id", -1)), id, "reply id matches for %s" % method)
        return reply
    return {}


## Calls and unwraps a successful result.
func call_ok(method: String, params: Dictionary = {}) -> Variant:
    var r: Dictionary = rpc(method, params)
    ok(r.has("result"), "%s succeeded: %s" % [method, str(r.get("error", ""))])
    return r.get("result", null)


func test_methods_registered() -> void:
    _start_server()
    var names: Array[String] = _server.method_names()
    for m in ["scenario.list", "scenario.load", "state.summary", "state.zones", "state.packages", "state.tasks", "state.crews",
            "state.tiles", "state.procurement", "state.gantt", "sim.advance", "sim.resolve_event", "sim.set_speed",
            "crew.hire", "crew.fire", "crew.assign", "tile.place", "tile.remove", "equipment.place", "equipment.remove",
            "procure.order", "package.release", "package.hold", "package.priority", "zone.set_shift", "card.list", "card.get",
            "card.apply", "card.clear", "card.save", "train.apply", "plan.export", "save.write", "save.read",
            "zone.staff", "zone.clear_crews", "site.auto_layout", "procure.order_all_due", "sim.run_until", "sim.autopilot",
            "analysis.bottlenecks", "analysis.critical", "analysis.s_curve", "analysis.what_if_shift"]:
        ok(names.has(m), "method registered: %s" % m)
    _stop_server()


func test_errors_and_framing() -> void:
    _start_server()
    var r: Dictionary = rpc("no.such_method")
    eq(int(r["error"]["code"]), -32601, "unknown method -> -32601")
    r = rpc("state.summary")
    eq(int(r["error"]["code"]), -32000, "no scenario loaded -> game error")
    ok(String(r["error"]["message"]).contains("scenario"), "message explains: " + str(r["error"]["message"]))
    r = rpc("scenario.load", {"id": "does_not_exist"})
    eq(int(r["error"]["code"]), -32000, "unknown scenario is a game error")
    # parse error and invalid request
    var bad: Variant = _send_frame("this is not an object")  # a JSON string, not a request
    ok(bad is Dictionary and (bad as Dictionary).has("error"), "invalid request answered with an error")
    _client.send_text("{not json")
    var tries: int = 0
    var parse_err: Variant = null
    while tries < 5000 and parse_err == null:
        _client.poll()
        _server.poll()
        if _client.get_available_packet_count() > 0:
            parse_err = JSON.parse_string(_client.get_packet().get_string_from_utf8())
        OS.delay_msec(1)
        tries += 1
    eq(int(parse_err["error"]["code"]), -32700, "malformed JSON -> parse error")
    _stop_server()


func test_token_is_required() -> void:
    _start_server("secret")
    var r: Dictionary = rpc("scenario.list")
    ok(r.has("error"), "missing token rejected")
    r = rpc("scenario.list", {}, "wrong")
    ok(r.has("error"), "wrong token rejected")
    r = rpc("scenario.list", {}, "secret")
    ok(r.has("result"), "correct token accepted")
    _stop_server()


func test_batch_requests() -> void:
    _start_server()
    var reply: Variant = _send_frame([
        {"jsonrpc": "2.0", "id": 101, "method": "scenario.list", "params": {}},
        {"jsonrpc": "2.0", "id": 102, "method": "no.such_method"},
        {"jsonrpc": "2.0", "method": "scenario.list"},  # notification: no reply
    ])
    ok(reply is Array, "batch reply is an array")
    eq((reply as Array).size(), 2, "one reply per request, none for the notification")
    eq(int(reply[0]["id"]), 101, "ids preserved")
    ok(reply[0].has("result"), "first ok")
    ok(reply[1].has("error"), "second failed independently")
    _stop_server()


func test_full_session() -> void:
    _start_server()
    var list: Array = call_ok("scenario.list")
    var ids: Array = list.map(func(e: Dictionary) -> String: return str(e["id"]))
    ok(ids.has("minimal"), "minimal listed")
    var summary: Dictionary = call_ok("scenario.load", {"id": "minimal"})
    eq(int(summary["week"]), 0, "loaded at week 0")
    eq(int(summary["contract_weeks"]), 6, "contract weeks")
    ok(summary.has("counts") and summary.has("score") and summary.has("budget") and summary.has("day"), "summary fields")
    eq(summary["pending_event"], null, "no pending event")
    var s2: Dictionary = call_ok("state.summary")
    eq(int(s2["tasks_total"]), 12, "state.summary: 12 tasks")
    _gs.cash += 1.0e6  # the test is about the API, not about the economy

    # zones and packages
    var zones: Array = call_ok("state.zones")
    eq(zones.size(), 2, "two zones")
    for k in ["zone_id", "name", "max_crews", "crews", "shift_mode", "card", "station", "faces", "color"]:
        ok((zones[0] as Dictionary).has(k), "zone view has %s" % k)
    eq(call_ok("state.zones", {"storey_id": "L01"}).size(), 1, "storey filter")
    var pkgs: Array = call_ok("state.packages", {"zone_id": "L00-Z1"})
    eq(pkgs.size(), 5, "five packages in the ground zone")
    for k in ["package_id", "state", "crews_now", "crew_days_done", "remaining_crew_days", "blocked_reason", "station", "behind_takt", "crew_profile"]:
        ok((pkgs[0] as Dictionary).has(k), "package view has %s" % k)
    eq(call_ok("state.packages", {"state": "ready"}).size(), 1, "state filter: only the footings are ready")
    eq(call_ok("state.tasks", {"package_id": "P00001"}).size(), 4, "tasks of a package")
    eq(call_ok("state.tasks", {"zone_id": "L01-Z1"}).size(), 1, "tasks of a zone")

    # site and crews
    var layout: Dictionary = call_ok("site.auto_layout", {"laydown": 2})
    ok(layout["ok"] and int(layout["roads"]) >= 1, "auto_layout placed roads")
    var tiles: Dictionary = call_ok("state.tiles")
    ok(bool(tiles["access"]["L00-Z1"]), "ground zone reachable after the layout")
    var placed: Dictionary = call_ok("tile.place", {"cell": [7, 5], "tile": "hoarding"})
    ok(placed["ok"], "tile.place")
    ok(rpc("tile.place", {"cell": [2, 2], "tile": "hoarding"}).has("error"), "footprint refused with the game's message")
    ok(call_ok("tile.remove", {"cell": [7, 5]})["ok"], "tile.remove")
    var hired: Dictionary = call_ok("crew.hire", {"trade": "concrete", "count": 2})
    eq(hired["hired"].size(), 2, "two concrete crews hired")
    eq(hired["caps"]["concrete"], 2, "caps reported")
    ok(rpc("crew.hire", {"trade": "concrete"}).has("error"), "cap reached: error with last_error")
    var crew_id: int = int(hired["hired"][0])
    var assigned: Dictionary = call_ok("crew.assign", {"crew_id": crew_id, "zone_id": "L00-Z1"})
    ok(assigned["ok"], "crew.assign")
    ok(rpc("crew.assign", {"crew_id": crew_id, "zone_id": "nowhere"}).has("error"), "unknown zone: error")
    var crews: Dictionary = call_ok("state.crews")
    eq(crews["crews"].size(), 2, "state.crews lists the crews")
    eq(crews["crews"][0]["zone_id"], "L00-Z1", "with their zone")
    ok(crews.has("hires_left"), "hires_left reported")

    # packages: hold / release / priority
    var held: Dictionary = call_ok("package.hold", {"package_id": "P00001"})
    eq(held["state"], "held", "package.hold returns the view")
    var rel: Dictionary = call_ok("package.release", {"package_id": "P00001"})
    ok(rel["state"] != "held", "package.release returns the view")
    eq(call_ok("package.priority", {"package_id": "P00001", "priority": -1})["priority"], -1, "package.priority")
    ok(rpc("package.hold", {"package_id": "P99999"}).has("error"), "unknown package: error")

    # cards
    var card: Dictionary = {"id": "card_api", "name": "API card", "stations": [
        {"name": "Foundations", "select": {"phase": "substructure"}, "takt_weeks": 1},
        {"name": "Frame", "select": {"phase": "superstructure"}, "takt_weeks": 1}], "auto_staff": "off"}
    var saved: Dictionary = call_ok("card.save", card)
    ok(saved["ok"], "card.save")
    var cl: Array = call_ok("card.list")
    ok(cl.any(func(c: Dictionary) -> bool: return c["id"] == "card_api"), "saved card listed")
    eq(call_ok("card.get", {"card_id": "card_api"})["stations"].size(), 2, "card.get")
    var z_card: Dictionary = call_ok("card.apply", {"card_id": "card_api", "zone_id": "L00-Z1"})
    eq(z_card["card"], "card_api", "card.apply: zone view shows the card")
    eq(z_card["station"], "Foundations", "current station")
    ok(call_ok("package.release", {"package_id": "P00002"})["released"], "player may still release a held package")
    eq(call_ok("card.clear", {"zone_id": "L00-Z1"})["card"], null, "card.clear")
    ok(rpc("card.apply", {"card_id": "nope", "zone_id": "L00-Z1"}).has("error"), "unknown card: error")
    var trained: Array = call_ok("train.apply", {"card_id": "card_api", "zone_ids": ["L00-Z1", "L01-Z1"], "stagger_weeks": 2})
    eq(trained.size(), 2, "train.apply returns the zones")
    eq(int(trained[1]["hold_until_week"]), 2, "zone 1 held by the stagger")
    call_ok("card.clear", {"zone_id": "L00-Z1"})
    call_ok("card.clear", {"zone_id": "L01-Z1"})

    # shift
    var shifted: Dictionary = call_ok("zone.set_shift", {"zone_id": "L00-Z1", "mode": "double"})
    eq(shifted["shift_mode"], "double", "zone.set_shift")
    var refused: Dictionary = rpc("zone.set_shift", {"zone_id": "L01-Z1", "mode": "double"})
    ok(refused.has("error") and String(refused["error"]["message"]).contains("occupied_adjacent"), "quiet-hours zone refused")
    call_ok("zone.set_shift", {"zone_id": "L00-Z1", "mode": "single"})

    # staffing helpers and analysis
    ok(call_ok("zone.staff", {"zone_id": "L00-Z1", "level": "ideal", "hire": false})["ok"], "zone.staff")
    ok(call_ok("zone.clear_crews", {"zone_id": "L00-Z1"})["ok"], "zone.clear_crews")
    ok(call_ok("analysis.bottlenecks").has("zones_with_ready_work_and_no_crews"), "analysis.bottlenecks")
    ok(call_ok("analysis.critical", {"top": 3}) is Array, "analysis.critical")
    ok(call_ok("analysis.s_curve").has("planned"), "analysis.s_curve")
    ok(call_ok("analysis.what_if_shift", {"zone_id": "L00-Z1"}).has("weeks_saved"), "analysis.what_if_shift")
    ok(call_ok("procure.order_all_due", {"horizon_weeks": 8})["ok"], "procure.order_all_due")
    ok(call_ok("state.procurement") is Array, "state.procurement")
    var gantt: Dictionary = call_ok("state.gantt", {"zone_ids": ["L00-Z1"]})
    eq(gantt["bars"].size(), 5, "gantt bars for the zone")
    for k in ["package_id", "zone_id", "storey_id", "name", "planned_start_day", "planned_finish_day", "actual_start_day",
            "actual_finish_day", "progress", "state", "discipline", "understaffed", "held", "behind_takt"]:
        ok((gantt["bars"][0] as Dictionary).has(k), "gantt bar has %s" % k)

    # time
    var adv: Dictionary = call_ok("sim.advance", {"weeks": 1})
    eq(int(adv["week"]), 1, "sim.advance: one week")
    eq(int(adv["weeks_advanced"]), 1, "reports weeks advanced")
    ok(notifications.any(func(n: Dictionary) -> bool: return n["method"] == "week.advanced" and int(n["params"]["week"]) == 1), "week.advanced notification")
    ok(call_ok("sim.set_speed", {"speed": 2})["ok"], "sim.set_speed")
    call_ok("sim.set_speed", {"speed": 0})
    ok(rpc("sim.set_speed", {"speed": 3}).has("error"), "bad speed rejected")
    var until: Dictionary = call_ok("sim.run_until", {"week": 3})
    ok(int(until["week"]) >= 3, "sim.run_until a week")
    ok(rpc("sim.resolve_event", {"choice": 0}).has("error"), "no event pending: error")

    # save / read
    var sv: Dictionary = call_ok("save.write", {"slot": "apitest"})
    ok(sv["ok"], "save.write")
    var wk: int = _gs.week
    call_ok("sim.advance", {"weeks": 1})
    var rd: Dictionary = call_ok("save.read", {"slot": "apitest"})
    eq(int(rd["week"]), wk, "save.read restores the week")
    DirAccess.remove_absolute(ProjectSettings.globalize_path(sv["path"]))

    # autopilot to completion
    var done: Dictionary = call_ok("sim.autopilot", {"weeks": 40, "staff_level": "ideal"})
    for i in 10:
        if done["pending_event"] == null:
            break
        done = call_ok("sim.resolve_event", {"choice": 0})
        if done["finished"]:
            break
        done = call_ok("sim.autopilot", {"weeks": 40})
    ok(bool(done["finished"]), "autopilot finished the level (week %d, cash %d)" % [int(done["week"]), int(done["cash"])])
    ok(bool(done["won"]), "and won it: %s" % str(done["result"].get("reason", "")))
    eq(int(done["tasks_finished"]), 12, "all 12 tasks finished")
    ok(notifications.any(func(n: Dictionary) -> bool: return n["method"] == "level.finished"), "level.finished notification")
    ok(notifications.any(func(n: Dictionary) -> bool: return n["method"] == "package.state"), "package.state notifications")

    # export
    var path: String = "res://tests/_api_plan.json"
    var ex: Dictionary = call_ok("plan.export", {"path": path})
    ok(ex["ok"], "plan.export")
    var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(path))
    ok(parsed is Dictionary and (parsed["tasks"] as Array).size() == 12, "export holds 12 tasks")
    for t in parsed["tasks"]:
        ok(t["actual_finish_day"] != null, "%s has an actual finish" % t["task_id"])
    for p in ex["paths"]:
        DirAccess.remove_absolute(ProjectSettings.globalize_path(p))
    _stop_server()


func test_events_are_notified_and_resolved() -> void:
    _start_server()
    call_ok("scenario.load", {"id": "minimal"})
    _gs.scenario.events.clear()
    _gs.scenario.events.append(EventDef.from_dict({"id": "rfi", "name": "RFI", "text": "Steel RFI", "weight": 1, "effect": {},
        "choices": [{"label": "Pay", "effect": {"cash_delta": -1000}}, {"label": "Wait", "effect": {}}]}))
    _gs.rng.seed = 1
    var tries: int = 0
    var summary: Dictionary = {}
    while tries < 30:
        summary = call_ok("sim.advance", {"weeks": 1})
        if summary["pending_event"] != null:
            break
        tries += 1
    ok(summary["pending_event"] != null, "an event with choices eventually fires")
    var ev: Dictionary = summary["pending_event"]
    eq(ev["choices"].size(), 2, "summary lists the choices")
    eq(ev["choices"][0]["text"], "Pay", "choice text")
    ok(notifications.any(func(n: Dictionary) -> bool: return n["method"] == "event.fired" and n["params"]["event_id"] == "rfi"), "event.fired notification")
    ok(rpc("sim.advance", {"weeks": 1}).has("error"), "advancing with a pending event is refused")
    var after: Dictionary = call_ok("sim.resolve_event", {"choice": 0})
    eq(after["pending_event"], null, "resolved")
    _stop_server()
