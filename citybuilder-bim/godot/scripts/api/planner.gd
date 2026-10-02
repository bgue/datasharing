class_name Planner
extends RefCounted
## High-level planning helpers used by the control API (docs/05 section 6.2) and the UI:
## site auto layout, zone staffing, procurement, autopilot and analysis.

const LEVELS: Array[String] = ["min", "ideal", "max"]


# ----------------------------------------------------------------- site layout

## Shortest path of placeable cells from `starts` (the road network so far) to a cell next to the zone.
static func _route_to_zone(gs: SimState, starts: Array[Vector2i], zone: ZoneData) -> Array[Vector2i]:
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
            if gs.placement_error(n, SiteTiles.HAUL_ROAD) != "" and not (gs.tile_at(n) == SiteTiles.HAUL_ROAD):
                continue
            prev[n] = cur
            queue.append(n)
    return []


## Haul roads from the first gate to every zone (nearest first), `laydown` laydown tiles near the
## roads, and crane pads with cranes where tasks need them. Returns a summary dictionary.
static func auto_layout(gs: SimState, laydown: int = 4) -> Dictionary:
    var b: SequenceBundle = gs.bundle
    var summary: Dictionary = {"roads": 0, "laydown": 0, "cranes": 0, "zones_without_access": []}
    if b.scenario.gates.is_empty():
        gs.last_error = "scenario has no gates"
        return summary
    var gate: Vector2i = b.scenario.gates[0]
    var network: Array[Vector2i] = [gate]
    for c in gs.tiles:
        if SiteTiles.is_road(Logistics.tile_at(gs.tiles, c)) and not network.has(c):
            network.append(c)
    var zones: Array[ZoneData] = b.zones.duplicate()
    zones.sort_custom(func(x: ZoneData, y: ZoneData) -> bool:
        return Vector2(gate).distance_to(x.centroid()) < Vector2(gate).distance_to(y.centroid()))
    for z in zones:
        if gs.zone_access(z.id):
            continue
        for c in _route_to_zone(gs, network, z):
            if gs.tile_at(c) == SiteTiles.HAUL_ROAD:
                network.append(c)
            elif gs.place_tile(c, SiteTiles.HAUL_ROAD):
                network.append(c)
                summary["roads"] += 1
    var placed: int = 0
    for r in range(1, 6):
        for net in network:
            for d in Logistics.DIRS4:
                var c: Vector2i = net + d * r
                if placed < laydown and gs.tile_at(c) == "" and gs.place_tile(c, SiteTiles.LAYDOWN):
                    placed += 1
    summary["laydown"] = placed
    summary["cranes"] = _place_cranes(gs)
    for z in b.zones:
        if not gs.zone_access(z.id):
            (summary["zones_without_access"] as Array).append(z.id)
    return summary


## Each crane goes on the free cell covering the most still-uncovered crane tasks, up to max_count.
static func _place_cranes(gs: SimState) -> int:
    var b: SequenceBundle = gs.bundle
    var crane_cells: Array[Vector2i] = []
    for t in b.tasks:
        if t.requires_crane:
            for c in t.cells:
                if not crane_cells.has(c):
                    crane_cells.append(c)
    if crane_cells.is_empty():
        return 0
    var placed: int = 0
    for e in b.scenario.equipment:
        if not e.is_crane():
            continue
        for n in e.max_count:
            var best: Vector2i = Vector2i.ZERO
            var best_cover: int = 0
            for x in range(b.site_rect.position.x, b.site_rect.end.x):
                for y in range(b.site_rect.position.y, b.site_rect.end.y):
                    var cand := Vector2i(x, y)
                    if gs.tile_at(cand) != "" or gs.placement_error(cand, SiteTiles.CRANE_PAD) != "":
                        continue
                    var cover: int = 0
                    for c in crane_cells:
                        if Vector2(c - cand).length() <= float(e.reach_cells):
                            cover += 1
                    if cover > best_cover:
                        best_cover = cover
                        best = cand
            if best_cover == 0 or not gs.place_tile(best, SiteTiles.CRANE_PAD):
                break
            if not gs.place_equipment(e.id, best):
                break
            placed += 1
            var remaining: Array[Vector2i] = []
            for c in crane_cells:
                if Vector2(c - best).length() > float(e.reach_cells):
                    remaining.append(c)
            crane_cells = remaining
            if crane_cells.is_empty():
                return placed
    return placed


