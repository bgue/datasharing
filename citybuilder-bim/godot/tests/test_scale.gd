extends TC
## Scale smoke test: every bundle under res://scenarios is played for 40 weeks with a simple
## scripted planner. Checks performance (average advance_week time), progress and snapshot sanity.

const WEEKS: int = 40
const MAX_AVG_MS: float = 250.0
const MIN_FINISHED_FRACTION: float = 0.20
## Per-bundle target for the funded max-crews run (everything else needs MIN_FINISHED_FRACTION).
const TARGET_FRACTION: Dictionary = {"healthcare_standard": 0.70, "civil_standard": 0.70}
const RS := TaskRuntime.State
## Cash added at the start: this test is about throughput and flow at scale, not economic balance
## (hiring every available crew bankrupts the shipped scenarios within 6 to 22 weeks, see the report).
## Laydown tiles for the whole site (4 cells each): with max crews, one yard throttles starts.
const LAYDOWN_TILES: int = 4
const FUNDING_TOP_UP: float = 1.0e9


## Microseconds spent in the test planner (excluded from the simulation timings).
var planner_us: int = 0


func _bundle_paths() -> Array[String]:
    var out: Array[String] = []
    var dir := DirAccess.open("res://scenarios")
    if dir == null:
        return out
    var names: PackedStringArray = dir.get_directories()
    for n in names:
        var p: String = "res://scenarios/%s/sequence.json" % n
        if FileAccess.file_exists(p):
            out.append(p)
    out.sort()
    return out


## Shortest path of placeable cells from `starts` (the road network so far) to any cell next to the zone.
func _route_to_zone(gs: SimState, starts: Array[Vector2i], zone: ZoneData) -> Array[Vector2i]:
    var zone_set: Dictionary = {}
    for c in zone.cells:
        zone_set[c] = true
    var prev: Dictionary = {}
    var queue: Array[Vector2i] = []
    for s in starts:
        prev[s] = s
        queue.append(s)
    var head: int = 0
    while head < queue.size():
        var cur: Vector2i = queue[head]
        head += 1
        for d in Logistics.DIRS4:
            if zone_set.has(cur + d):
                var path: Array[Vector2i] = []
                var c: Vector2i = cur
                while not starts.has(c):
                    path.append(c)
                    c = prev[c]
                return path
        for d in Logistics.DIRS4:
            var n: Vector2i = cur + d
            if prev.has(n) or zone_set.has(n):
                continue
            if gs.placement_error(n, "haul_road") != "":
                continue
            prev[n] = cur
            queue.append(n)
    return []


func _lay_out_site(gs: SimState) -> void:
    var b: SequenceBundle = gs.bundle
    var gate: Vector2i = b.scenario.gates[0]
    var network: Array[Vector2i] = [gate]
    var zones: Array[ZoneData] = b.zones.duplicate()
    zones.sort_custom(func(x: ZoneData, y: ZoneData) -> bool:
        return Vector2(gate).distance_to(x.centroid()) < Vector2(gate).distance_to(y.centroid()))
    for z in zones:
        if gs.zone_access(z.id):
            continue
        for c in _route_to_zone(gs, network, z):
            if gs.place_tile(c, "haul_road"):
                network.append(c)
    # one laydown yard near the road network
    var placed_laydown: int = 0
    for r in range(1, 6):
        for net in network:
            for d in Logistics.DIRS4:
                var c: Vector2i = net + d * r
                if placed_laydown < LAYDOWN_TILES and gs.tile_at(c) == "" and gs.place_tile(c, "laydown"):
                    placed_laydown += 1
    ok(placed_laydown >= 1, "%s: laydown placed" % b.project_name)
    _place_cranes(gs)


## Crane pads + cranes (only if some task requires one): each crane goes on the free cell that
## covers the most still-uncovered crane tasks, up to the equipment's max_count.
func _place_cranes(gs: SimState) -> void:
    var b: SequenceBundle = gs.bundle
    var crane_cells: Array[Vector2i] = []
    for t in b.tasks:
        if t.requires_crane:
            for c in t.cells:
                if not crane_cells.has(c):
                    crane_cells.append(c)
    if crane_cells.is_empty():
        return
    for e in b.scenario.equipment:
        if not e.is_crane():
            continue
        for n in e.max_count:
            var best: Vector2i = Vector2i.ZERO
            var best_cover: int = 0
            for x in range(b.site_rect.position.x, b.site_rect.end.x):
                for y in range(b.site_rect.position.y, b.site_rect.end.y):
                    var cand := Vector2i(x, y)
                    if gs.tile_at(cand) != "" or gs.placement_error(cand, "crane_pad") != "":
                        continue
                    var cover: int = 0
                    for c in crane_cells:
                        if Vector2(c - cand).length() <= float(e.reach_cells):
                            cover += 1
                    if cover > best_cover:
                        best_cover = cover
                        best = cand
            if best_cover == 0 or not gs.place_tile(best, "crane_pad"):
                break
            if not gs.place_equipment(e.id, best):
                break
            var remaining: Array[Vector2i] = []
            for c in crane_cells:
                if Vector2(c - best).length() > float(e.reach_cells):
                    remaining.append(c)
            crane_cells = remaining
            if crane_cells.is_empty():
                return


