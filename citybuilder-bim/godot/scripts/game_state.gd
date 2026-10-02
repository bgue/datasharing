class_name SimState
extends Node
## The simulation. Registered as the `GameState` autoload (scripts are also
## instantiated directly by the headless tests via `SimState.new()`).
## Owns the SequenceBundle, task runtime states, week counter, cash, crews,
## equipment, site tile layer and score. See docs/03-architecture.md sections 3-5.

signal week_advanced(week: int)
signal task_state_changed(task_id: String, old_state: int, new_state: int)
signal event_fired(event: EventDef, choices: Array)
signal level_finished(result: Dictionary)
signal tiles_changed()
signal crews_changed()
signal cash_changed(cash: float)
signal incident_occurred(zone_id: String)
signal level_started()
signal focus_changed(storey_index: int)

## Visual state of a BIM element (see BimView).
enum Visual { GHOST, FRAMED, SOLID, INSPECTED, REWORK }

const WEEK_DAYS: int = 5
const MAX_WEEKS_FACTOR: int = 3

var bundle: SequenceBundle = null
var scenario: ScenarioData = null
var running: bool = false

var runtime: Dictionary = {}  # task_id -> TaskRuntime
var week: int = 0
var cash: float = 0.0
var speed: int = 0  # 0 = paused, 1, 2, 4
var tiles: Dictionary = {}  # Vector2i -> {"tile": String, "orientation": int}
var tiles_version: int = 0
var crews: Array[Dictionary] = []  # {"id": int, "trade": String, "zone_id": String ("" = unassigned)}
var _next_crew_id: int = 1
var equipment_placed: Array[Dictionary] = []  # {"id": String, "cell": Vector2i}
var hired_this_week: Dictionary = {}  # trade -> int
var rng: RandomNumberGenerator = RandomNumberGenerator.new()
var learning_counts: Dictionary = {}  # step_id -> completed repetitions
var modifiers: Array[Dictionary] = []
var zone_paused_until: Dictionary = {}  # zone_id -> week (exclusive)
var fired_events: Dictionary = {}  # event id -> times fired
var pending_event: Dictionary = {}  # {"event": EventDef, "zones": Array}
var incidents: int = 0
var goodwill: float = 0.0
var score_adjust: float = 0.0
var plan_changes: int = 0
var inspections_first_total: int = 0
var inspections_first_pass: int = 0
var inspection_failures_total: int = 0
var inspection_fail_override: float = -1.0  # tests: force the fail chance
var spent_total: float = 0.0
var overdraft_fees_total: float = 0.0
var payments_received: float = 0.0
var retention_held: float = 0.0
var negative_weeks: int = 0
var cumulative_spend_by_week: Array[float] = []
var crew_count_by_week: Array[int] = []
var crew_days_worked: float = 0.0
var crew_days_idle: float = 0.0
var crew_days_by_trade: Dictionary = {}  # trade -> [worked, idle] (assigned crews only)
var worked_this_week: Array[String] = []
var week_log: Array[String] = []
var last_report: Dictionary = {}
var finished: bool = false
var won: bool = false
var result: Dictionary = {}
var last_error: String = ""
var focus_storey_index: int = 0
## Day (0..4) being worked inside advance_week(); 0 otherwise.
var day_in_week: int = 0
## Tasks whose readiness must be re-evaluated at the next work day (successors of tasks that
## started or finished, tasks held by a gate that just opened).
var dirty_tasks: Array[String] = []
## Optional hook called with the day index before each work day (auto-planners, tests).
var before_work_day: Callable = Callable()

var element_visuals: Dictionary = {}  # guid -> Visual
## Gate open-counts from the last readiness refresh (conservative within a week).
var gate_cache: Dictionary = {}
var _access_cache: Dictionary = {}
var _access_cache_version: int = -1
var _footprint: Dictionary = {}


# ----------------------------------------------------------------- lifecycle