## Hires cranes only while crane work is at hand: a crane whose coverage holds no unfinished
## crane task that is workable now (or planned within two weeks) is demobilised, and one is placed
## again on an existing crane pad when such work is uncovered. Returns [removed, placed].
static func manage_cranes(gs: SimState) -> Array[int]:
    var removed: int = 0
    var placed: int = 0
    var soon: Array[TaskData] = []
    var horizon_day: int = (gs.week + 2) * 5
    for t in gs.bundle.tasks:
        if not t.requires_crane:
            continue
        var st: int = (gs.runtime[t.task_id] as TaskRuntime).state
        if TaskRuntime.is_finished(st):
            continue
        if st == TaskRuntime.State.READY or st == TaskRuntime.State.ACTIVE or st == TaskRuntime.State.REWORK \
                or t.planned_start_day <= horizon_day:
            soon.append(t)
    for i in range(gs.equipment_placed.size() - 1, -1, -1):
        var e: Dictionary = gs.equipment_placed[i]
        var def: EquipmentDef = gs.equipment_def(str(e["id"]))
        if def == null or not def.is_crane():
            continue
        var used: bool = false
        for t in soon:
            if Logistics.cranes_cover([{"cell": e["cell"], "reach": float(def.reach_cells)}], t.cells):
                used = true
                break
        if not used and gs.remove_equipment(i):
            removed += 1
    for _round in 4:  # each move strictly reduces the uncovered crane tasks, a few moves settle it
        var uncovered: bool = false
        for t in soon:
            if not Logistics.crane_covers(gs, t.cells):
                uncovered = true
                break
        if not uncovered:
            break
        var move: Dictionary = _best_crane_move(gs, soon)
        if move.is_empty():
            break
        if int(move["occupant"]) >= 0 and gs.remove_equipment(int(move["occupant"])):
            removed += 1
        if gs.place_equipment(str(move["def"]), move["pad"]):
            placed += 1
        else:
            break
    return [removed, placed]


## The crane placement that leaves the fewest `soon` crane tasks uncovered: a free crane pad with any crane type
## that still has stock, or a pad whose crane is swapped for another type (a pad holding a crane that does not
## reach the work blocks it: the crawler may sit on the wrong pad while the cheaper mobile crane occupies the right
## one). Ties go to the cheaper crane. Returns {pad, def, occupant (equipment index or -1)} or {} when nothing helps.
static func _best_crane_move(gs: SimState, soon: Array[TaskData]) -> Dictionary:
    var cranes: Array[Dictionary] = []
    for e in gs.equipment_placed:
        var d0: EquipmentDef = gs.equipment_def(str(e["id"]))
        if d0 != null and d0.is_crane():
            cranes.append({"cell": e["cell"], "reach": float(d0.reach_cells), "pad": e["cell"]})
    var base_unc: int = _uncovered_count(soon, cranes)
    var best: Dictionary = {}
    var best_unc: int = base_unc
    var best_cost: float = INF
    for pad in gs.tiles:
        if Logistics.tile_at(gs.tiles, pad) != SiteTiles.CRANE_PAD:
            continue
        var occ: int = -1
        for i in gs.equipment_placed.size():
            if gs.equipment_placed[i]["cell"] == pad:
                occ = i
        var occ_id: String = str(gs.equipment_placed[occ]["id"]) if occ >= 0 else ""
        for def in gs.scenario.equipment:
            if not def.is_crane() or def.id == occ_id:
                continue
            if gs.equipment_count(def.id) >= def.max_count:
                continue
            var trial: Array[Dictionary] = []
            for c in cranes:
                if c["pad"] != pad:
                    trial.append(c)
            trial.append({"cell": pad, "reach": float(def.reach_cells), "pad": pad})
            var unc: int = _uncovered_count(soon, trial)
            if unc < best_unc or (unc == best_unc and unc < base_unc and def.weekly_cost < best_cost):
                best_unc = unc
                best_cost = def.weekly_cost
                best = {"pad": pad, "def": def.id, "occupant": occ}
    return best


