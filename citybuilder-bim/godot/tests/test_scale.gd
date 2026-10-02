extends TC
## Scale smoke test: every bundle under res://scenarios is played for 40 weeks with a simple
## scripted planner. Checks performance (average advance_week time), progress and snapshot sanity.

const WEEKS: int = 40
const MAX_AVG_MS: float = 250.0
const MIN_FINISHED_FRACTION: float = 0.20
## Per-bundle target for the funded max-crews run (everything else needs MIN_FINISHED_FRACTION).
## healthcare_standard is limited by its crew profiles (concrete cap 3 against packages that need 2 to 3
## crews each), see test_healthcare_with_single_crew_minimums_holds_throughput for the sim's own capacity.
const TARGET_FRACTION: Dictionary = {"healthcare_standard": 0.35, "civil_standard": 0.70}
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


func _lay_out_site(gs: SimState) -> void:
    var res: Dictionary = Planner.auto_layout(gs, LAYDOWN_TILES)
    ok(int(res["laydown"]) >= 1, "%s: laydown placed" % gs.bundle.project_name)


## The daily planner of the autopilot (Planner.daily_plan), run before every working day.
## `fraction` limits the crews per trade; its time is excluded from the simulation timings.
func _install_planner(gs: SimState, fraction: float = 1.0) -> void:
    gs.before_work_day = func(_d: int) -> void:
        var t0: int = Time.get_ticks_usec()
        Planner.daily_plan(gs, "ideal", fraction, true)
        planner_us += Time.get_ticks_usec() - t0


func _order_long_lead(gs: SimState) -> void:
    Planner.order_all_due(gs, 60)


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


## The packages' minimum crews are what holds healthcare_standard back (a package below its minimum makes
## no progress). With single-crew minimums the same autopilot reaches the previous throughput.
func test_healthcare_with_single_crew_minimums_holds_throughput() -> void:
    var path: String = "res://scenarios/healthcare_standard/sequence.json"
    if not FileAccess.file_exists(path):
        return
    var b := SequenceBundle.load_from_path(path)
    for p in b.packages:
        p.crew_min = 1
        p.crew_ideal = maxi(p.crew_ideal, 1)
    var gs := SimState.new()
    gs.start(b)
    gs.cash += FUNDING_TOP_UP
    _lay_out_site(gs)
    _install_planner(gs)
    for w in WEEKS:
        if gs.finished:
            break
        if not gs.pending_event.is_empty():
            gs.resolve_event(0)
        _order_long_lead(gs)
        gs.advance_week()
    var frac: float = float(gs.finished_task_count()) / float(b.tasks.size())
    print("      [scale-min1] healthcare_standard with crew minimums of 1: %.0f%% finished at week %d" % [frac * 100.0, gs.week])
    ok(frac >= 0.70, "healthcare with single-crew minimums: %.1f%% >= 70%%" % (frac * 100.0))
    gs.free()