## Orders every long-lead item as early as possible (the planner has no reason to wait).
func _order_long_lead(gs: SimState) -> void:
    if gs.week > 0:
        return
    for t in gs.bundle.tasks:
        if t.lead_time_weeks > 0:
            gs.order(t.task_id)


func _trade_has_work(gs: SimState, trade: String) -> bool:
    for tk in gs.bundle.tasks:
        var task: TaskData = tk
        if task.trade != trade:
            continue
        var st: int = (gs.runtime[task.task_id] as TaskRuntime).state
        if st == RS.READY or st == RS.ACTIVE or st == RS.REWORK:
            return true
    return false


## Hires crews of every trade that has ready work, up to `fraction` of the available crews
## (at least one per trade), respecting the weekly hire cap.
func _manage_crews(gs: SimState, fraction: float = 1.0) -> void:
    for t in gs.bundle.trades:
        if not _trade_has_work(gs, t.id):
            continue
        var cap: int = maxi(1, int(floor(float(gs.crews_cap(t.id)) * fraction)))
        while gs.crew_count(t.id) < mini(cap, gs.crews_cap(t.id)) and gs.hires_left_this_week(t.id) > 0:
            if gs.hire(t.id) < 0:
                break


## Deals each trade's crews round-robin over the zones that hold ready / active / rework work of
## that trade. Called before every working day (SimState.before_work_day), so crews follow the work.
func _assign_round_robin(gs: SimState) -> void:
    for t in gs.bundle.trades:
        var zones: Array[String] = []
        for z in gs.bundle.zones:
            for tk in gs.bundle.tasks_by_zone.get(z.id, []):
                var task: TaskData = tk
                if task.trade != t.id:
                    continue
                var st: int = (gs.runtime[task.task_id] as TaskRuntime).state
                if st == RS.READY or st == RS.ACTIVE or st == RS.REWORK:
                    zones.append(z.id)
                    break
        var i: int = 0
        for c in gs.crews:
            if str(c["trade"]) != t.id:
                continue
            gs.assign_crew(int(c["id"]), zones[i % zones.size()] if not zones.is_empty() else "")
            i += 1


## Installs the daily planner hook and returns nothing; `fraction` limits crews per trade.
func _install_planner(gs: SimState, fraction: float = 1.0) -> void:
    gs.before_work_day = func(_d: int) -> void:
        var t0: int = Time.get_ticks_usec()
        _manage_crews(gs, fraction)
        _assign_round_robin(gs)
        planner_us += Time.get_ticks_usec() - t0


func test_every_bundle_plays_40_weeks() -> void:
    var paths: Array[String] = _bundle_paths()
    ok(not paths.is_empty(), "at least one bundle present")
    for path in paths:
        var b := SequenceBundle.load_from_path(path)
        ok(b.valid, "%s valid: %s" % [path, ", ".join(b.errors)])
        if not b.valid:
            continue
        var gs := SimState.new()
        ok(gs.start(b), "%s starts" % path)
        gs.cash += FUNDING_TOP_UP
        _lay_out_site(gs)
        _install_planner(gs)
        var total_us: int = 0
        var max_us: int = 0
        var weeks_run: int = 0
        for w in WEEKS:
            if gs.finished:
                break
            if not gs.pending_event.is_empty():
                gs.resolve_event(0)
            _order_long_lead(gs)
            var t0: int = Time.get_ticks_usec()
            planner_us = 0
            var advanced: bool = gs.advance_week()
            var dt: int = Time.get_ticks_usec() - t0 - planner_us
            ok(advanced, "%s week %d advanced" % [b.scenario.id, w])
            total_us += dt
            max_us = maxi(max_us, dt)
            weeks_run += 1
        var avg_ms: float = float(total_us) / 1000.0 / float(maxi(weeks_run, 1))
        var fin: int = gs.finished_task_count()
        var frac: float = float(fin) / float(b.tasks.size())
        print("      [scale] %-20s tasks=%d links=%d weeks=%d avg=%.1f ms/week max=%.1f ms finished=%d (%.0f%%) cash=%s game_over=%s" % [
            b.scenario.id, b.tasks.size(), _link_count(b), weeks_run, avg_ms, float(max_us) / 1000.0, fin,
            frac * 100.0, Fmt.money(gs.cash), str(gs.finished and not gs.won)])
        ok(avg_ms < MAX_AVG_MS, "%s: average advance_week %.1f ms must be < %.0f ms" % [b.scenario.id, avg_ms, MAX_AVG_MS])
        var need: float = TARGET_FRACTION.get(b.scenario.id, MIN_FINISHED_FRACTION)
        ok(frac >= need or gs.won, "%s: %.1f%% finished by week %d (need >= %.0f%%)" % [b.scenario.id, frac * 100.0, weeks_run, need * 100.0])
        var snap: Dictionary = gs.snapshot()
        var sum: int = 0
        for k in snap["states"]:
            sum += int(snap["states"][k])
        eq(sum, b.tasks.size(), "%s: snapshot state counts sum to the task count" % b.scenario.id)
        eq(int(snap["tasks_total"]), b.tasks.size(), "%s: snapshot tasks_total" % b.scenario.id)
        eq(int(snap["tasks_finished"]), fin, "%s: snapshot tasks_finished" % b.scenario.id)
        gs.free()