static func _uncovered_count(tasks: Array[TaskData], cranes: Array[Dictionary]) -> int:
    var n: int = 0
    for t in tasks:
        if not Logistics.cranes_cover(cranes, t.cells):
            n += 1
    return n


# ----------------------------------------------------------------- procurement

## Orders every long-lead task whose planned start minus lead time falls within the horizon.
## Returns the ordered task ids.
static func order_all_due(gs: SimState, horizon_weeks: int = 8) -> Array[String]:
    var ordered: Array[String] = []
    for t in gs.bundle.tasks:
        if t.lead_time_weeks <= 0:
            continue
        var rt: TaskRuntime = gs.runtime[t.task_id]
        if rt.ordered or TaskRuntime.is_finished(rt.state):
            continue
        var due_week: float = float(t.planned_start_day) / 5.0 - float(t.lead_time_weeks)
        if due_week <= float(gs.week + horizon_weeks):
            if gs.order(t.task_id):
                ordered.append(t.task_id)
    return ordered


# ----------------------------------------------------------------- staffing

static func level_need(p: PackageData, level: String) -> int:
    match level:
        "min":
            return p.crew_min
        "max":
            return p.crew_max
    return p.crew_ideal


## Crew-days of work a package can absorb right now: remaining effort of its READY (unimpeded),
## ACTIVE and REWORK tasks. Work that cannot start this week (gate, delivery, access) is not counted.
static func ready_crew_days(gs: SimState, p: PackageData) -> float:
    var rt: PackageRuntime = gs.package_runtime[p.package_id]
    var sum: float = 0.0
    for t in p.tasks:
        var trt: TaskRuntime = gs.runtime[t.task_id]
        if trt.state == TaskRuntime.State.ACTIVE or trt.state == TaskRuntime.State.REWORK \
                or (trt.state == TaskRuntime.State.READY and rt.released \
                and (trt.blocked_reason == "" or trt.blocked_reason.begins_with("No free laydown"))):
            sum += maxf(trt.required - trt.progress, 0.0)
    return sum


## Crews of a trade the zone needs at `level`: the level demand of the package the crews will work
## first (lowest priority value with ready work), but never more than the ready work can keep busy
## for a week (and never below the package minimum), capped by the zone's max_crews. Crews beyond that
## package's max spill to the next one, so the head package is what a crew count has to satisfy.
static func zone_trade_need(gs: SimState, zone_id: String, trade: String, level: String) -> int:
    var pkgs: Array[PackageData] = Packages.workable(gs, zone_id, trade)
    if pkgs.is_empty():
        return 0
    var zone: ZoneData = gs.bundle.zones_by_id[zone_id]
    var head: PackageData = pkgs[0]
    var want: int = level_need(head, level)
    if level != "min":
        var busy: int = ceili(ready_crew_days(gs, head) / 5.0)
        want = mini(want, maxi(busy, head.crew_min))
    return mini(want, zone.max_crews)


static func _crews_of(gs: SimState, trade: String, zone_id: String) -> Array[int]:
    var out: Array[int] = []
    for c in gs.crews:
        if str(c["trade"]) == trade and str(c["zone_id"]) == zone_id:
            out.append(int(c["id"]))
    return out


## A crew of `trade` that is idle: unassigned, or assigned to a zone with nothing for its trade.
static func _find_idle_crew(gs: SimState, trade: String, exclude_zone: String) -> int:
    for c in gs.crews:
        if str(c["trade"]) == trade and str(c["zone_id"]) == "":
            return int(c["id"])
    for c in gs.crews:
        var z: String = str(c["zone_id"])
        if str(c["trade"]) == trade and z != exclude_zone and not Packages.zone_trade_has_work(gs, z, trade):
            return int(c["id"])
    return -1


const IDLE_FIRE_DAYS: int = 5