func start(b: SequenceBundle) -> bool:
    if b == null or not b.valid:
        last_error = "invalid bundle"
        return false
    bundle = b
    scenario = b.scenario
    running = true
    runtime.clear()
    for t in b.tasks:
        var rt := TaskRuntime.new()
        rt.required = t.estimated_crew_days
        runtime[t.task_id] = rt
    week = 0
    cash = scenario.start_cash
    speed = 0
    tiles.clear()
    tiles_version += 1
    crews.clear()
    _next_crew_id = 1
    equipment_placed.clear()
    hired_this_week.clear()
    learning_counts.clear()
    modifiers.clear()
    zone_paused_until.clear()
    fired_events.clear()
    pending_event = {}
    incidents = 0
    goodwill = 0.0
    score_adjust = 0.0
    plan_changes = 0
    inspections_first_total = 0
    inspections_first_pass = 0
    inspection_failures_total = 0
    inspection_fail_override = -1.0
    spent_total = 0.0
    overdraft_fees_total = 0.0
    payments_received = 0.0
    retention_held = 0.0
    negative_weeks = 0
    cumulative_spend_by_week.clear()
    crew_count_by_week.clear()
    crew_days_worked = 0.0
    crew_days_idle = 0.0
    crew_days_by_trade.clear()
    worked_this_week.clear()
    week_log.clear()
    last_report = {}
    finished = false
    won = false
    result = {}
    focus_storey_index = 0
    rng.seed = scenario.id.hash()
    _footprint = b.building_footprint_cells()
    element_visuals.clear()
    for e in b.elements:
        element_visuals[e.guid] = Visual.GHOST
    _load_initial_tiles()
    refresh_states()
    level_started.emit()
    return true


func _load_initial_tiles() -> void:
    for g in scenario.gates:
        tiles[g] = {"tile": SiteTiles.GATE, "orientation": 0}
    for it in scenario.initial_tiles:
        tiles[it["cell"]] = {"tile": str(it["tile"]), "orientation": int(it["orientation"])}
    tiles_version += 1


func current_day() -> int:
    return week * WEEK_DAYS + day_in_week


func equipment_def(id: String) -> EquipmentDef:
    for e in scenario.equipment:
        if e.id == id:
            return e
    return null


func log_event(msg: String) -> void:
    week_log.append(msg)


func spend(amount: float, _what: String = "") -> void:
    if amount <= 0.0:
        return
    cash -= amount
    spent_total += amount
    cash_changed.emit(cash)


func set_task_state(task_id: String, new_state: int) -> void:
    var rt: TaskRuntime = runtime[task_id]
    if rt.state == new_state:
        return
    var old_state: int = rt.state
    rt.state = new_state
    _update_element_visual((bundle.tasks_by_id[task_id] as TaskData).element_guid)
    if new_state == TaskRuntime.State.ACTIVE or TaskRuntime.is_finished(new_state):
        Readiness.note_changed(self, task_id, new_state)
    task_state_changed.emit(task_id, old_state, new_state)


func refresh_states() -> void:
    Readiness.refresh(self)


# ----------------------------------------------------------------- element visuals

func _update_element_visual(guid: String) -> void:
    var list: Array = bundle.tasks_by_element.get(guid, [])
    var any_rework: bool = false
    var any_active: bool = false
    var any_finished: bool = false
    var all_finished: bool = true
    var any_inspected: bool = false
    var best_visual: int = Visual.GHOST
    for t in list:
        var task: TaskData = t
        var rt: TaskRuntime = runtime[task.task_id]
        match rt.state:
            TaskRuntime.State.REWORK:
                any_rework = true
                all_finished = false
            TaskRuntime.State.ACTIVE:
                any_active = true
                all_finished = false
            TaskRuntime.State.AWAITING_INSPECTION:
                any_finished = true
                all_finished = false
                best_visual = maxi(best_visual, Visual.SOLID)
            TaskRuntime.State.DONE, TaskRuntime.State.INSPECTED:
                any_finished = true
                if rt.state == TaskRuntime.State.INSPECTED:
                    any_inspected = true
                var st: StepDef = bundle.step_of(task)
                var pv: String = st.progress_visual if st != null else "solid"
                match pv:
                    "none":
                        pass
                    "framed":
                        best_visual = maxi(best_visual, Visual.FRAMED)
                    _:
                        best_visual = maxi(best_visual, Visual.SOLID)
            _:
                all_finished = false
    var v: int = Visual.GHOST
    if any_rework:
        v = Visual.REWORK
    elif any_active and not any_finished:
        v = Visual.FRAMED
    elif any_active:
        v = maxi(Visual.FRAMED, best_visual)
    elif any_finished:
        v = maxi(Visual.FRAMED, best_visual)
        if all_finished and any_inspected:
            v = Visual.INSPECTED
    element_visuals[guid] = v


