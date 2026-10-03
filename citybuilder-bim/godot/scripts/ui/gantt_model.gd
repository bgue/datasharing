class_name GanttModel
extends RefCounted
## Builds the row / bar model the Gantt renderer draws (docs/05 section 5). Package bars come from
## ApiViews.gantt (the same data as the `state.gantt` API view); this adds grouping by storey,
## filters, lane packing (overlapping bars of a zone go to separate lanes), station spans, delivery
## and incident markers, and task rows for expanded zones.
##
## Model: {"rows": Array[Dictionary], "current_day", "week", "max_day", "bar_count", "contract_day"}
## Row kinds:
##   {"kind": "group", "storey_id", "name", "zone_count", "bar_count"}
##   {"kind": "zone", "zone_id", "name", "storey_id", "bars", "lanes", "stations", "deliveries", "incidents",
##    "card_id", "expanded"}
##   {"kind": "task", "zone_id", "task_id", "name", "bars": [bar]}   (only below an expanded zone)
## Bars carry every key of ApiViews.gantt bars plus: station (String), released, priority, lane,
## span_start / span_end (the drawn extent: actual start .. actual finish or now, else the plan).

const DAYS_PER_WEEK: int = 5
## Models with more zones or packages than this build their rows lazily (`lazy`): the rows carry only what the layout needs
## (zone, storey, lane count, stations); bars, lanes and markers are filled by `fill_row` for the rows that are drawn.
const LAZY_ZONES: int = 150
const LAZY_PACKAGES: int = 1500


