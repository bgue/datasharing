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
signal package_state_changed(package_id: String, state: String)
## Tasks were added / removed / re-linked at runtime (manual sequencing); views rebuild.
signal tasks_changed()
## A zone's manual mode was switched.
signal manual_changed()

## Visual state of a BIM element (see BimView).
enum Visual { GHOST, FRAMED, SOLID, INSPECTED, REWORK }

const WEEK_DAYS: int = 5
const MAX_WEEKS_FACTOR: int = 3

var bundle: SequenceBundle = null
var scenario: ScenarioData = null
var running: bool = false

## Per-task runtime state (PackedArray store); `runtime[task_id]` returns the TaskRuntime view, see task_store.gd.
var runtime: TaskStore = TaskStore.new()
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
var spent_by: Dictionary = {}  # category (materials, weekly, place, mobilise, overdraft fee, event) -> amount
var overdraft_fees_total: float = 0.0
var payments_received: float = 0.0
var retention_held: float = 0.0
var negative_weeks: int = 0
var cumulative_spend_by_week: Array[float] = []
var crew_count_by_week: Array[int] = []
var crew_days_worked: float = 0.0
var crew_days_idle: float = 0.0
var crew_days_by_trade: Dictionary = {}  # trade -> [worked, idle] (all hired crews: they are paid)
var week_crew_days_worked: float = 0.0
var week_crew_days_idle: float = 0.0
var crew_idle_days: Dictionary = {}  # crew id -> consecutive working days without productive work
var worked_this_week: Array[String] = []
var worked_set: Dictionary = {}  # task id -> true, the same tasks as worked_this_week (dedupe)
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
## BLOCKED tasks that only time, a delivery or a gate can release (see Readiness._apply): re-checked every week.
var readiness_watch: Dictionary = {}  # slot -> true
## BLOCKED by a gate: released by note_changed when the gate opens, re-checked by the end-of-week pass (counts in reasons).
var readiness_gate_watch: Dictionary = {}
## Readiness evaluations since start (diagnostics).
var stat_evals: int = 0
var last_gate_gi: int = -1
var last_gate_n: int = 0
## Microseconds per section of the last advance_week() (events, release, days, economy_safety, report, refresh_end).
var last_week_profile_us: Dictionary = {}
## Microseconds spent per part of the work days of the current / last week (ready, hook, work, inspect).
var day_profile_us: Dictionary = {}
## Optional hook called with the day index before each work day (auto-planners, tests).
var before_work_day: Callable = Callable()

## Caches of the package code (Packages): task slots per package and priority-sorted (zone, trade) package lists.
var pkg_slots: Dictionary = {}
var zt_cache: Dictionary = {}
var zt_cache_gen: int = -1
## Work packages and zones (docs/05).
var package_runtime: Dictionary = {}  # package_id -> PackageRuntime
var zone_runtime: Dictionary = {}  # zone_id -> ZoneRuntime
var crew_package: Dictionary = {}  # crew id -> package id (last working day)
var zone_face_state: Dictionary = {}  # zone_id -> face -> {crews, cap, over, excluded, discipline}
var double_shift_zone_weeks: int = 0
var incident_log: Array[Dictionary] = []  # {week, zone_id}
var element_visuals: Dictionary = {}  # guid -> Visual
## Gate open-counts from the last readiness refresh (conservative within a week).
var gate_cache: Dictionary = {}
var _access_cache: Dictionary = {}
var _access_cache_version: int = -1
var _access_reach: Dictionary = {}
var _hoarding_cache: Dictionary = {}
var _hoarding_version: int = -1
var _hoarding_cells: Array[Vector2i] = []
## Crews per zone while the day's work pass runs (Productivity.run_day), null otherwise.
var zone_crews_cache: Variant = null
var _pkg_order: Dictionary = {}
var _gate_after: PackedInt32Array = PackedInt32Array()
var _gate_before: PackedInt32Array = PackedInt32Array()
var _gate_orders_n: int = -1
var _gate_orders_stale: bool = true
var _gate_orders_bundle: SequenceBundle = null
## Task id -> display label for blocked reasons (Readiness.pred_label).
var label_cache: Dictionary = {}
var _crane_tasks: Array[TaskData] = []
var _long_lead_tasks: Array[TaskData] = []
var _task_lists_n: int = -1
var _task_lists_bundle: SequenceBundle = null
## Zone id -> lane count of its Gantt row from the planned spans (GanttModel lazy mode).
var gantt_lanes: Dictionary = {}
var crane_cache: Array[Dictionary] = []
var crane_cache_key: String = ""
var equipment_version: int = 0
var task_crane_memo: Dictionary = {}
var task_crane_key: String = ""
## Tests: make every refresh_states(false) a full pass (reference behaviour for the incremental readiness).
var debug_full_refresh: bool = false
## Packages that had crews at the last Packages.refresh_states.
var crewed_pkgs: Dictionary = {}
var _footprint: Dictionary = {}