func element_visual(guid: String) -> int:
    return int(element_visuals.get(guid, Visual.GHOST))


# ----------------------------------------------------------------- crews

func crew_count(trade: String) -> int:
    var n: int = 0
    for c in crews:
        if str(c["trade"]) == trade:
            n += 1
    return n


func hires_left_this_week(trade: String) -> int:
    var td: TradeDef = bundle.trades_by_id.get(trade, null)
    if td == null:
        return 0
    return maxi(0, td.max_hire_per_week - int(hired_this_week.get(trade, 0)))


func crews_cap(trade: String) -> int:
    return int(scenario.crews_available.get(trade, 0))


## Returns the new crew id, or -1 (see last_error).
func hire(trade: String) -> int:
    if not running or finished:
        last_error = "level not running"
        return -1
    if not bundle.trades_by_id.has(trade):
        last_error = "unknown trade %s" % trade
        return -1
    if crew_count(trade) >= crews_cap(trade):
        last_error = "no %s crews available (cap %d)" % [trade, crews_cap(trade)]
        return -1
    if hires_left_this_week(trade) <= 0:
        last_error = "hiring limit reached for %s this week" % trade
        return -1
    var id: int = _next_crew_id
    _next_crew_id += 1
    crews.append({"id": id, "trade": trade, "zone_id": ""})
    hired_this_week[trade] = int(hired_this_week.get(trade, 0)) + 1
    crews_changed.emit()
    return id


func fire(crew_id: int) -> bool:
    for i in crews.size():
        if int(crews[i]["id"]) == crew_id:
            crews.remove_at(i)
            crews_changed.emit()
            return true
    last_error = "no such crew"
    return false


func crew_by_id(crew_id: int) -> Dictionary:
    for c in crews:
        if int(c["id"]) == crew_id:
            return c
    return {}


## Assigns a crew to a zone ("" unassigns).
func assign_crew(crew_id: int, zone_id: String) -> bool:
    var c: Dictionary = crew_by_id(crew_id)
    if c.is_empty():
        last_error = "no such crew"
        return false
    if zone_id != "" and not bundle.zones_by_id.has(zone_id):
        last_error = "no such zone"
        return false
    var old: String = str(c["zone_id"])
    if old != "" and old != zone_id:
        plan_changes += 1
    c["zone_id"] = zone_id
    crews_changed.emit()  # readiness does not depend on crews, so no refresh_states() here
    return true


func crews_in_zone(zone_id: String) -> Array[Dictionary]:
    var out: Array[Dictionary] = []
    for c in crews:
        if str(c["zone_id"]) == zone_id:
            out.append(c)
    return out


# ----------------------------------------------------------------- site tiles

