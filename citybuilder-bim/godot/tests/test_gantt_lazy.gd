extends TC
## The timeline at 600+ zones: rows are built lazily (only the rows that are drawn or hit-tested get bars), the model is
## cheap to rebuild, and the lazy rows carry the same bars as the eager model.

const MAX_MODEL_MS: float = 150.0


func _state(copies: int) -> SimState:
    var b := SequenceBundle.from_dictionary(StressGen.zone_heavy(copies))
    ok(b.valid, "zone-heavy bundle valid: %s" % ", ".join(b.errors))
    var gs := SimState.new()
    ok(gs.start(b), "starts")
    return gs


func _zone_rows(m: Dictionary) -> Array:
    var out: Array = []
    for r in m["rows"]:
        if r["kind"] == "zone":
            out.append(r)
    return out


func test_600_zone_model_is_lazy_and_cheap() -> void:
    var gs: SimState = _state(300)
    eq(gs.bundle.zones.size(), 600, "600 zones")
    var best: float = INF
    var m: Dictionary = {}
    for k in 5:
        var t0: int = Time.get_ticks_usec()
        m = GanttModel.build(gs)
        best = minf(best, float(Time.get_ticks_usec() - t0) / 1000.0)
    print("      [gantt-lazy] 600 zones, %d packages: lazy model %.1f ms (best of 5)" % [gs.bundle.packages.size(), best])
    ok(bool(m.get("lazy", false)), "above the zone threshold the model is lazy")
    eq(_zone_rows(m).size(), 600, "a row per zone")
    ok(best < MAX_MODEL_MS, "model build %.1f ms < %.0f ms" % [best, MAX_MODEL_MS])
    var built: int = 0
    for r in _zone_rows(m):
        if bool(r.get("built", false)):
            built += 1
        ok(int(r["lanes"]) >= 1, "every row has a lane count for the layout")
    eq(built, 0, "no row has bars yet")
    gs.free()


func test_only_visible_rows_are_built_and_drawn() -> void:
    var gs: SimState = _state(300)
    var r := GanttRenderer.new()
    r.gs = gs
    r.size = Vector2(1000, 260)
    r.set_model(GanttModel.build(gs))
    var shown: int = r.count_visible()
    ok(shown > 0, "bars are drawn (%d)" % shown)
    var built: int = r.built_row_count()
    ok(built > 0 and built <= 40, "only the rows in view were built (%d of 600)" % built)
    eq(r.detail_built, built, "every build was triggered by drawing")
    r.scroll_rows(4000.0)
    r.count_visible()
    var built2: int = r.built_row_count()
    ok(built2 > built and built2 <= 80, "scrolling builds the rows that come into view (%d)" % built2)
    # hit testing a lazy row builds it and finds its bar
    var found: bool = false
    for y in range(40, 240, 4):
        for x in range(r.label_w + 4.0, 990.0, 12.0):
            var bar: Dictionary = r.bar_at(Vector2(x, float(y)))
            if not bar.is_empty():
                found = true
                ok(bar.has("package_id") and bar.has("lane"), "the hit bar is a full bar")
                break
        if found:
            break
    ok(found, "a bar can be hit in the lazy rows")
    r.free()
    gs.free()


func test_lazy_rows_carry_the_bars_of_the_eager_model() -> void:
    var gs: SimState = _state(8)
    var eager: Dictionary = GanttModel.build(gs, {"lazy": false})
    var lazy: Dictionary = GanttModel.build(gs, {"lazy": true})
    eq(_zone_rows(lazy).size(), _zone_rows(eager).size(), "same rows")
    var fill: Callable = lazy["detail"]
    for i in _zone_rows(lazy).size():
        var a: Dictionary = _zone_rows(eager)[i]
        var b: Dictionary = _zone_rows(lazy)[i]
        eq(str(b["zone_id"]), str(a["zone_id"]), "zone order")
        fill.call(b)
        var ids_a: Array = []
        for bar in a["bars"]:
            ids_a.append(str(bar["package_id"]))
        var ids_b: Array = []
        for bar in b["bars"]:
            ids_b.append(str(bar["package_id"]))
        eq(ids_b, ids_a, "the bars of %s" % str(a["zone_id"]))
        ok(bool(b["built"]), "built")
        for bar in b["bars"]:
            ok(int(bar["lane"]) < int(b["lanes"]), "lanes fit the row height")
    eq(int(lazy["max_day"]) >= int(eager["contract_day"]), true, "max day covers the contract")


func test_lazy_filters_drop_empty_zones() -> void:
    var gs: SimState = _state(300)
    var all: Dictionary = GanttModel.build(gs)
    var none: Dictionary = GanttModel.build(gs, {"discipline": "no_such_discipline"})
    eq(_zone_rows(none).size(), 0, "a discipline nobody has leaves no rows")
    var st: Dictionary = GanttModel.build(gs, {"storey_id": "L01"})
    eq(_zone_rows(st).size(), 300, "storey filter keeps the zones of that storey")
    ok(_zone_rows(all).size() == 600, "unfiltered")
    gs.free()


func test_expanded_zone_still_lists_its_tasks_in_lazy_mode() -> void:
    var gs: SimState = _state(300)
    var m: Dictionary = GanttModel.build(gs, {"expanded": {"K000-L00-Z1": true}})
    var task_rows: int = 0
    for r in m["rows"]:
        if r["kind"] == "task":
            task_rows += 1
    ok(task_rows > 0, "task rows below the expanded zone (%d)" % task_rows)
    gs.free()