## Manual sequencing (docs/06 A.3): zones in manual mode (zone id -> true; their generated packages are frozen),
## ids of tasks created / changed at runtime, pristine tasks removed, the id counter and the applied recipes.
var manual_zones: Dictionary = {}
var manual_touched: Dictionary = {}  # task id -> true (created, updated or re-linked by manual operations)
var manual_removed: Dictionary = {}  # removed loaded task id -> true
var manual_next_id: int = 1
var manual_applied: Array[Dictionary] = []  # {recipe, zone_id, element_guid, include_optional}
var _recipe_fallback: Array[RecipeData] = []
var _recipe_fallback_loaded: bool = false


# ----------------------------------------------------------------- lifecycle

var api: ApiServer = null


func _init() -> void:
    runtime.listener = Callable(self, "_on_state_set")
    runtime.gate_live = Callable(self, "_live_gate_reason")


## Reason text of a task held by a gate, with the gate's current count (see TaskStore.gate_live).
func _live_gate_reason(i: int, stored: String) -> String:
    var gi: int = runtime.gate_idx[i]
    var gates: Array[GateDef] = bundle.gates
    if gi < 0 or gi >= gates.size():
        return stored
    var per: Variant = gate_cache.get(gi, null)
    if per == null:
        return stored
    var n: int = int((per as Dictionary).get(SequenceBundle.gate_scope_key(gates[gi], runtime.task_refs[i]), -1))
    return Readiness.gate_reason(gates[gi], n) if n > 0 else stored


func _ready() -> void:
    _maybe_start_api()


## Starts the control API when `--api[=port]` is on the command line (before or after `--`) or the
## project setting sitebuilder/api/enabled is true. `--api-token=X` requires the token in every call.
func _maybe_start_api() -> void:
    var enabled: bool = bool(ProjectSettings.get_setting("sitebuilder/api/enabled", false))
    var port: int = int(ProjectSettings.get_setting("sitebuilder/api/port", ApiServer.DEFAULT_PORT))
    var token: String = str(ProjectSettings.get_setting("sitebuilder/api/token", ""))
    var args: PackedStringArray = OS.get_cmdline_args() + OS.get_cmdline_user_args()
    for a in args:
        if a == "--api":
            enabled = true
        elif a.begins_with("--api="):
            enabled = true
            port = int(a.substr(6))
        elif a.begins_with("--api-token="):
            token = a.substr(12)
    if not enabled:
        return
    api = ApiServer.new()
    api.name = "ApiServer"
    add_child(api)
    api.start(self, port, token)

func start(b: SequenceBundle) -> bool:
    if b == null or not b.valid:
        last_error = "invalid bundle"
        return false
    if b.edited:  # manual edits of a previous run: start from the pristine tasks again
        var fresh: SequenceBundle = b.pristine_copy()
        if fresh.valid:
            b = fresh
    bundle = b
    scenario = b.scenario
    running = true
    runtime.clear()
    for t in b.tasks:
        runtime.add_task(t)
    readiness_watch.clear()
    readiness_gate_watch.clear()
    dirty_tasks.clear()
    gate_cache = {}
    pkg_slots.clear()
    zt_cache.clear()
    _pkg_order.clear()
    label_cache.clear()
    gantt_lanes.clear()
    manual_zones.clear()
    manual_touched.clear()
    manual_removed.clear()
    manual_applied.clear()
    manual_next_id = Manual.first_free_id(b)
    _recipe_fallback.clear()
    _recipe_fallback_loaded = false
    package_runtime.clear()
    crewed_pkgs.clear()
    for p in b.packages:
        var prt := PackageRuntime.new()
        prt.bind(runtime, p.package_id)
        prt.priority = p.planned_start_day
        package_runtime[p.package_id] = prt
    zone_runtime.clear()
    for z in b.zones:
        zone_runtime[z.id] = ZoneRuntime.new()
    crew_package.clear()
    zone_face_state.clear()
    double_shift_zone_weeks = 0
    incident_log.clear()
    week = 0
    cash = scenario.start_cash
    speed = 0
    tiles.clear()
    tiles_version += 1
    crews.clear()
    _next_crew_id = 1
    equipment_placed.clear()
    equipment_version += 1
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
    spent_by.clear()
    overdraft_fees_total = 0.0
    payments_received = 0.0
    retention_held = 0.0
    negative_weeks = 0
    cumulative_spend_by_week.clear()
    crew_count_by_week.clear()
    crew_days_worked = 0.0
    crew_days_idle = 0.0
    crew_days_by_trade.clear()
    crew_idle_days.clear()
    week_crew_days_worked = 0.0
    week_crew_days_idle = 0.0
    worked_this_week.clear()
    worked_set.clear()
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
    if b.manual != null:
        for zid in b.manual.zones_in_manual_mode:
            if b.zones_by_id.has(zid):
                manual_zones[zid] = true
        for a in b.manual.applied_recipes:
            manual_applied.append(a.duplicate())
        apply_manual_flags()
    refresh_states()
    level_started.emit()
    return true