## Returns "" if the tile can be placed on the cell, else the reason.
func placement_error(cell: Vector2i, tile: String) -> String:
    if not SiteTiles.PLAYER_TILES.has(tile):
        return "%s cannot be placed by the player" % tile
    if not bundle.in_site(cell):
        return "Outside the site"
    if scenario.occupied_cells.has(cell):
        return "Cell is occupied (live area)"
    if scenario.blocked_cells.has(cell):
        return "Cell is blocked"
    if _footprint.has(cell):
        return "Building footprint"
    if tiles.has(cell):
        var existing: String = str((tiles[cell] as Dictionary)["tile"])
        if SiteTiles.PERMANENT_TILES.has(existing) and not (existing == SiteTiles.GATE and tile == SiteTiles.HAUL_ROAD):
            return "Cell holds a permanent %s" % existing
    return ""


func place_tile(cell: Vector2i, tile: String, orientation: int = 0) -> bool:
    var err: String = placement_error(cell, tile)
    if err != "":
        last_error = err
        return false
    var existing: Dictionary = tiles.get(cell, {})
    var same: bool = not existing.is_empty() and str(existing["tile"]) == tile
    if same:
        if int(existing["orientation"]) == orientation:
            last_error = "already placed"
            return false
        existing["orientation"] = orientation
        tiles_changed.emit()
        return true
    var cost: float = scenario.tile_place_cost(tile)
    if cash - cost < -scenario.overdraft_limit:
        last_error = "Not enough cash"
        return false
    spend(cost, "place " + tile)
    tiles[cell] = {"tile": tile, "orientation": orientation}
    tiles_version += 1
    tiles_changed.emit()
    refresh_states()
    return true


func remove_tile(cell: Vector2i) -> bool:
    if not tiles.has(cell):
        last_error = "nothing to remove"
        return false
    var existing: String = str((tiles[cell] as Dictionary)["tile"])
    if SiteTiles.PERMANENT_TILES.has(existing):
        last_error = "permanent tile"
        return false
    if scenario.gates.has(cell):
        tiles[cell] = {"tile": SiteTiles.GATE, "orientation": 0}  # the entrance itself stays
    else:
        tiles.erase(cell)
    for i in range(equipment_placed.size() - 1, -1, -1):
        if equipment_placed[i]["cell"] == cell:
            equipment_placed.remove_at(i)
    tiles_version += 1
    tiles_changed.emit()
    refresh_states()
    return true


func tile_at(cell: Vector2i) -> String:
    return Logistics.tile_at(tiles, cell)


## Access of a zone: BFS from gates over haul road (cached per tile layer version).
func zone_access(zone_id: String) -> bool:
    if _access_cache_version != tiles_version:
        _access_cache.clear()
        _access_cache_version = tiles_version
    if not _access_cache.has(zone_id):
        var z: ZoneData = bundle.zones_by_id.get(zone_id, null)
        _access_cache[zone_id] = z != null and Logistics.bfs_access(tiles, scenario.gates, z.cells, bundle.zone_cell_set)
    return bool(_access_cache[zone_id])


# ----------------------------------------------------------------- equipment

func equipment_count(equipment_id: String) -> int:
    var n: int = 0
    for e in equipment_placed:
        if str(e["id"]) == equipment_id:
            n += 1
    return n


## Equipment is placed on a crane_pad tile; pays the mobilisation cost, then weekly hire.
func place_equipment(equipment_id: String, cell: Vector2i) -> bool:
    var def: EquipmentDef = equipment_def(equipment_id)
    if def == null:
        last_error = "unknown equipment"
        return false
    if tile_at(cell) != SiteTiles.CRANE_PAD:
        last_error = "Equipment needs a crane pad"
        return false
    for e in equipment_placed:
        if e["cell"] == cell:
            last_error = "pad already in use"
            return false
    if equipment_count(equipment_id) >= def.max_count:
        last_error = "equipment limit reached"
        return false
    spend(def.mobilisation_cost, "mobilise " + def.name)
    equipment_placed.append({"id": equipment_id, "cell": cell})
    tiles_changed.emit()
    refresh_states()
    return true


func remove_equipment(index: int) -> bool:
    if index < 0 or index >= equipment_placed.size():
        return false
    equipment_placed.remove_at(index)
    tiles_changed.emit()
    refresh_states()
    return true