## Fires crews that were idle for IDLE_FIRE_DAYS or more consecutive working days (optionally only in
## one zone; "" = all crews, including unassigned ones). Returns how many were fired.
static func fire_idle_crews(gs: SimState, zone_id: String = "", all_zones: bool = true, idle_days: int = IDLE_FIRE_DAYS) -> int:
    var n: int = 0
    for c in gs.crews.duplicate():
        if not all_zones and str(c["zone_id"]) != zone_id:
            continue
        if int(gs.crew_idle_days.get(int(c["id"]), 0)) >= idle_days:
            if gs.fire(int(c["id"])):
                n += 1
    return n


## Moves idle crews (hiring when `hire` and caps allow, and only when no idle crew of the trade exists)
## so each trade with workable packages in the zone has the crews that level calls for. With `hire` and
## `fire_idle`, crews of the zone that were idle for 5+ working days are fired first.
## Returns {moved, hired, fired, unmet}.
static func staff_zone(gs: SimState, zone_id: String, level: String = "ideal", hire: bool = false, fire_idle: bool = true) -> Dictionary:
    var res: Dictionary = {"moved": 0, "hired": 0, "fired": 0, "unmet": 0}
    if not gs.bundle.zones_by_id.has(zone_id):
        gs.last_error = "no such zone"
        return res
    if hire and fire_idle:
        res["fired"] = fire_idle_crews(gs, zone_id, false)
    for trade in gs.bundle.trade_ids():
        var need: int = zone_trade_need(gs, zone_id, trade, level)
        if need <= 0:
            continue
        var have: int = _crews_of(gs, trade, zone_id).size()
        while have < need:
            var cid: int = _find_idle_crew(gs, trade, zone_id)
            if cid < 0 and hire:
                cid = gs.hire(trade)
                if cid > 0:
                    res["hired"] += 1
            if cid < 0:
                res["unmet"] += need - have
                break
            gs.assign_crew(cid, zone_id)
            res["moved"] += 1
            have += 1
    return res


## Unassigns crews in the zone whose trade has nothing workable there.
static func release_idle_crews(gs: SimState, zone_id: String) -> int:
    var n: int = 0
    for c in gs.crews.duplicate():
        if str(c["zone_id"]) == zone_id and not Packages.zone_trade_has_work(gs, zone_id, str(c["trade"])):
            gs.assign_crew(int(c["id"]), "")
            n += 1
    return n


static func clear_crews(gs: SimState, zone_id: String) -> int:
    var n: int = 0
    for c in gs.crews:
        if str(c["zone_id"]) == zone_id:
            gs.assign_crew(int(c["id"]), "")
            n += 1
    return n


static func trade_has_work(gs: SimState, trade: String) -> bool:
    for z in gs.bundle.zones:
        if Packages.zone_trade_has_work(gs, z.id, trade):
            return true
    return false


## One planning pass (also run before every working day by the autopilot). Per trade:
## 1. deal the existing crews over the zones with workable packages of that trade: pass 1 gives each zone
##    the head package's minimum, pass 2 raises it to `level` (capped by the ready work), pass 3 adds
##    spare crews up to the package maximum; crews nobody needs are unassigned;
## 2. only if some zone still lacks crews and no surplus crew of the trade exists, hire (up to
##    `crew_fraction` of the available crews, at least one per trade, weekly hire cap) and deal again.
## With `hire` and `fire_idle`, crews idle for 5+ consecutive working days are fired first.
static func daily_plan(gs: SimState, level: String = "ideal", crew_fraction: float = 1.0, hire: bool = true, fire_idle: bool = true) -> void:
    # when cash is short the autopilot stops hiring and sheds crews that are not working
    var lean: bool = hire and gs.cash < -0.25 * gs.scenario.overdraft_limit
    if hire and fire_idle:
        fire_idle_crews(gs, "", true, 1 if lean else IDLE_FIRE_DAYS)
        manage_cranes(gs)
    for t in gs.bundle.trades:
        var trade: String = t.id
        var dealt: Dictionary = _deal_trade(gs, trade, level)
        # spare crews that could not fill a package minimum on their own count towards the shortfall
        var unmet: int = int(dealt["unmet"]) - int(dealt["spare"])
        if not hire or unmet <= 0 or (lean and gs.crew_count(trade) > 0):
            continue
        var cap: int = mini(maxi(1, int(floor(float(gs.crews_cap(trade)) * crew_fraction))), gs.crews_cap(trade))
        var hired: int = 0
        while hired < unmet and gs.crew_count(trade) < cap and gs.hires_left_this_week(trade) > 0:
            if gs.hire(trade) < 0:
                break
            hired += 1
        if hired > 0:
            _deal_trade(gs, trade, level)