func _link_count(b: SequenceBundle) -> int:
    var n: int = 0
    for t in b.tasks:
        n += t.predecessors.size()
    return n


## The visual layers (BimView colours, zone overlay, HUD panels) must also keep up with big bundles.
func test_main_scene_frame_cost_at_scale() -> void:
    var root: Window = Engine.get_main_loop().root
    var sc: Node = root.get_node("Scenarios")
    for path in _bundle_paths():
        ok(bool(sc.call("select", path)), "select %s" % path)
        var main: Node = (load("res://scenes/main.tscn") as PackedScene).instantiate()
        root.add_child(main)
        var gs: SimState = main.get("gs")
        gs.cash += FUNDING_TOP_UP
        _lay_out_site(gs)
        _install_planner(gs)
        var total_us: int = 0
        var weeks: int = 6
        for w in weeks:
            _order_long_lead(gs)
            gs.advance_week()
            var t0: int = Time.get_ticks_usec()
            main.get("bim_view").call("_process", 0.016)
            main.get("overlay").call("_process", 0.016)
            main.get("top_bar").call("_process", 0.016)
            (main.get("inspector") as ZoneInspector).show_zone(gs.bundle.zones[w % gs.bundle.zones.size()].id)
            main.get("inspector").call("_process", 0.016)
            main.get("crew_panel").call("_process", 0.016)
            main.get("procurement").call("_process", 0.016)
            main.get("charts").call("_redraw")
            total_us += Time.get_ticks_usec() - t0
        var avg_ms: float = float(total_us) / 1000.0 / float(weeks)
        print("      [scale-ui] %-20s elements=%d avg visual+HUD refresh %.1f ms per week" % [gs.scenario.id, gs.bundle.elements.size(), avg_ms])
        ok(avg_ms < MAX_AVG_MS, "%s: visual + HUD refresh %.1f ms per week must be < %.0f ms" % [gs.scenario.id, avg_ms, MAX_AVG_MS])
        root.remove_child(main)
        main.free()
        sc.set("current_bundle", null)


## No funding top-up, half the available crews per trade: sensible staffing must not go bankrupt
## within 40 weeks on healthcare_standard. Prints the weekly cash trajectory (first 12 weeks) so a
## failure can be diagnosed from the log.
func test_healthcare_sensible_crews_survive_without_funding() -> void:
    var path: String = "res://scenarios/healthcare_standard/sequence.json"
    if not FileAccess.file_exists(path):
        return
    var b := SequenceBundle.load_from_path(path)
    ok(b.valid, "healthcare_standard valid")
    var gs := SimState.new()
    gs.start(b)
    _lay_out_site(gs)
    _install_planner(gs, 0.5)
    var traj: PackedStringArray = []
    var cash0: float = gs.cash
    for w in WEEKS:
        if gs.finished:
            break
        if not gs.pending_event.is_empty():
            gs.resolve_event(0)
        _order_long_lead(gs)
        gs.advance_week()
        if w < 12:
            traj.append("%d:%d" % [w + 1, int(gs.cash / 1000.0)])
    var frac: float = float(gs.finished_task_count()) / float(b.tasks.size())
    print("      [scale-cash] healthcare_standard start cash %s, half crews, weeks=%d, finished %.0f%%, final cash %s, game_over=%s" % [
        Fmt.money(cash0), gs.week, frac * 100.0, Fmt.money(gs.cash), str(gs.finished and not gs.won)])
    print("      [scale-cash] cash (k$) by week for the first 12 weeks: %s" % " ".join(traj))
    ok(not (gs.finished and not gs.won), "no bankruptcy / game over by week %d (%s)" % [gs.week, str(gs.result.get("reason", ""))])
    gs.free()