## opts: storey_id (String, "" = all), discipline ("" = all), mine (bool), zone_ids (Array, [] = all),
## expanded (Dictionary zone_id -> true), group (bool, default true: storey header rows), lazy (bool, default: models
## above LAZY_ZONES zones or LAZY_PACKAGES packages), area_id (String, "" = all: only the zones of the area).
static func build(gs: SimState, opts: Dictionary = {}) -> Dictionary:
    var storey_filter: String = str(opts.get("storey_id", ""))
    var disc_filter: String = str(opts.get("discipline", ""))
    var mine: bool = bool(opts.get("mine", false))
    var only_zones: Dictionary = {}
    for z in opts.get("zone_ids", []):
        only_zones[str(z)] = true
    var area_id: String = str(opts.get("area_id", ""))
    if area_id != "":
        var area: Dictionary = gs.bundle.areas_by_id.get(area_id, {}) if gs != null and gs.bundle != null else {}
        var members: Dictionary = {}
        for zid in area.get("zone_ids", []):
            if only_zones.is_empty() or only_zones.has(str(zid)):
                members[str(zid)] = true
        only_zones = members if not members.is_empty() else {"": true}  # an unknown / empty area shows nothing
    var expanded: Dictionary = opts.get("expanded", {})
    var grouped: bool = bool(opts.get("group", true))
    var filtered: bool = disc_filter != "" or mine
    var empty := {"rows": [], "current_day": 0, "week": 0, "max_day": 0, "bar_count": 0, "contract_day": 0}
    if gs == null or gs.bundle == null:
        return empty
    var zones: Array[ZoneData] = []
    for z in gs.bundle.zones:
        if storey_filter != "" and z.storey_id != storey_filter:
            continue
        if not only_zones.is_empty() and not only_zones.has(z.id):
            continue
        zones.append(z)
    empty["current_day"] = gs.current_day()
    empty["week"] = gs.week
    empty["contract_day"] = gs.bundle.contract_weeks() * DAYS_PER_WEEK
    if zones.is_empty():
        return empty
    var lazy: bool = bool(opts.get("lazy", zones.size() > LAZY_ZONES or gs.bundle.packages.size() > LAZY_PACKAGES))
    if lazy:
        return _build_lazy(gs, zones, storey_filter, disc_filter, mine, expanded, grouped)
    var narrow: bool = storey_filter != "" or not only_zones.is_empty()
    var zone_arg: Array = []
    if narrow:
        for z in zones:
            zone_arg.append(z.id)
    var view: Dictionary = ApiViews.gantt(gs, zone_arg)
    var cur_day: int = int(view["current_day"])

    var bars_by_zone: Dictionary = {}
    var stations_by_zone: Dictionary = {}
    for s in view["stations"]:
        stations_by_zone[str(s["zone_id"])] = s
    var mine_cache: Dictionary = {}  # "zone|trade" -> bool
    var bar_count: int = 0
    var max_day: int = gs.bundle.contract_weeks() * DAYS_PER_WEEK
    for b in view["bars"]:
        var bar: Dictionary = b
        if disc_filter != "" and str(bar["discipline"]) != disc_filter:
            continue
        var zid: String = str(bar["zone_id"])
        if mine:
            var key: String = zid + "|" + str(bar["trade"])
            if not mine_cache.has(key):
                mine_cache[key] = _has_crew(gs, zid, str(bar["trade"]))
            if not bool(mine_cache[key]):
                continue
        _enrich(gs, bar, stations_by_zone.get(zid, null), cur_day)
        if not bars_by_zone.has(zid):
            bars_by_zone[zid] = []
        (bars_by_zone[zid] as Array).append(bar)
        bar_count += 1
        max_day = maxi(max_day, int(bar["span_end"]))

    var deliveries_by_zone: Dictionary = {}
    for d in view["deliveries"]:
        var zid2: String = str(d["zone_id"])
        if not deliveries_by_zone.has(zid2):
            deliveries_by_zone[zid2] = []
        (deliveries_by_zone[zid2] as Array).append(int(d["week"]) * DAYS_PER_WEEK)
    var incidents_by_zone: Dictionary = {}
    for inc in view["incidents"]:
        var zid3: String = str(inc["zone_id"])
        if not incidents_by_zone.has(zid3):
            incidents_by_zone[zid3] = []
        (incidents_by_zone[zid3] as Array).append(int(inc["week"]) * DAYS_PER_WEEK + 2)

    var zones_by_storey: Dictionary = {}
    for z in zones:
        if not zones_by_storey.has(z.storey_id):
            zones_by_storey[z.storey_id] = [] as Array[ZoneData]
        (zones_by_storey[z.storey_id] as Array).append(z)
    var rows: Array[Dictionary] = []
    var storey_order: Array[String] = []
    for st in gs.bundle.storeys:
        storey_order.append(st.id)
    for sid in zones_by_storey:
        if not storey_order.has(str(sid)):
            storey_order.append(str(sid))
    for sid in storey_order:
        if not zones_by_storey.has(sid):
            continue
        var zone_rows: Array[Dictionary] = []
        var storey_bars: int = 0
        for z in zones_by_storey[sid]:
            var zd: ZoneData = z
            var zbars: Array = bars_by_zone.get(zd.id, [])
            if filtered and zbars.is_empty():
                continue
            storey_bars += zbars.size()
            var zr: ZoneRuntime = gs.zone_runtime[zd.id]
            var is_exp: bool = bool(expanded.get(zd.id, false))
            zone_rows.append({
                "kind": "zone", "zone_id": zd.id, "name": zd.name, "storey_id": zd.storey_id,
                "bars": zbars, "lanes": pack_lanes(zbars),
                "stations": station_spans(stations_by_zone.get(zd.id, null)),
                "deliveries": deliveries_by_zone.get(zd.id, []), "incidents": incidents_by_zone.get(zd.id, []),
                "card_id": zr.card_id, "behind_takt": zr.behind_takt, "expanded": is_exp,
            })
            if is_exp:
                zone_rows.append_array(task_rows(gs, zd.id, cur_day))
        if zone_rows.is_empty():
            continue
        if grouped:
            var sd: StoreyData = gs.bundle.storeys_by_id.get(sid, null)
            var zc: int = 0
            for r in zone_rows:
                if r["kind"] == "zone":
                    zc += 1
            rows.append({"kind": "group", "storey_id": sid, "name": sd.name if sd != null else str(sid),
                    "zone_count": zc, "bar_count": storey_bars})
        rows.append_array(zone_rows)
    return {"rows": rows, "current_day": cur_day, "week": gs.week, "max_day": max_day, "bar_count": bar_count,
            "contract_day": gs.bundle.contract_weeks() * DAYS_PER_WEEK}


static func _has_crew(gs: SimState, zone_id: String, trade: String) -> bool:
    for c in gs.crews:
        if str(c["zone_id"]) == zone_id and str(c["trade"]) == trade:
            return true
    return false


static func _enrich(gs: SimState, bar: Dictionary, st: Variant, cur_day: int) -> void:
    var rt: PackageRuntime = gs.package_runtime[str(bar["package_id"])]
    bar["released"] = rt.released
    bar["priority"] = rt.priority
    bar["station"] = ""
    if st != null and int(bar["station_index"]) >= 0:
        var list: Array = (st as Dictionary)["stations"]
        var i: int = int(bar["station_index"])
        bar["station"] = str((list[i] as Dictionary)["name"]) if i < list.size() else "Other"
    var span: Vector2i = span_of(bar, cur_day)
    bar["span_start"] = span.x
    bar["span_end"] = span.y