## Deals the trade's crews over its work zones. Returns {unmet: crews still missing at `level`,
## spare: crews nobody could use (left unassigned)}.
static func _deal_trade(gs: SimState, trade: String, level: String) -> Dictionary:
    var crews: Array[int] = []
    for c in gs.crews:
        if str(c["trade"]) == trade:
            crews.append(int(c["id"]))
    var zones: Array[String] = []
    for z in gs.bundle.zones:
        if not Packages.workable(gs, z.id, trade).is_empty():
            zones.append(z.id)  # zone order: spreading crews over the site beats following the baseline order
    var target: Dictionary = {}  # zone -> crews
    var left: int = crews.size()
    var wanted: int = 0
    for z in zones:
        var m: int = zone_trade_need(gs, z, trade, "min")
        wanted += zone_trade_need(gs, z, trade, level)
        if m > 0 and left >= m:
            target[z] = m
            left -= m
    if level != "min":
        for z in zones:
            if left <= 0:
                break
            var want: int = zone_trade_need(gs, z, trade, level) - int(target.get(z, 0))
            var give: int = mini(maxi(want, 0), left)
            if give > 0 and (target.has(z) or give >= zone_trade_need(gs, z, trade, "min")):
                target[z] = int(target.get(z, 0)) + give
                left -= give
    # crews that cannot fill a package minimum on their own wait in the first zone that lacks crews, so a
    # partner can join them (they are not idle, and are not fired)
    if left > 0:
        for z in zones:
            if not target.has(z):
                target[z] = left
                left = 0
                break
    var assigned_total: int = crews.size() - left
    var unmet: int = maxi(wanted - assigned_total, 0)
    # hiring is conservative: only to give every zone with workable packages the package minimum
    # (the extra crews up to `level` come from existing crews only)
    var wanted_min: int = 0
    for z in zones:
        wanted_min += zone_trade_need(gs, z, trade, "min")
    unmet = mini(unmet, maxi(wanted_min - crews.size(), 0) + left)
    # spare crews: up to the head package's maximum
    for z in zones:
        if left <= 0:
            break
        if not target.has(z):
            continue
        var room: int = mini(zone_trade_need(gs, z, trade, "max"), gs.bundle.zones_by_id[z].max_crews) - int(target[z])
        var add: int = mini(maxi(room, 0), left)
        target[z] = int(target[z]) + add
        left -= add
    # deal: crews stay where the zone still needs them
    var assigned: Dictionary = {}
    var pool: Array[int] = []
    for cid in crews:
        var cz: String = str(gs.crew_by_id(cid)["zone_id"])
        if target.has(cz) and int(assigned.get(cz, 0)) < int(target[cz]):
            assigned[cz] = int(assigned.get(cz, 0)) + 1
        else:
            pool.append(cid)
    for z in target:
        while int(assigned.get(z, 0)) < int(target[z]) and not pool.is_empty():
            gs.assign_crew(pool.pop_front(), z)
            assigned[z] = int(assigned.get(z, 0)) + 1
    for cid in pool:
        if str(gs.crew_by_id(cid)["zone_id"]) != "":
            gs.assign_crew(cid, "")
    return {"unmet": unmet, "spare": pool.size()}