func _load_initial_tiles() -> void:
    for g in scenario.gates:
        tiles[g] = {"tile": SiteTiles.GATE, "orientation": 0}
    for it in scenario.initial_tiles:
        tiles[it["cell"]] = {"tile": str(it["tile"]), "orientation": int(it["orientation"])}
    tiles_version += 1


## Loads the per-zone detail file of a split bundle (bundle_format.zone_detail_dir) the first time the zone is looked at:
## task descriptions and aggregate member lists. Clears the label cache when something was merged.
func ensure_zone_detail(zone_id: String) -> int:
    if bundle == null:
        return 0
    var n: int = bundle.ensure_zone_detail(zone_id)
    if n > 0:
        label_cache.clear()
    return n


## Tasks that need a crane / have a lead time (cached; the planner asks for them every working day).
func crane_tasks() -> Array[TaskData]:
    _ensure_task_lists()
    return _crane_tasks


func long_lead_tasks() -> Array[TaskData]:
    _ensure_task_lists()
    return _long_lead_tasks


func _ensure_task_lists() -> void:
    if _task_lists_n == bundle.tasks.size() and _task_lists_bundle == bundle:
        return
    _crane_tasks = []
    _long_lead_tasks = []
    for t in bundle.tasks:
        if t.requires_crane:
            _crane_tasks.append(t)
        if t.lead_time_weeks > 0:
            _long_lead_tasks.append(t)
    _task_lists_n = bundle.tasks.size()
    _task_lists_bundle = bundle


func current_day() -> int:
    return week * WEEK_DAYS + day_in_week


func equipment_def(id: String) -> EquipmentDef:
    for e in scenario.equipment:
        if e.id == id:
            return e
    return null


func log_event(msg: String) -> void:
    week_log.append(msg)


func spend(amount: float, what: String = "") -> void:
    if amount <= 0.0:
        return
    var cat: String = what.get_slice(":", 0).get_slice(" ", 0) if what != "" else "other"
    spent_by[cat] = float(spent_by.get(cat, 0.0)) + amount
    cash -= amount
    spent_total += amount
    cash_changed.emit(cash)


func set_task_state(task_id: String, new_state: int) -> void:
    runtime.set_state(runtime.idx_of(task_id), new_state)


## Store listener: every task state change (set_task_state or a direct `rt.state = x`) updates the element visual,
## queues the successors / gate holders for the next readiness pass and emits task_state_changed.
func _on_state_set(i: int, old_state: int, new_state: int) -> void:
    var task: TaskData = runtime.task_refs[i]
    if old_state <= TaskRuntime.State.BLOCKED and new_state <= TaskRuntime.State.BLOCKED:
        # NOT_STARTED / READY / BLOCKED flips change neither the element visual nor any successor
        task_state_changed.emit(task.task_id, old_state, new_state)
        return
    if not task.is_virtual:  # virtual tasks have no element: they show as markers (virtual_markers)
        for g in task.element_guids:
            _update_element_visual(g)
    if new_state == TaskRuntime.State.ACTIVE or TaskRuntime.is_finished(new_state):
        Readiness.note_changed(self, task.task_id, new_state)
    task_state_changed.emit(task.task_id, old_state, new_state)


## `full` re-evaluates every task that has not started (the safe default: tests and tools that edit task state directly
## rely on it); `full = false` is the incremental pass of the weekly loop and of tile / equipment / order actions.
func refresh_states(full: bool = true, include_gates: bool = true) -> void:
    if full or debug_full_refresh:
        full = true
        Readiness.refresh(self)
    else:
        Readiness.refresh_incremental(self, include_gates)
    for zid in card_zones():
        Cards.sync_zone(self, zid)
    Packages.refresh_states(self, full)