## Drawn extent of a bar in days (end exclusive): the actual start (or planned start) to the actual
## finish, or to max(planned finish, now) while it runs, or the plan when it has not started.
static func span_of(bar: Dictionary, cur_day: int) -> Vector2i:
    var a_start: Variant = bar.get("actual_start_day", null)
    var a_finish: Variant = bar.get("actual_finish_day", null)
    var s: int = int(a_start) if a_start != null else int(bar["planned_start_day"])
    var e: int
    if a_finish != null:
        e = int(a_finish) + 1
    elif a_start != null:
        e = maxi(int(bar["planned_finish_day"]), cur_day)
    else:
        e = int(bar["planned_finish_day"])
    return Vector2i(s, maxi(e, s + 1))


## Assigns bar["lane"] greedily by start day so overlapping bars of a zone do not overdraw; returns the lane count.
static func pack_lanes(bars: Array) -> int:
    var order: Array = bars.duplicate()
    order.sort_custom(func(a: Dictionary, b: Dictionary) -> bool:
        var sa: int = mini(int(a["planned_start_day"]), int(a["span_start"]))
        var sb: int = mini(int(b["planned_start_day"]), int(b["span_start"]))
        if sa != sb:
            return sa < sb
        return str(a.get("package_id", a.get("task_id", ""))) < str(b.get("package_id", b.get("task_id", ""))))
    var ends: Array[int] = []
    for b in order:
        var bar: Dictionary = b
        var s: int = mini(int(bar["planned_start_day"]), int(bar["span_start"]))
        var e: int = maxi(int(bar["planned_finish_day"]), int(bar["span_end"]))
        var lane: int = -1
        for i in ends.size():
            if ends[i] <= s:
                lane = i
                break
        if lane < 0:
            ends.append(e)
            lane = ends.size() - 1
        else:
            ends[lane] = e
        bar["lane"] = lane
    return maxi(ends.size(), 1)


## Planned week spans per card station, anchored at the current station's start week:
## [{index, name, start_day, end_day, current}] (end exclusive).
static func station_spans(st: Variant) -> Array:
    var out: Array = []
    if st == null:
        return out
    var d: Dictionary = st
    var list: Array = d["stations"]
    var cur: int = int(d["current"])
    var anchor: int = int(d["station_start_week"])
    var n: int = list.size()
    var starts: Array[int] = []
    starts.resize(n)
    var c: int = clampi(cur, 0, maxi(n - 1, 0))
    if n == 0:
        return out
    var t: int = anchor
    if cur >= n:
        # card finished / trailing station: the whole card lies before the anchor
        t = anchor - int((list[n - 1] as Dictionary)["takt_weeks"])
        c = n - 1
        starts[c] = t
    else:
        starts[c] = anchor
    for i in range(c - 1, -1, -1):
        starts[i] = starts[i + 1] - int((list[i] as Dictionary)["takt_weeks"])
    for i in range(c + 1, n):
        starts[i] = starts[i - 1] + int((list[i - 1] as Dictionary)["takt_weeks"])
    for i in n:
        var sw: int = starts[i]
        var tw: int = maxi(int((list[i] as Dictionary)["takt_weeks"]), 1)
        out.append({"index": i, "name": str((list[i] as Dictionary)["name"]), "start_day": sw * DAYS_PER_WEEK,
                "end_day": (sw + tw) * DAYS_PER_WEEK, "current": i == cur, "behind_takt": bool(d["behind_takt"]) and i == cur})
    return out