## Weekly loop: order due items, plan crews before each working day, advance. Stops early when an
## event with choices is pending. Returns a summary.
static func autopilot(gs: SimState, weeks: int, level: String = "ideal", crew_fraction: float = 1.0, hire: bool = true, horizon_weeks: int = 8, fire_idle: bool = true) -> Dictionary:
    var run_weeks: int = 0
    var prev_hook: Callable = gs.before_work_day
    gs.before_work_day = func(_d: int) -> void: daily_plan(gs, level, crew_fraction, hire, fire_idle)
    for w in weeks:
        if gs.finished or not gs.pending_event.is_empty():
            break
        order_all_due(gs, horizon_weeks)
        if not gs.advance_week():
            break
        run_weeks += 1
    gs.before_work_day = prev_hook
    return {"weeks_run": run_weeks, "finished": gs.finished, "won": gs.won,
            "pending_event": not gs.pending_event.is_empty()}


## Advances until a condition holds or an event with choices appears. `cond` may hold:
## week, package_done (id), cash_below, state_count ({state, at_least}). `max_weeks` bounds the loop.
static func run_until(gs: SimState, cond: Dictionary, max_weeks: int = 200) -> Dictionary:
    var reason: String = "max_weeks"
    for i in max_weeks:
        if _cond_met(gs, cond):
            reason = "condition"
            break
        if gs.finished:
            reason = "finished"
            break
        if not gs.pending_event.is_empty():
            reason = "event"
            break
        if not gs.advance_week():
            reason = "blocked"
            break
    if _cond_met(gs, cond) and reason == "max_weeks":
        reason = "condition"
    return {"stopped": reason, "week": gs.week}


static func _cond_met(gs: SimState, cond: Dictionary) -> bool:
    if cond.is_empty():
        return false
    if cond.has("week") and gs.week >= int(cond["week"]):
        return true
    if cond.has("package_done"):
        var p: PackageData = gs.bundle.packages_by_id.get(str(cond["package_done"]), null)
        if p != null and Packages.is_done(gs, p):
            return true
    if cond.has("cash_below") and gs.cash < float(cond["cash_below"]):
        return true
    if cond.has("state_count"):
        var sc: Dictionary = cond["state_count"]
        var counts: Dictionary = gs.state_counts()
        if int(counts.get(str(sc.get("state", "")), 0)) >= int(sc.get("at_least", 1)):
            return true
    return false


# ----------------------------------------------------------------- analysis

static func bottlenecks(gs: SimState) -> Dictionary:
    var zones_idle: Array[Dictionary] = []
    var understaffed: Array[Dictionary] = []
    var waiting: Array[Dictionary] = []
    for z in gs.bundle.zones:
        var crews_here: int = Productivity.zone_crew_count(gs, z.id)
        var has_ready: bool = false
        for p in gs.bundle.packages_by_zone.get(z.id, []):
            if Packages.has_work(gs, p):
                has_ready = true
                break
        if has_ready and crews_here == 0:
            zones_idle.append({"zone_id": z.id, "name": z.name})
    for p in gs.bundle.packages:
        var rt: PackageRuntime = gs.package_runtime[p.package_id]
        match rt.state:
            "understaffed":
                understaffed.append({"package_id": p.package_id, "name": p.name, "crews": rt.crews_now, "min": p.crew_min})
            "waiting":
                var r: String = rt.blocked_reason
                var kind: String = "predecessor"
                if r.begins_with("Gate"):
                    kind = "gate"
                elif r.contains("access"):
                    kind = "access"
                elif r.contains("crane"):
                    kind = "crane"
                elif r.contains("laydown"):
                    kind = "laydown"
                elif r.contains("Long-lead") or r.begins_with("Delivery"):
                    kind = "procurement"
                if kind != "predecessor":
                    waiting.append({"package_id": p.package_id, "name": p.name, "kind": kind, "reason": r})
            "ready":
                if rt.blocked_reason != "":
                    var r2: String = rt.blocked_reason
                    var k2: String = "access" if r2.contains("access") else ("crane" if r2.contains("crane") else ("laydown" if r2.contains("laydown") else "other"))
                    waiting.append({"package_id": p.package_id, "name": p.name, "kind": k2, "reason": r2})
    var late: Array[Dictionary] = []
    for t in gs.bundle.tasks:
        if t.lead_time_weeks > 0 and not TaskRuntime.is_finished(gs.runtime[t.task_id].state) and gs.order_is_late(t.task_id):
            var rt2: TaskRuntime = gs.runtime[t.task_id]
            late.append({"task_id": t.task_id, "ordered": rt2.ordered, "delivery_week": rt2.delivery_week,
                    "planned_start_week": float(t.planned_start_day) / 5.0})
    return {"zones_with_ready_work_and_no_crews": zones_idle, "understaffed_packages": understaffed,
            "waiting_packages": waiting, "late_orders": late}