# ----------------------------------------------------------------- packages, cards, shifts

func card_zones() -> Array[String]:
    var out: Array[String] = []
    for zid in zone_runtime:
        if (zone_runtime[zid] as ZoneRuntime).card_id != "":
            out.append(zid)
    return out


func double_shift_zones() -> Array[String]:
    var out: Array[String] = []
    for zid in zone_runtime:
        if (zone_runtime[zid] as ZoneRuntime).shift_mode == "double":
            out.append(zid)
    return out


func is_double_shift(zone_id: String) -> bool:
    var zr: ZoneRuntime = zone_runtime.get(zone_id, null)
    return zr != null and zr.shift_mode == "double"


## Second shift (docs/05 section 4). Refuses zones that forbid it, forbidden tags and the max_zones cap.
func set_shift(zone_id: String, mode: String) -> bool:
    var zone: ZoneData = bundle.zones_by_id.get(zone_id, null)
    if zone == null:
        last_error = "no such zone"
        return false
    if mode != "single" and mode != "double":
        last_error = "mode must be single or double"
        return false
    var zr: ZoneRuntime = zone_runtime[zone_id]
    if mode == "double" and zr.shift_mode != "double":
        if not zone.shift_allowed:
            last_error = "Double shift is not allowed in this zone"
            return false
        for tag in scenario.shift_forbidden_zone_tags:
            if zone.tags.has(tag):
                last_error = "Double shift refused: zone is tagged %s (quiet hours)" % tag
                return false
        if double_shift_zones().size() >= scenario.shift_max_zones:
            last_error = "At most %d zones may run double shift" % scenario.shift_max_zones
            return false
    zr.shift_mode = mode
    crews_changed.emit()
    return true


func hold_package(package_id: String) -> bool:
    if not package_runtime.has(package_id):
        last_error = "no such package"
        return false
    (package_runtime[package_id] as PackageRuntime).released = false
    Packages.refresh_states(self)
    return true


func release_package(package_id: String) -> bool:
    if not package_runtime.has(package_id):
        last_error = "no such package"
        return false
    (package_runtime[package_id] as PackageRuntime).released = true
    Packages.refresh_states(self)
    return true


func set_package_priority(package_id: String, priority: int) -> bool:
    if not package_runtime.has(package_id):
        last_error = "no such package"
        return false
    (package_runtime[package_id] as PackageRuntime).priority = priority
    Packages.refresh_states(self)
    return true


# ----------------------------------------------------------------- manual sequencing / virtual tasks

## True for a generated task in a zone in manual mode: frozen, it does not count for gates or completion.
func is_frozen_task(task: TaskData) -> bool:
    return not manual_zones.is_empty() and manual_zones.has(task.zone_id) and not task.is_authored()


## (Re)applies the frozen flag to the generated packages of every zone in manual mode and releases the others.
func apply_manual_flags() -> void:
    for zone in bundle.zones:
        var on: bool = manual_zones.has(zone.id)
        for p in bundle.packages_by_zone.get(zone.id, []):
            var pkg: PackageData = p
            var rt: PackageRuntime = package_runtime.get(pkg.package_id, null)
            if rt == null or pkg.manual:
                continue
            if on and not rt.frozen:
                rt.frozen = true
                rt.released_before_freeze = rt.released
                rt.released = false
            elif not on and rt.frozen:
                rt.frozen = false
                rt.released = rt.released_before_freeze


## Registers a task created at runtime (manual.add_task, recipes): indices, successors, gate caches, package and
## runtime structures. The task needs a unique id, a known zone and step and existing predecessors.
## `refresh` = false defers the readiness pass (batch creation: call graph_changed() once at the end).
func register_task(task: TaskData, refresh: bool = true, package_id: String = "") -> bool:
    if bundle == null or not running:
        last_error = "level not running"
        return false
    if task.task_id == "" or bundle.tasks_by_id.has(task.task_id):
        last_error = "task id %s is empty or already used" % task.task_id
        return false
    if not bundle.zones_by_id.has(task.zone_id):
        last_error = "no such zone: %s" % task.zone_id
        return false
    if not bundle.steps_by_id.has(task.step_id):
        last_error = "unknown step: %s" % task.step_id
        return false
    for p in task.predecessors:
        if not bundle.tasks_by_id.has(str(p["task_id"])):
            last_error = "unknown predecessor: %s" % str(p["task_id"])
            return false
    task.runtime_added = true
    bundle.add_task(task)
    bundle.assign_package(task, package_id)
    runtime.add_task(task)
    manual_touched[task.task_id] = true
    if refresh:
        graph_changed()
    return true