## One row per task of a zone (same bar shape, `task_id` set), for the expanded zone view.
static func task_rows(gs: SimState, zone_id: String, cur_day: int) -> Array[Dictionary]:
    var out: Array[Dictionary] = []
    gs.ensure_zone_detail(zone_id)
    var tasks: Array = (gs.bundle.tasks_by_zone.get(zone_id, []) as Array).duplicate()
    tasks.sort_custom(func(a: TaskData, b: TaskData) -> bool:
        if a.planned_start_day != b.planned_start_day:
            return a.planned_start_day < b.planned_start_day
        return a.task_id < b.task_id)
    for t in tasks:
        var task: TaskData = t
        var rt: TaskRuntime = gs.runtime[task.task_id]
        var prt: PackageRuntime = gs.package_runtime.get(task.package_id, null)
        var pkg: PackageData = gs.bundle.packages_by_id.get(task.package_id, null)
        var step: StepDef = gs.bundle.step_of(task)
        var zr: ZoneRuntime = gs.zone_runtime[zone_id]
        var finished: bool = TaskRuntime.is_finished(rt.state)
        var bar: Dictionary = {
            "task_id": task.task_id, "package_id": task.package_id, "zone_id": zone_id, "storey_id": task.storey_id,
            "name": "%s - %s" % [step.name if step != null else task.step_id, task.element_name],
            "phase": task.phase, "trade": task.trade, "work_face": task.work_face,
            "planned_start_day": task.planned_start_day, "planned_finish_day": task.planned_finish_day,
            "actual_start_day": rt.actual_start_day if rt.actual_start_day >= 0 else null,
            "actual_finish_day": rt.actual_finish_day if (finished and rt.actual_finish_day >= 0) else null,
            "progress": 1.0 if finished else clampf(rt.progress / maxf(rt.required, 0.0001), 0.0, 1.0),
            "state": TaskRuntime.state_name(rt.state).to_lower(),
            "discipline": step.discipline if step != null else "general",
            "held": prt != null and prt.state == "held", "understaffed": false,
            "behind_takt": prt != null and zr.behind_takt and prt.station_index == zr.station_index,
            "crews_now": prt.crews_now if prt != null else 0,
            "crew_min": pkg.crew_min if pkg != null else 1, "crew_ideal": pkg.crew_ideal if pkg != null else 1,
            "crew_max": pkg.crew_max if pkg != null else 1,
            "remaining_crew_days": maxf(rt.required - rt.progress, 0.0) if not finished else 0.0,
            "blocked_reason": rt.blocked_reason, "station_index": prt.station_index if prt != null else -1,
            "station": "", "released": prt == null or prt.released, "priority": prt.priority if prt != null else 0,
            "is_task": true, "lane": 0,
        }
        var span: Vector2i = span_of(bar, cur_day)
        bar["span_start"] = span.x
        bar["span_end"] = span.y
        out.append({"kind": "task", "zone_id": zone_id, "task_id": task.task_id, "name": str(bar["name"]), "bars": [bar]})
    return out


# ------------------------------------------------------------------ lazy model (big projects)

## Rows without bars: the model of a 600-zone project is a few milliseconds instead of a second. A zone row keeps its
## lane count (packed from the planned spans once per zone, cached in the SimState) so the layout is stable; `fill_row`
## adds the bars, actual lanes and markers of the rows the renderer draws. model["detail"] is the Callable for it.
static func _build_lazy(gs: SimState, zones: Array[ZoneData], storey_filter: String, disc_filter: String, mine: bool,
        expanded: Dictionary, grouped: bool) -> Dictionary:
    var cur_day: int = gs.current_day()
    var ctx: Dictionary = {"gs": gs, "cur_day": cur_day, "discipline": disc_filter, "mine": mine,
            "deliveries": {}, "incidents": {}, "stations": {}}
    for d in ApiViews.gantt_deliveries(gs):
        var zd: String = str(d["zone_id"])
        if not (ctx["deliveries"] as Dictionary).has(zd):
            (ctx["deliveries"] as Dictionary)[zd] = []
        ((ctx["deliveries"] as Dictionary)[zd] as Array).append(int(d["week"]) * DAYS_PER_WEEK)
    for inc in gs.incident_log:
        var zi: String = str(inc["zone_id"])
        if not (ctx["incidents"] as Dictionary).has(zi):
            (ctx["incidents"] as Dictionary)[zi] = []
        ((ctx["incidents"] as Dictionary)[zi] as Array).append(int(inc["week"]) * DAYS_PER_WEEK + 2)
    for st in ApiViews.gantt_stations(gs):
        (ctx["stations"] as Dictionary)[str(st["zone_id"])] = st
    var zones_by_storey: Dictionary = {}
    for z in zones:
        if not zones_by_storey.has(z.storey_id):
            zones_by_storey[z.storey_id] = [] as Array[ZoneData]
        (zones_by_storey[z.storey_id] as Array).append(z)
    var storey_order: Array[String] = []
    for st in gs.bundle.storeys:
        storey_order.append(st.id)
    for sid in zones_by_storey:
        if not storey_order.has(str(sid)):
            storey_order.append(str(sid))
    var filtered: bool = disc_filter != "" or mine
    var max_day: int = gs.bundle.contract_weeks() * DAYS_PER_WEEK
    var rows: Array[Dictionary] = []
    var bar_count: int = 0
    for sid in storey_order:
        if not zones_by_storey.has(sid):
            continue
        var zone_rows: Array[Dictionary] = []
        var storey_bars: int = 0
        for z in zones_by_storey[sid]:
            var zd2: ZoneData = z
            var pkgs: Array = gs.bundle.packages_by_zone.get(zd2.id, [])
            var n_match: int = 0
            for p in pkgs:
                var pk: PackageData = p
                max_day = maxi(max_day, pk.planned_finish_day)
                if disc_filter != "" and pk.discipline != disc_filter:
                    continue
                if mine and not _has_crew(gs, zd2.id, pk.trade):
                    continue
                n_match += 1
            if filtered and n_match == 0:
                continue
            storey_bars += n_match
            var zr: ZoneRuntime = gs.zone_runtime[zd2.id]
            var is_exp: bool = bool(expanded.get(zd2.id, false))
            var st_info: Variant = (ctx["stations"] as Dictionary).get(zd2.id, null)
            zone_rows.append({
                "kind": "zone", "zone_id": zd2.id, "name": zd2.name, "storey_id": zd2.storey_id,
                "bars": [], "lanes": _planned_lanes(gs, zd2.id),
                "stations": station_spans(st_info),
                "deliveries": [], "incidents": [],
                "card_id": zr.card_id, "behind_takt": zr.behind_takt, "expanded": is_exp,
                "lazy": true, "built": false,
            })
            if is_exp:
                zone_rows.append_array(task_rows(gs, zd2.id, cur_day))
        if zone_rows.is_empty():
            continue
        bar_count += storey_bars
        if grouped:
            var sd: StoreyData = gs.bundle.storeys_by_id.get(sid, null)
            var zc: int = 0
            for r in zone_rows:
                if r["kind"] == "zone":
                    zc += 1
            rows.append({"kind": "group", "storey_id": sid, "name": sd.name if sd != null else str(sid),
                    "zone_count": zc, "bar_count": storey_bars})
        rows.append_array(zone_rows)
    return {"rows": rows, "current_day": cur_day, "week": gs.week, "max_day": max_day, "bar_count": bar_count,
            "contract_day": gs.bundle.contract_weeks() * DAYS_PER_WEEK, "lazy": true,
            "detail": Callable(GanttModel, "fill_row").bind(ctx)}