# ----------------------------------------------------------------- procurement

## Orders a long-lead task's item. delivery_week = current week + lead time.
func order(task_id: String) -> bool:
    var task: TaskData = bundle.tasks_by_id.get(task_id, null)
    if task == null or task.lead_time_weeks <= 0:
        last_error = "not a long-lead task"
        return false
    var rt: TaskRuntime = runtime[task_id]
    if rt.ordered:
        last_error = "already ordered"
        return false
    rt.ordered = true
    rt.delivery_week = week + task.lead_time_weeks
    refresh_states()
    return true


## True if the planned start (in weeks) is earlier than the delivery week of an order placed now.
func order_is_late(task_id: String) -> bool:
    var task: TaskData = bundle.tasks_by_id.get(task_id, null)
    if task == null:
        return false
    var rt: TaskRuntime = runtime[task_id]
    var delivery: int = rt.delivery_week if rt.ordered else week + task.lead_time_weeks
    return float(task.planned_start_day) / float(WEEK_DAYS) < float(delivery)


func _land_deliveries() -> void:
    for t in bundle.tasks:
        var rt: TaskRuntime = runtime[t.task_id]
        if rt.ordered and rt.delivery_week == week:
            log_event("Delivery landed: %s" % Readiness.pred_label(self, t.task_id))


# ----------------------------------------------------------------- weekly loop

## Order: deliveries -> events -> release -> progress -> inspections -> economy -> safety -> score.
## Returns false if the week could not be advanced (finished, or event awaiting a choice).
func advance_week() -> bool:
    if not running or finished or not pending_event.is_empty():
        return false
    week_log.clear()
    worked_this_week.clear()
    hired_this_week.clear()
    var cash_before: float = cash
    var finished_before: int = finished_task_count()
    # 1 deliveries land
    _land_deliveries()
    # 2 events draw
    Events.draw(self)
    # 3 release pass (states current for the crews' work)
    refresh_states()
    # 4 + 5 progress and inspections at day resolution (5 working days)
    for d in WEEK_DAYS:
        run_work_day(d)
    day_in_week = 0
    # 6 economy
    Economy.run_week(self)
    # 7 safety
    Safety.run_week(self)
    # 8 score snapshot
    cumulative_spend_by_week.append(spent_total)
    crew_count_by_week.append(crews.size())
    var report: Dictionary = {
        "week": week,
        "cash": cash,
        "cash_change": cash - cash_before,
        "tasks_finished": finished_task_count() - finished_before,
        "worked_tasks": worked_this_week.size(),
        "log": week_log.duplicate(),
        "score": Scoring.compute(self, true),
    }
    week += 1
    _expire_modifiers()
    refresh_states()
    last_report = report
    week_advanced.emit(week)
    _check_finish()
    return true


## One working day (0..4 of the current week): incremental readiness, every assigned crew
## spends one crew-day, inspections due at the end of the day resolve.
func run_work_day(d: int) -> void:
    day_in_week = d
    if d > 0:
        Readiness.process_dirty(self, week * WEEK_DAYS + d)
    if before_work_day.is_valid():
        before_work_day.call(d)
    Productivity.run_day(self, d)
    Inspections.run_day(self, d)
    day_in_week = 0


func _expire_modifiers() -> void:
    for i in range(modifiers.size() - 1, -1, -1):
        if int(modifiers[i]["until_week"]) <= week:
            modifiers.remove_at(i)


func resolve_event(choice_index: int) -> void:
    Events.resolve(self, choice_index)
    refresh_states()


func finished_task_count() -> int:
    var n: int = 0
    for tid in runtime:
        if TaskRuntime.is_finished((runtime[tid] as TaskRuntime).state):
            n += 1
    return n


func all_tasks_finished() -> bool:
    return finished_task_count() == runtime.size()


func max_weeks() -> int:
    return maxi(52, bundle.contract_weeks() * MAX_WEEKS_FACTOR)