## Removes a task created at runtime (or a loaded authored task) from every structure. Links in other tasks are the
## caller's business (Manual.remove_task bridges them first).
func unregister_task(task: TaskData) -> void:
    bundle.remove_task(task)
    runtime.erase(task.task_id)
    dirty_tasks.erase(task.task_id)
    worked_this_week.erase(task.task_id)
    worked_set.erase(task.task_id)
    manual_touched.erase(task.task_id)
    if not task.runtime_added:
        manual_removed[task.task_id] = true
    for g in task.element_guids:
        if not task.is_virtual and bundle.elements_by_guid.has(g):
            _update_element_visual(g)


## Brings the runtime structures in line with the (edited) task graph: package runtimes, gate caches, readiness,
## package states. Emits tasks_changed.
## Drops the cached gate phase orders (the full readiness pass calls this: tests and tools may edit bundle.gates).
func invalidate_gate_orders() -> void:
    _gate_orders_stale = true


## Phase orders of the bundle's gates: `after` (phase up to which the gate counts) or `before` (first phase it holds).
func gate_orders(after: bool) -> PackedInt32Array:
    if _gate_orders_n != bundle.gates.size() or _gate_orders_bundle != bundle or _gate_orders_stale:
        _gate_orders_stale = false
        _gate_after = PackedInt32Array()
        _gate_before = PackedInt32Array()
        for g in bundle.gates:
            _gate_after.append(bundle.order_of_phase(g.after_phase))
            _gate_before.append(bundle.order_of_phase(g.before_phase))
        _gate_orders_n = bundle.gates.size()
        _gate_orders_bundle = bundle
    return _gate_after if after else _gate_before


## Package id -> position in bundle.packages (cached until the package list changes).
func package_order() -> Dictionary:
    if _pkg_order.size() != bundle.packages.size():
        _pkg_order.clear()
        for i in bundle.packages.size():
            _pkg_order[bundle.packages[i].package_id] = i
    return _pkg_order


func graph_changed() -> void:
    _task_lists_n = -1
    gantt_lanes.clear()
    task_crane_memo.clear()
    _pkg_order.clear()
    label_cache.clear()
    bundle.invalidate_gate_caches()
    pkg_slots.clear()
    zt_cache.clear()
    runtime.work_cache.clear()
    for p in bundle.packages:
        if not package_runtime.has(p.package_id):
            var prt := PackageRuntime.new()
            prt.bind(runtime, p.package_id)
            prt.priority = p.planned_start_day
            package_runtime[p.package_id] = prt
    for id in package_runtime.keys():
        if not bundle.packages_by_id.has(id):
            package_runtime.erase(id)
    refresh_states()
    tasks_changed.emit()


## Marker kit id of a virtual task: its own, else derived from the step id ("" for tasks that are not virtual).
func marker_of(task: TaskData) -> String:
    if not task.is_virtual:
        return ""
    if task.marker != "":
        return task.marker
    var sid: String = task.step_id
    for pair in [["SURVEY", "survey"], ["DEWATER", "dewatering"], ["SCAFF", "scaffold"], ["LIFT", "lift_plan"],
            ["PERMIT", "permit"], ["SHOR", "shoring"], ["CRANE", "crane"], ["TEST", "test"], ["HYDRO", "test"],
            ["PRESS", "test"]]:
        if sid.contains(pair[0]):
            return pair[1]
    return "generic"


## Marker instances for the 3D view (BimView): one per virtual task, at the zone centre cell.
## Array of {task_id, marker, zone_id, cell: Vector2i, storey_id, state: String, index: int (per zone)}.
func virtual_markers() -> Array[Dictionary]:
    var out: Array[Dictionary] = []
    if bundle == null:
        return out
    var per_zone: Dictionary = {}
    for t in bundle.virtual_tasks:
        var zone: ZoneData = bundle.zones_by_id.get(t.zone_id, null)
        if zone == null:
            continue
        var rt: TaskRuntime = runtime.get_rt(t.task_id)
        var idx: int = int(per_zone.get(t.zone_id, 0))
        per_zone[t.zone_id] = idx + 1
        out.append({"task_id": t.task_id, "marker": marker_of(t), "zone_id": t.zone_id, "cell": zone.centre_cell(),
                "storey_id": zone.storey_id, "state": TaskRuntime.state_name(rt.state) if rt != null else "NOT_STARTED",
                "index": idx})
    return out