## Lane count of a zone from the planned spans of its packages (cached in gs.gantt_lanes until the task graph changes).
static func _planned_lanes(gs: SimState, zone_id: String) -> int:
    var c: Variant = gs.gantt_lanes.get(zone_id, null)
    if c != null:
        return int(c)
    var bars: Array = []
    for p in gs.bundle.packages_by_zone.get(zone_id, []):
        var pk: PackageData = p
        bars.append({"package_id": pk.package_id, "planned_start_day": pk.planned_start_day,
                "planned_finish_day": pk.planned_finish_day, "span_start": pk.planned_start_day,
                "span_end": maxi(pk.planned_finish_day, pk.planned_start_day + 1)})
    var lanes: int = pack_lanes(bars)
    gs.gantt_lanes[zone_id] = lanes
    return lanes


## Fills a lazy zone row (bars, lanes, markers); a no-op for rows that are built or not lazy. `ctx` is bound by the model.
static func fill_row(row: Dictionary, ctx: Dictionary) -> void:
    if not bool(row.get("lazy", false)) or bool(row.get("built", false)):
        return
    var gs: SimState = ctx["gs"]
    var cur_day: int = int(ctx["cur_day"])
    var disc_filter: String = str(ctx["discipline"])
    var mine: bool = bool(ctx["mine"])
    var zid: String = str(row["zone_id"])
    var bars: Array = []
    var st_info: Variant = (ctx["stations"] as Dictionary).get(zid, null)
    for p in gs.bundle.packages_by_zone.get(zid, []):
        var pk: PackageData = p
        if disc_filter != "" and pk.discipline != disc_filter:
            continue
        if mine and not _has_crew(gs, zid, pk.trade):
            continue
        var bar: Dictionary = ApiViews.gantt_bar(gs, pk)
        if bar.is_empty():
            continue
        _enrich(gs, bar, st_info, cur_day)
        bars.append(bar)
    var lanes: int = int(row["lanes"])
    pack_lanes(bars)
    for b in bars:
        (b as Dictionary)["lane"] = mini(int((b as Dictionary)["lane"]), lanes - 1)  # the planned lane count fixed the row height
    row["bars"] = bars
    row["deliveries"] = (ctx["deliveries"] as Dictionary).get(zid, [])
    row["incidents"] = (ctx["incidents"] as Dictionary).get(zid, [])
    row["built"] = true