func _check_finish() -> void:
    if finished:
        return
    if all_tasks_finished():
        finish_level(true, "Handover achieved")
        return
    if negative_weeks >= Economy.OVERDRAFT_GAME_OVER_WEEKS:
        finish_level(false, "Bankrupt: cash below the overdraft limit for %d weeks" % negative_weeks)
    elif scenario.difficulty == "hard" and incidents >= scenario.max_incidents_hard:
        finish_level(false, "Too many recordable incidents")
    elif week >= max_weeks():
        finish_level(false, "Out of time")


func finish_level(win: bool, reason: String) -> void:
    if finished:
        return
    finished = true
    won = win
    speed = 0
    if win and retention_held > 0.0:
        cash += retention_held
        payments_received += retention_held
        log_event("Retention released: %s" % Fmt.money(retention_held))
        retention_held = 0.0
        cash_changed.emit(cash)
    result = Scoring.compute(self, win)
    result["reason"] = reason
    result["weeks"] = week
    result["contract_weeks"] = bundle.contract_weeks()
    result["spent"] = spent_total
    result["budget"] = bundle.contract_budget()
    result["incidents"] = incidents
    result["first_pass_rate"] = Scoring.quality_score(inspections_first_pass, inspections_first_total)
    result["plan_changes"] = plan_changes
    level_finished.emit(result)


# ----------------------------------------------------------------- queries

func zone_status(zone_id: String) -> Dictionary:
    ## Aggregated zone info for overlay/inspector: counts per state and a colour category.
    var counts: Dictionary = {}
    for s in TaskRuntime.STATE_NAMES:
        counts[s] = 0
    var impeded: int = 0
    var blocked_gate_proc: int = 0
    var total: int = 0
    for t in bundle.tasks_by_zone.get(zone_id, []):
        var task: TaskData = t
        var rt: TaskRuntime = runtime[task.task_id]
        counts[TaskRuntime.state_name(rt.state)] = int(counts[TaskRuntime.state_name(rt.state)]) + 1
        total += 1
        if rt.state == TaskRuntime.State.READY and rt.blocked_reason != "":
            impeded += 1
        if rt.state == TaskRuntime.State.BLOCKED and (rt.blocked_reason.begins_with("Gate") \
                or rt.blocked_reason.begins_with("Long-lead") or rt.blocked_reason.begins_with("Delivery")):
            blocked_gate_proc += 1
    var zone: ZoneData = bundle.zones_by_id[zone_id]
    var n_crews: int = Productivity.zone_crew_count(self, zone_id)
    var color_key: String = "idle"
    var active: int = int(counts["ACTIVE"]) + int(counts["REWORK"])
    var done: int = int(counts["DONE"]) + int(counts["INSPECTED"])
    var ready: int = int(counts["READY"])
    if total > 0 and done == total:
        color_key = "done"
    elif n_crews > zone.max_crews:
        color_key = "congested"
    elif impeded > 0 and active == 0 and n_crews > 0 or (blocked_gate_proc > 0 and active == 0 and ready == 0):
        color_key = "blocked"
    elif active > 0:
        color_key = "active"
    elif ready > 0 and n_crews == 0:
        color_key = "ready"
    elif impeded > 0:
        color_key = "blocked"
    return {"counts": counts, "crews": n_crews, "max_crews": zone.max_crews, "color": color_key,
            "total": total, "impeded": impeded}