func virtual_task_counts() -> Dictionary:
    var total: int = 0
    var finished: int = 0
    var active: int = 0
    for t in bundle.virtual_tasks:
        total += 1
        var st: int = (runtime[t.task_id] as TaskRuntime).state
        if TaskRuntime.is_finished(st):
            finished += 1
        elif st == TaskRuntime.State.ACTIVE or st == TaskRuntime.State.REWORK or st == TaskRuntime.State.AWAITING_INSPECTION:
            active += 1
    return {"total": total, "finished": finished, "active": active}


## Recipes of this scenario: the bundle's `recipes[]`; when it has none, the logic library under res://logic
## (index.json + recipes/**.json) if present. Nothing is synthesised.
func recipe_list() -> Array[RecipeData]:
    if bundle == null:
        return []
    if not bundle.recipes.is_empty():
        return bundle.recipes
    if not _recipe_fallback_loaded:
        _recipe_fallback = LogicLib.load_library()
        _recipe_fallback_loaded = true
    return _recipe_fallback


func recipe_by_id(id: String) -> RecipeData:
    if bundle != null and bundle.recipes_by_id.has(id):
        return bundle.recipes_by_id[id]
    for r in recipe_list():
        if r.id == id:
            return r
    return null


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
        var ti: int = runtime.index[task.task_id]
        var s: int = runtime.state[ti]
        match s:
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
                if s == TaskRuntime.State.INSPECTED:
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
            crew_idle_days.erase(crew_id)
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
    refresh_states(false, false)
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
    equipment_version += 1
    tiles_version += 1
    tiles_changed.emit()
    refresh_states(false, false)
    return true


## True when a hoarding tile stands next to the zone (cached per tile layer version; used by the safety roll). Checks the
## hoarding tiles against the zone's cells instead of building the ring around every zone.
func zone_has_hoarding(zone: ZoneData) -> bool:
    if _hoarding_version != tiles_version:
        _hoarding_cache.clear()
        _hoarding_cells.clear()
        for c in tiles:
            if str((tiles[c] as Dictionary).get("tile", "")) == SiteTiles.HOARDING:
                _hoarding_cells.append(c)
        _hoarding_version = tiles_version
    if not _hoarding_cache.has(zone.id):
        var found: bool = false
        if not _hoarding_cells.is_empty():
            var set: Dictionary = {}
            for c in zone.cells:
                set[c] = true
            for h in _hoarding_cells:
                for dx in range(-1, 2):
                    for dz in range(-1, 2):
                        if (dx != 0 or dz != 0) and set.has(Vector2i(h.x + dx, h.y + dz)) and not set.has(h):
                            found = true
                            break
                    if found:
                        break
                if found:
                    break
        _hoarding_cache[zone.id] = found
    return bool(_hoarding_cache[zone.id])


func tile_at(cell: Vector2i) -> String:
    return Logistics.tile_at(tiles, cell)


## Access of a zone: BFS from gates over haul road (cached per tile layer version).
func zone_access(zone_id: String) -> bool:
    if _access_cache_version != tiles_version:
        _access_cache.clear()
        _access_reach = {}
        _access_cache_version = tiles_version
    if not _access_cache.has(zone_id):
        var z: ZoneData = bundle.zones_by_id.get(zone_id, null)
        var ok: bool = false
        if z != null and not z.cells.is_empty() and not scenario.gates.is_empty():
            if _access_reach.is_empty():  # one flood fill from the gates per tile layer, shared by all zones
                _access_reach = Logistics.access_set(tiles, scenario.gates, bundle.zone_cell_set)
            ok = Logistics.set_reaches_zone(_access_reach, z.cells)
        _access_cache[zone_id] = ok
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
    equipment_version += 1
    tiles_changed.emit()
    refresh_states(false, false)
    return true


func remove_equipment(index: int) -> bool:
    if index < 0 or index >= equipment_placed.size():
        return false
    equipment_placed.remove_at(index)
    equipment_version += 1
    tiles_changed.emit()
    refresh_states(false, false)
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
    dirty_tasks.append(task_id)  # only this task's procurement block changed
    refresh_states(false, false)
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
    var landed: Array[int] = []
    for i in runtime.ordered:
        if runtime.delivery_week[i] == week:
            landed.append(i)
    landed.sort()  # task order, as the full scan had
    for i in landed:
        log_event("Delivery landed: %s" % Readiness.pred_label(self, runtime.ids[i]))