## Unfinished packages on the baseline critical path with their slip against plan.
static func critical(gs: SimState, top: int = 20) -> Array[Dictionary]:
    var crit: Dictionary = {}
    for id in gs.bundle.critical_task_ids:
        crit[id] = true
    var rows: Array[Dictionary] = []
    for p in gs.bundle.packages:
        if Packages.is_done(gs, p):
            continue
        var on_path: bool = false
        for t in p.tasks:
            if crit.has(t.task_id):
                on_path = true
                break
        if not on_path:
            continue
        var rt: PackageRuntime = gs.package_runtime[p.package_id]
        var remaining: float = maxf(p.total_crew_days - rt.crew_days_done, 0.0)
        var slip: int = maxi(0, gs.week * 5 - p.planned_start_day) if rt.crew_days_done <= 0.0 else maxi(0, gs.week * 5 - p.planned_finish_day)
        rows.append({"package_id": p.package_id, "name": p.name, "state": rt.state, "planned_start_day": p.planned_start_day,
                "planned_finish_day": p.planned_finish_day, "remaining_crew_days": remaining, "slip_days": slip})
    rows.sort_custom(func(a: Dictionary, b: Dictionary) -> bool:
        if int(a["slip_days"]) != int(b["slip_days"]):
            return int(a["slip_days"]) > int(b["slip_days"])
        return int(a["planned_start_day"]) < int(b["planned_start_day"]))
    return rows.slice(0, top)


static func s_curve(gs: SimState) -> Dictionary:
    var planned: Array[float] = gs.bundle.baseline_weekly_planned_cost
    return {"planned": planned.duplicate(), "actual": gs.cumulative_spend_by_week.duplicate(), "week": gs.week}


## Projected weeks saved and cost added if the zone ran double shift from now on, with the crews
## it has today (or its ideal staffing if none).
static func what_if_shift(gs: SimState, zone_id: String) -> Dictionary:
    var zone: ZoneData = gs.bundle.zones_by_id.get(zone_id, null)
    if zone == null:
        gs.last_error = "no such zone"
        return {}
    var remaining: float = 0.0
    var wage_week: float = 0.0
    var crews_n: int = 0
    for p in gs.bundle.packages_by_zone.get(zone_id, []):
        var pk: PackageData = p
        if not Packages.is_done(gs, pk):
            remaining += maxf(pk.total_crew_days - (gs.package_runtime[pk.package_id] as PackageRuntime).crew_days_done, 0.0)
    for c in gs.crews:
        if str(c["zone_id"]) == zone_id:
            crews_n += 1
            var td: TradeDef = gs.bundle.trades_by_id.get(str(c["trade"]), null)
            wage_week += td.weekly_cost if td != null else 0.0
    if crews_n == 0:
        crews_n = maxi(1, zone.max_crews)
        wage_week = 0.0
    var f: float = gs.scenario.shift_productivity_factor
    var weeks_single: float = remaining / (float(crews_n) * 5.0)
    var weeks_double: float = weeks_single / maxf(f, 0.01)
    var added: float = wage_week * (gs.scenario.shift_cost_factor * weeks_double - weeks_single)
    var allowed: String = ""
    if not zone.shift_allowed:
        allowed = "zone does not allow double shift"
    for tag in gs.scenario.shift_forbidden_zone_tags:
        if zone.tags.has(tag):
            allowed = "zone is tagged %s" % tag
    return {"zone_id": zone_id, "remaining_crew_days": remaining, "crews": crews_n, "weeks_single": weeks_single,
            "weeks_double": weeks_double, "weeks_saved": weeks_single - weeks_double, "cost_added": added,
            "allowed": allowed == "", "refused_because": allowed}