## Phase completion per storey: Array of {storey_id, name, index, phases: [{id, name, done, total, started}]}.
func storey_phase_status() -> Array[Dictionary]:
    # one pass over the tasks: storey_id -> phase -> [total, done, started]
    var acc: Dictionary = {}
    for task in bundle.tasks:
        if not acc.has(task.storey_id):
            acc[task.storey_id] = {}
        var per_phase: Dictionary = acc[task.storey_id]
        if not per_phase.has(task.phase):
            per_phase[task.phase] = [0, 0, 0]
        var c: Array = per_phase[task.phase]
        c[0] += 1
        var st: int = (runtime[task.task_id] as TaskRuntime).state
        if TaskRuntime.is_finished(st):
            c[1] += 1
        elif st == TaskRuntime.State.ACTIVE or st == TaskRuntime.State.AWAITING_INSPECTION \
                or st == TaskRuntime.State.REWORK:
            c[2] += 1
    var out: Array[Dictionary] = []
    for s in bundle.storeys:
        var phases: Array[Dictionary] = []
        var per_phase2: Dictionary = acc.get(s.id, {})
        for p in bundle.phases:
            if per_phase2.has(p["id"]):
                var c2: Array = per_phase2[p["id"]]
                phases.append({"id": p["id"], "name": p["name"], "done": c2[1], "total": c2[0], "started": c2[2]})
        out.append({"storey_id": s.id, "name": s.name, "index": s.index, "phases": phases})
    return out


func state_counts() -> Dictionary:
    var counts: Dictionary = {}
    for s in TaskRuntime.STATE_NAMES:
        counts[s] = 0
    for tid in runtime:
        var n: String = TaskRuntime.state_name((runtime[tid] as TaskRuntime).state)
        counts[n] = int(counts[n]) + 1
    return counts


func planned_cumulative_cost(w: int) -> float:
    var arr: Array[float] = bundle.baseline_weekly_planned_cost
    if arr.is_empty():
        return 0.0
    return arr[mini(w, arr.size() - 1)]


## Plain-dictionary snapshot for UI and tests.
func snapshot() -> Dictionary:
    var crew_list: Array[Dictionary] = []
    for c in crews:
        crew_list.append(c.duplicate())
    var per_trade: Dictionary = {}
    for t in bundle.trades:
        per_trade[t.id] = {"count": crew_count(t.id), "cap": crews_cap(t.id),
                "hires_left": hires_left_this_week(t.id), "weekly_cost": t.weekly_cost}
    return {
        "scenario_id": scenario.id,
        "name": scenario.name,
        "week": week,
        "day": current_day(),
        "contract_weeks": bundle.contract_weeks(),
        "cash": cash,
        "budget": bundle.contract_budget(),
        "spent": spent_total,
        "payments": payments_received,
        "retention_held": retention_held,
        "speed": speed,
        "crews": crew_list,
        "trades": per_trade,
        "states": state_counts(),
        "tasks_total": runtime.size(),
        "tasks_finished": finished_task_count(),
        "incidents": incidents,
        "goodwill": goodwill,
        "negative_weeks": negative_weeks,
        "finished": finished,
        "won": won,
        "pending_event": not pending_event.is_empty(),
        "equipment": equipment_placed.size(),
        "tile_count": tiles.size(),
        "weekly_outflow": Economy.weekly_outflow(self),
        "score": Scoring.compute(self, true),
        "phases": storey_phase_status(),
    }


# ----------------------------------------------------------------- save / load

func serialize() -> Dictionary:
    var rts: Dictionary = {}
    for tid in runtime:
        rts[tid] = (runtime[tid] as TaskRuntime).to_dict()
    var tile_list: Array = []
    for c in tiles:
        var td: Dictionary = tiles[c]
        tile_list.append([(c as Vector2i).x, (c as Vector2i).y, str(td["tile"]), int(td["orientation"])])
    var eq: Array = []
    for e in equipment_placed:
        eq.append([str(e["id"]), (e["cell"] as Vector2i).x, (e["cell"] as Vector2i).y])
    return {
        "version": 1,
        "scenario_id": scenario.id,
        "week": week, "cash": cash,
        "crews": crews.duplicate(true), "next_crew_id": _next_crew_id,
        "tiles": tile_list, "equipment": eq,
        "tasks": rts,
        "rng_seed": str(rng.seed), "rng_state": str(rng.state),
        "learning": learning_counts.duplicate(),
        "modifiers": modifiers.duplicate(true),
        "zone_paused": zone_paused_until.duplicate(),
        "fired_events": fired_events.duplicate(),
        "incidents": incidents, "goodwill": goodwill, "score_adjust": score_adjust,
        "plan_changes": plan_changes,
        "insp": [inspections_first_total, inspections_first_pass, inspection_failures_total],
        "spent_total": spent_total, "overdraft_fees": overdraft_fees_total,
        "payments": payments_received, "retention": retention_held, "negative_weeks": negative_weeks,
        "spend_curve": cumulative_spend_by_week.duplicate(),
        "crew_curve": crew_count_by_week.duplicate(),
        "hired_this_week": hired_this_week.duplicate(),
        "finished": finished, "won": won,
    }