# ----------------------------------------------------------------- weekly loop

## Order: deliveries -> events -> release -> progress -> inspections -> economy -> safety -> score.
## Returns false if the week could not be advanced (finished, or event awaiting a choice).
func advance_week() -> bool:
    if not running or finished or not pending_event.is_empty():
        return false
    week_log.clear()
    week_crew_days_worked = 0.0
    week_crew_days_idle = 0.0
    worked_this_week.clear()
    worked_set.clear()
    hired_this_week.clear()
    var cash_before: float = cash
    var finished_before: int = finished_task_count()
    var prof: Dictionary = {}
    day_profile_us = {}
    var t0: int = Time.get_ticks_usec()
    # 1 deliveries land
    _land_deliveries()
    # 2 events draw
    Events.draw(self)
    prof["events"] = Time.get_ticks_usec() - t0
    t0 = Time.get_ticks_usec()
    # 3 release pass (states current for the crews' work)
    refresh_states(false, false)
    prof["release"] = Time.get_ticks_usec() - t0
    t0 = Time.get_ticks_usec()
    # 4 + 5 progress and inspections at day resolution (5 working days)
    for d in WEEK_DAYS:
        run_work_day(d)
    day_in_week = 0
    prof["days"] = Time.get_ticks_usec() - t0
    prof["days_ready"] = int(day_profile_us.get("ready", 0))
    prof["days_hook"] = int(day_profile_us.get("hook", 0))
    prof["days_work"] = int(day_profile_us.get("work", 0))
    prof["days_inspect"] = int(day_profile_us.get("inspect", 0))
    t0 = Time.get_ticks_usec()
    # 6 economy
    Economy.run_week(self)
    # 7 safety
    Safety.run_week(self)
    prof["economy_safety"] = Time.get_ticks_usec() - t0
    t0 = Time.get_ticks_usec()
    # 8 score snapshot
    double_shift_zone_weeks += double_shift_zones().size()
    cumulative_spend_by_week.append(spent_total)
    crew_count_by_week.append(crews.size())
    var report: Dictionary = {
        "week": week,
        "cash": cash,
        "cash_change": cash - cash_before,
        "tasks_finished": finished_task_count() - finished_before,
        "worked_tasks": worked_this_week.size(),
        "utilisation_week": utilisation_of(week_crew_days_worked, week_crew_days_idle),
        "utilisation": crew_utilisation(),
        "log": week_log.duplicate(),
        "score": Scoring.compute(self, true),
    }
    week += 1
    _expire_modifiers()
    prof["report"] = Time.get_ticks_usec() - t0
    t0 = Time.get_ticks_usec()
    refresh_states(false)
    prof["refresh_end"] = Time.get_ticks_usec() - t0
    last_week_profile_us = prof
    last_report = report
    week_advanced.emit(week)
    _check_finish()
    return true


## One working day (0..4 of the current week): incremental readiness, every assigned crew
## spends one crew-day, inspections due at the end of the day resolve.
func run_work_day(d: int) -> void:
    day_in_week = d
    var t0: int = Time.get_ticks_usec()
    if d > 0:
        Readiness.process_dirty(self, week * WEEK_DAYS + d)
    Cards.update(self)
    var t1: int = Time.get_ticks_usec()
    if before_work_day.is_valid():
        before_work_day.call(d)
    var t2: int = Time.get_ticks_usec()
    Productivity.run_day(self, d)
    var t3: int = Time.get_ticks_usec()
    Inspections.run_day(self, d)
    var t4: int = Time.get_ticks_usec()
    day_in_week = 0
    var dp: Dictionary = day_profile_us
    dp["ready"] = int(dp.get("ready", 0)) + t1 - t0
    dp["hook"] = int(dp.get("hook", 0)) + t2 - t1
    dp["work"] = int(dp.get("work", 0)) + t3 - t2
    dp["inspect"] = int(dp.get("inspect", 0)) + t4 - t3


func _expire_modifiers() -> void:
    for i in range(modifiers.size() - 1, -1, -1):
        if int(modifiers[i]["until_week"]) <= week:
            modifiers.remove_at(i)


func resolve_event(choice_index: int) -> void:
    Events.resolve(self, choice_index)
    refresh_states(false, false)


static func utilisation_of(worked: float, idle: float) -> float:
    var total: float = worked + idle
    return worked / total if total > 0.0 else 0.0


## Crew utilisation so far: worked / (worked + idle) crew-days over all hired crews.
func crew_utilisation() -> float:
    return utilisation_of(crew_days_worked, crew_days_idle)


func finished_task_count() -> int:
    return runtime.finished_count()


func all_tasks_finished() -> bool:
    if manual_zones.is_empty():
        return finished_task_count() == runtime.size()
    for t in bundle.tasks:  # generated tasks frozen by a zone's manual mode do not count
        if not TaskRuntime.is_finished((runtime[t.task_id] as TaskRuntime).state) and not is_frozen_task(t):
            return false
    return true


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
    ## Aggregated zone info for overlay/inspector: counts per state and a colour category (from the TaskStore's per-zone
    ## counters, no task scan).
    var counts: Dictionary = {}
    var impeded: int = 0
    var blocked_gate_proc: int = 0
    var total: int = 0
    var st: TaskStore = runtime
    var zi: int = int(st.zone_index.get(zone_id, -1))
    for k in TaskRuntime.STATE_NAMES.size():
        var n: int = st.zone_counts[zi * 8 + k] if zi >= 0 else 0
        counts[TaskRuntime.STATE_NAMES[k]] = n
        total += n
    if zi >= 0:
        impeded = st.zone_impeded[zi]
        blocked_gate_proc = st.zone_gate[zi]
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
    var zr: ZoneRuntime = zone_runtime[zone_id]
    return {"counts": counts, "crews": n_crews, "max_crews": zone.max_crews, "color": color_key,
            "total": total, "impeded": impeded, "shift_mode": zr.shift_mode, "card_id": zr.card_id,
            "behind_takt": zr.behind_takt}


## Phase completion per storey: Array of {storey_id, name, index, phases: [{id, name, done, total, started}]}.
func storey_phase_status() -> Array[Dictionary]:
    # one pass over the tasks: storey_id -> phase -> [total, done, started]
    var acc: Dictionary = {}
    for g in runtime.group_keys.size():
        var gk: PackedStringArray = runtime.group_keys[g]
        if runtime.group_counts[g * 3] <= 0:
            continue
        if not acc.has(gk[0]):
            acc[gk[0]] = {}
        (acc[gk[0]] as Dictionary)[gk[1]] = [runtime.group_counts[g * 3], runtime.group_counts[g * 3 + 1],
                runtime.group_counts[g * 3 + 2]]
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
    for s in TaskRuntime.STATE_NAMES.size():
        counts[TaskRuntime.STATE_NAMES[s]] = runtime.counts[s]
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
        "double_shift_zone_weeks": double_shift_zone_weeks,
        "utilisation": crew_utilisation(),
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
        "packages": _serialize_packages(), "zones_rt": _serialize_zones(),
        "dsz": double_shift_zone_weeks, "incident_log": incident_log.duplicate(true),
        "manual": Manual.serialize(self),
    }


func _serialize_packages() -> Dictionary:
    var out: Dictionary = {}
    for id in package_runtime:
        out[id] = (package_runtime[id] as PackageRuntime).to_dict()
    return out


func _serialize_zones() -> Dictionary:
    var out: Dictionary = {}
    for id in zone_runtime:
        out[id] = (zone_runtime[id] as ZoneRuntime).to_dict()
    return out


func deserialize(d: Dictionary) -> bool:
    if str(d.get("scenario_id", "")) != scenario.id:
        last_error = "save belongs to another scenario"
        return false
    if bundle.edited:  # runtime manual edits of this session: replay the save on the pristine tasks
        start(bundle)
    Manual.restore(self, d.get("manual", {}))
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
    equipment_version += 1
    var rts: Dictionary = d["tasks"]
    runtime.silent = true  # bulk load: visuals and readiness are rebuilt below
    for tid in rts:
        if runtime.has(tid):
            (runtime[tid] as TaskRuntime).from_dict(rts[tid])
    runtime.silent = false
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
    var pk: Dictionary = d.get("packages", {})
    for id in pk:
        if package_runtime.has(id):
            (package_runtime[id] as PackageRuntime).from_dict(pk[id])
    var zr: Dictionary = d.get("zones_rt", {})
    for id in zr:
        if zone_runtime.has(id):
            (zone_runtime[id] as ZoneRuntime).from_dict(zr[id])
    apply_manual_flags()
    double_shift_zone_weeks = int(d.get("dsz", 0))
    incident_log.clear()
    for e in d.get("incident_log", []):
        incident_log.append({"week": int((e as Dictionary)["week"]), "zone_id": str((e as Dictionary)["zone_id"])})
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