func deserialize(d: Dictionary) -> bool:
    if str(d.get("scenario_id", "")) != scenario.id:
        last_error = "save belongs to another scenario"
        return false
    week = int(d["week"])
    cash = float(d["cash"])
    crews.clear()
    for c in d["crews"]:
        var cd: Dictionary = c
        crews.append({"id": int(cd["id"]), "trade": str(cd["trade"]), "zone_id": str(cd["zone_id"])})
    _next_crew_id = int(d["next_crew_id"])
    tiles.clear()
    for t in d["tiles"]:
        var a: Array = t
        tiles[Vector2i(int(a[0]), int(a[1]))] = {"tile": str(a[2]), "orientation": int(a[3])}
    tiles_version += 1
    equipment_placed.clear()
    for e in d["equipment"]:
        var ea: Array = e
        equipment_placed.append({"id": str(ea[0]), "cell": Vector2i(int(ea[1]), int(ea[2]))})
    var rts: Dictionary = d["tasks"]
    for tid in rts:
        if runtime.has(tid):
            (runtime[tid] as TaskRuntime).from_dict(rts[tid])
    rng.seed = int(str(d["rng_seed"]))
    rng.state = int(str(d["rng_state"]))
    learning_counts = {}
    for k in d["learning"]:
        learning_counts[str(k)] = int(d["learning"][k])
    modifiers.clear()
    for m in d["modifiers"]:
        var md: Dictionary = m
        modifiers.append({"factor": float(md["factor"]), "tag": str(md["tag"]), "trade": str(md["trade"]),
                "zone_tag": str(md["zone_tag"]), "from_week": int(md["from_week"]), "until_week": int(md["until_week"])})
    zone_paused_until = {}
    for k in d["zone_paused"]:
        zone_paused_until[str(k)] = int(d["zone_paused"][k])
    fired_events = {}
    for k in d["fired_events"]:
        fired_events[str(k)] = int(d["fired_events"][k])
    incidents = int(d["incidents"])
    goodwill = float(d["goodwill"])
    score_adjust = float(d["score_adjust"])
    plan_changes = int(d["plan_changes"])
    var insp: Array = d["insp"]
    inspections_first_total = int(insp[0])
    inspections_first_pass = int(insp[1])
    inspection_failures_total = int(insp[2])
    spent_total = float(d["spent_total"])
    overdraft_fees_total = float(d["overdraft_fees"])
    payments_received = float(d["payments"])
    retention_held = float(d["retention"])
    negative_weeks = int(d["negative_weeks"])
    cumulative_spend_by_week.clear()
    for v in d["spend_curve"]:
        cumulative_spend_by_week.append(float(v))
    crew_count_by_week.clear()
    for v in d["crew_curve"]:
        crew_count_by_week.append(int(v))
    hired_this_week = {}
    for k in d["hired_this_week"]:
        hired_this_week[str(k)] = int(d["hired_this_week"][k])
    finished = bool(d["finished"])
    won = bool(d["won"])
    pending_event = {}
    for g in element_visuals:
        element_visuals[g] = Visual.GHOST
    for e in bundle.elements:
        _update_element_visual(e.guid)
    refresh_states()
    tiles_changed.emit()
    crews_changed.emit()
    cash_changed.emit(cash)
    week_advanced.emit(week)
    return true
