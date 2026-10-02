class_name SequenceBundle
extends RefCounted
## Parses a sequence.json game bundle into typed objects and builds the indices
## used by the simulation (see docs/03-architecture.md section 3).

var valid: bool = false
var errors: Array[String] = []
var source_path: String = ""

var project_raw: Dictionary = {}
var project_name: String = ""
var sector: String = ""
var description: String = ""
var cell_size_m: float = 6.0
var storey_height_m: float = 4.0
var width_cells: int = 1
var depth_cells: int = 1

var storeys: Array[StoreyData] = []  # sorted by index
var zones: Array[ZoneData] = []
var elements: Array[ElementData] = []
var tasks: Array[TaskData] = []
var steps: Array[StepDef] = []
var trades: Array[TradeDef] = []
var gates: Array[GateDef] = []
var phases: Array[Dictionary] = []  # {id, name, order} sorted by order
var systems: Dictionary = {}  # system id -> {name, discipline}
var scenario: ScenarioData = null
## Work packages (from the bundle, or synthesised from the tasks per docs/05 section 1.1).
var packages: Array[PackageData] = []
var packages_by_id: Dictionary = {}  # package_id -> PackageData
var packages_by_zone: Dictionary = {}  # zone_id -> Array[PackageData]
var packages_synthesised: bool = false
## step_library.packaging with schema defaults.
var packaging_group_by: Array[String] = ["zone_id", "phase", "trade", "work_face"]
var packaging_target_duration_days: int = 10
var packaging_max_crew_days: float = 60.0
var packaging_max_over_ideal: int = 1
var packaging_over_ideal_factor: float = 0.6
var exclusive_faces: Array[String] = ["floor"]
## Sequence cards: step library cards overridden / extended by scenario.sequence_cards (by id).
var card_ids: Array[String] = []
var cards_by_id: Dictionary = {}  # id -> SequenceCardData

var baseline_finish_day: int = 0
var baseline_finish_week: int = 0
var baseline_total_cost: float = 0.0
var baseline_weekly_planned_cost: Array[float] = []
var critical_task_ids: Array[String] = []

# indices
var tasks_by_id: Dictionary = {}  # task_id -> TaskData
var tasks_by_zone: Dictionary = {}  # zone_id -> Array[TaskData]
var tasks_by_element: Dictionary = {}  # guid -> Array[TaskData]
var tasks_by_storey: Dictionary = {}  # storey_id -> Array[TaskData]
var tasks_by_phase: Dictionary = {}  # phase -> Array[TaskData]
var successors_by_task: Dictionary = {}  # task_id -> Array[String]
var steps_by_id: Dictionary = {}  # step_id -> StepDef
var trades_by_id: Dictionary = {}  # trade id -> TradeDef
var zones_by_id: Dictionary = {}  # zone_id -> ZoneData
var storeys_by_id: Dictionary = {}  # storey_id -> StoreyData
var storey_index_by_id: Dictionary = {}  # storey_id -> int
var elements_by_guid: Dictionary = {}  # guid -> ElementData
var phase_order: Dictionary = {}  # phase id -> int
## storey_id -> { Vector2i cell -> Array[ZoneData] }
var zones_by_cell: Dictionary = {}
## Playable site extent in cells: the model grid plus every declared gate / occupied / blocked /
## tile / zone cell, plus SITE_MARGIN. Real bundles place gates and live areas outside the grid.
var site_rect: Rect2i = Rect2i(0, 0, 1, 1)
## Every cell covered by any zone (Vector2i -> true): the interior vehicles can circulate through.
var zone_cell_set: Dictionary = {}
var _gate_scope_cache: Dictionary = {}  # "gate|scope key" -> Array[TaskData]


static func load_from_path(path: String) -> SequenceBundle:
    var b := SequenceBundle.new()
    b.source_path = path
    if not FileAccess.file_exists(path):
        b.errors.append("file not found: %s" % path)
        return b
    var text: String = FileAccess.get_file_as_string(path)
    var parsed: Variant = JSON.parse_string(text)
    if not (parsed is Dictionary):
        b.errors.append("not a JSON object: %s" % path)
        return b
    b.parse(parsed)
    return b


static func from_dictionary(d: Dictionary) -> SequenceBundle:
    var b := SequenceBundle.new()
    b.parse(d)
    return b


func parse(d: Dictionary) -> void:
    for key in ["project", "storeys", "zones", "elements", "step_library", "tasks", "scenario"]:
        if not d.has(key):
            errors.append("missing key: %s" % key)
    if not errors.is_empty():
        return

    var proj: Dictionary = d["project"]
    project_raw = proj
    project_name = str(proj.get("name", ""))
    sector = str(proj.get("sector", ""))
    description = str(proj.get("description", ""))
    var grid: Dictionary = proj.get("grid", {})
    cell_size_m = maxf(0.01, float(grid.get("cell_size_m", 6.0)))
    storey_height_m = float(grid.get("storey_height_m", 4.0))
    width_cells = int(grid.get("width_cells", 1))
    depth_cells = int(grid.get("depth_cells", 1))

    for s in d["storeys"]:
        storeys.append(StoreyData.from_dict(s))
    storeys.sort_custom(func(a: StoreyData, b: StoreyData) -> bool: return a.index < b.index)
    for s in storeys:
        storeys_by_id[s.id] = s
        storey_index_by_id[s.id] = s.index

    var sys: Variant = d.get("systems", [])
    if sys is Array:
        for s in sys:
            var sd: Dictionary = s
            systems[str(sd.get("id", ""))] = {
                "name": str(sd.get("name", "")), "discipline": str(sd.get("discipline", "general")),
            }

    for z in d["zones"]:
        var zd := ZoneData.from_dict(z)
        zones.append(zd)
        zones_by_id[zd.id] = zd
        tasks_by_zone[zd.id] = [] as Array[TaskData]
        if not zones_by_cell.has(zd.storey_id):
            zones_by_cell[zd.storey_id] = {}
        var per_cell: Dictionary = zones_by_cell[zd.storey_id]
        for c in zd.cells:
            if not per_cell.has(c):
                per_cell[c] = [] as Array[ZoneData]
            (per_cell[c] as Array[ZoneData]).append(zd)

    for e in d["elements"]:
        var ed := ElementData.from_dict(e)
        elements.append(ed)
        elements_by_guid[ed.guid] = ed
        tasks_by_element[ed.guid] = [] as Array[TaskData]

    var lib: Dictionary = d["step_library"]
    for p in lib.get("phases", []):
        var pd: Dictionary = p
        phases.append({"id": str(pd.get("id", "")), "name": str(pd.get("name", "")), "order": int(pd.get("order", 0))})
    phases.sort_custom(func(a: Dictionary, b: Dictionary) -> bool: return int(a["order"]) < int(b["order"]))
    for p in phases:
        phase_order[p["id"]] = int(p["order"])
    for t in lib.get("trades", []):
        var td := TradeDef.from_dict(t)
        trades.append(td)
        trades_by_id[td.id] = td
    for s in lib.get("steps", []):
        var sd := StepDef.from_dict(s)
        steps.append(sd)
        steps_by_id[sd.id] = sd
    for g in lib.get("gates", []):
        gates.append(GateDef.from_dict(g))
    _parse_packaging(lib)
    for c in lib.get("sequence_cards", []):
        _add_card(SequenceCardData.from_dict(c))

    for t in d["tasks"]:
        var task := TaskData.from_dict(t)
        tasks.append(task)
        tasks_by_id[task.task_id] = task
        if not tasks_by_zone.has(task.zone_id):
            tasks_by_zone[task.zone_id] = [] as Array[TaskData]
        (tasks_by_zone[task.zone_id] as Array[TaskData]).append(task)
        if not tasks_by_element.has(task.element_guid):
            tasks_by_element[task.element_guid] = [] as Array[TaskData]
        (tasks_by_element[task.element_guid] as Array[TaskData]).append(task)
        if not tasks_by_storey.has(task.storey_id):
            tasks_by_storey[task.storey_id] = [] as Array[TaskData]
        (tasks_by_storey[task.storey_id] as Array[TaskData]).append(task)
        if not tasks_by_phase.has(task.phase):
            tasks_by_phase[task.phase] = [] as Array[TaskData]
        (tasks_by_phase[task.phase] as Array[TaskData]).append(task)
        successors_by_task[task.task_id] = [] as Array[String]
    for task in tasks:
        for p in task.predecessors:
            var pid: String = p["task_id"]
            if not tasks_by_id.has(pid):
                errors.append("task %s has unknown predecessor %s" % [task.task_id, pid])
                continue
            (successors_by_task[pid] as Array[String]).append(task.task_id)
        if not steps_by_id.has(task.step_id):
            errors.append("task %s references unknown step %s" % [task.task_id, task.step_id])
        if not zones_by_id.has(task.zone_id):
            errors.append("task %s references unknown zone %s" % [task.task_id, task.zone_id])

    var bl: Dictionary = d.get("baseline", {})
    baseline_finish_day = int(bl.get("finish_day", 0))
    baseline_finish_week = int(bl.get("finish_week", baseline_finish_day / 5))
    baseline_total_cost = float(bl.get("total_cost", 0.0))
    for v in bl.get("weekly_planned_cost", []):
        baseline_weekly_planned_cost.append(float(v))
    for tid in bl.get("critical_task_ids", []):
        critical_task_ids.append(str(tid))

    scenario = ScenarioData.from_dict(d["scenario"])
    for c in scenario.sequence_cards:
        _add_card(c)
    for task in tasks:
        var st: StepDef = steps_by_id.get(task.step_id, null)
        task.work_face = st.work_face if st != null else "any"
    _build_packages(d.get("packages", []))
    _compute_site_rect()
    valid = errors.is_empty()


func _parse_packaging(lib: Dictionary) -> void:
    var pk: Variant = lib.get("packaging", {})
    if pk is Dictionary:
        var pd: Dictionary = pk
        if pd.has("group_by"):
            packaging_group_by = []
            for g in pd["group_by"]:
                packaging_group_by.append(str(g))
        packaging_target_duration_days = maxi(1, int(pd.get("target_duration_days", 10)))
        packaging_max_crew_days = maxf(0.001, float(pd.get("max_crew_days_per_package", 60.0)))
        packaging_max_over_ideal = maxi(0, int(pd.get("max_over_ideal", 1)))
        packaging_over_ideal_factor = clampf(float(pd.get("over_ideal_factor", 0.6)), 0.0, 1.0)
    var ef: Variant = lib.get("exclusive_faces", null)
    if ef is Array:
        exclusive_faces = []
        for f in ef:
            exclusive_faces.append(str(f))


func _add_card(c: SequenceCardData) -> void:
    if not cards_by_id.has(c.id):
        card_ids.append(c.id)
    cards_by_id[c.id] = c  # later (scenario) cards override by id


## Crew profile (docs/05 section 1.2): ideal = clamp(ceil(total / target), max(min, 1), zone.max_crews),
## max = clamp(ideal + max_over_ideal, ideal, zone.max_crews). Steps may pin ideal / max.
func derive_profile(p: PackageData) -> void:
    var zone: ZoneData = zones_by_id.get(p.zone_id, null)
    var zmax: int = maxi(1, zone.max_crews) if zone != null else 99
    var min_c: int = 1
    var step_ideal: int = 0
    var step_max: int = 0
    for t in p.tasks:
        var st: StepDef = steps_by_id.get(t.step_id, null)
        if st != null:
            min_c = maxi(min_c, st.crew_min)
            step_ideal = maxi(step_ideal, st.crew_ideal)
            step_max = maxi(step_max, st.crew_max)
    min_c = mini(min_c, zmax)
    var ideal: int = ceili(p.total_crew_days / float(packaging_target_duration_days))
    if step_ideal > 0:
        ideal = step_ideal
    ideal = clampi(ideal, maxi(min_c, 1), zmax)
    var max_c: int = ideal + packaging_max_over_ideal
    if step_max > 0:
        max_c = step_max
    max_c = clampi(max_c, ideal, zmax)
    p.crew_min = min_c
    p.crew_ideal = ideal
    p.crew_max = max_c


func _build_packages(raw: Variant) -> void:
    packages.clear()
    packages_by_id.clear()
    packages_by_zone.clear()
    packages_synthesised = false
    if raw is Array and not (raw as Array).is_empty():
        for r in raw:
            var p := PackageData.from_dict(r)
            for tid in p.task_ids:
                if tasks_by_id.has(tid):
                    p.tasks.append(tasks_by_id[tid])
            packages.append(p)
            packages_by_id[p.package_id] = p
        # every task must belong to exactly one package, else fall back to synthesis
        var covered: Dictionary = {}
        for p in packages:
            for t in p.tasks:
                covered[t.task_id] = p.package_id
        if covered.size() == tasks.size():
            for p in packages:
                var zone: ZoneData = zones_by_id.get(p.zone_id, null)
                if zone != null:
                    var zmax: int = maxi(1, zone.max_crews)
                    p.crew_min = mini(p.crew_min, zmax)
                    p.crew_ideal = clampi(p.crew_ideal, p.crew_min, zmax)
                    p.crew_max = clampi(p.crew_max, p.crew_ideal, zmax)
            for t in tasks:
                t.package_id = covered[t.task_id]
        else:
            packages.clear()
            packages_by_id.clear()
    if packages.is_empty():
        _synthesise_packages()
    for p in packages:
        if not packages_by_zone.has(p.zone_id):
            packages_by_zone[p.zone_id] = [] as Array[PackageData]
        (packages_by_zone[p.zone_id] as Array[PackageData]).append(p)


func _group_key(t: TaskData) -> String:
    var st: StepDef = steps_by_id.get(t.step_id, null)
    var parts: PackedStringArray = []
    for g in packaging_group_by:
        match g:
            "zone_id":
                parts.append(t.zone_id)
            "phase":
                parts.append(t.phase)
            "trade":
                parts.append(t.trade)
            "work_face":
                parts.append(t.work_face)
            "discipline":
                parts.append(st.discipline if st != null else "general")
    return "|".join(parts)


## Groups tasks by packaging.group_by and splits groups above max_crew_days_per_package, with the
## deterministic ordering of docs/05 section 1.1 (storey index, zone id, phase order, trade, face, first task id).
func _synthesise_packages() -> void:
    packages_synthesised = true
    var groups: Dictionary = {}
    var order: Array[String] = []
    var sorted_tasks: Array[TaskData] = tasks.duplicate()
    sorted_tasks.sort_custom(func(a: TaskData, b: TaskData) -> bool:
        if a.planned_start_day != b.planned_start_day:
            return a.planned_start_day < b.planned_start_day
        return a.task_id < b.task_id)
    for t in sorted_tasks:
        var key: String = _group_key(t)
        if not groups.has(key):
            groups[key] = [] as Array[TaskData]
            order.append(key)
        (groups[key] as Array[TaskData]).append(t)
    var built: Array[PackageData] = []
    for key in order:
        var current: PackageData = null
        for t in groups[key]:
            var task: TaskData = t
            if current == null or (current.total_crew_days + task.estimated_crew_days > packaging_max_crew_days \
                    and not current.tasks.is_empty()):
                current = PackageData.new()
                current.synthesized = true
                current.zone_id = task.zone_id
                current.storey_id = task.storey_id
                current.phase = task.phase
                current.trade = task.trade
                current.work_face = task.work_face
                var st: StepDef = steps_by_id.get(task.step_id, null)
                current.discipline = st.discipline if st != null else "general"
                current.planned_start_day = task.planned_start_day
                current.planned_finish_day = task.planned_finish_day
                built.append(current)
            current.tasks.append(task)
            current.task_ids.append(task.task_id)
            current.total_crew_days += task.estimated_crew_days
            current.cost += task.cost
            current.planned_start_day = mini(current.planned_start_day, task.planned_start_day)
            current.planned_finish_day = maxi(current.planned_finish_day, task.planned_finish_day)
            current.requires_crane = current.requires_crane or task.requires_crane
            current.lead_time_weeks = maxi(current.lead_time_weeks, task.lead_time_weeks)
            current.laydown_cells = maxi(current.laydown_cells, task.laydown_cells)
    built.sort_custom(func(a: PackageData, b: PackageData) -> bool:
        var sa: int = int(storey_index_by_id.get(a.storey_id, 0))
        var sb: int = int(storey_index_by_id.get(b.storey_id, 0))
        if sa != sb:
            return sa < sb
        if a.zone_id != b.zone_id:
            return a.zone_id < b.zone_id
        var pa: int = order_of_phase(a.phase)
        var pb: int = order_of_phase(b.phase)
        if pa != pb:
            return pa < pb
        if a.trade != b.trade:
            return a.trade < b.trade
        if a.work_face != b.work_face:
            return a.work_face < b.work_face
        return a.task_ids[0] < b.task_ids[0])
    var n: int = 0
    for p in built:
        n += 1
        p.package_id = "P%05d" % n
        var ph_name: String = p.phase
        for ph in phases:
            if ph["id"] == p.phase:
                ph_name = str(ph["name"])
        p.name = "%s · %s · %s · %s" % [p.zone_id, ph_name, p.trade, p.work_face.replace("_", " ")]
        derive_profile(p)
        for t in p.tasks:
            t.package_id = p.package_id
        packages.append(p)
        packages_by_id[p.package_id] = p


func package_of(task: TaskData) -> PackageData:
    return packages_by_id.get(task.package_id, null)


func card_list() -> Array[SequenceCardData]:
    var out: Array[SequenceCardData] = []
    for id in card_ids:
        out.append(cards_by_id[id])
    return out


const SITE_MARGIN: int = 2


func _compute_site_rect() -> void:
    var min_c := Vector2i(0, 0)
    var max_c := Vector2i(width_cells - 1, depth_cells - 1)
    var all: Array[Vector2i] = []
    all.append_array(scenario.gates)
    all.append_array(scenario.occupied_cells)
    all.append_array(scenario.blocked_cells)
    for it in scenario.initial_tiles:
        all.append(it["cell"])
    for z in zones:
        all.append_array(z.cells)
        for c in z.cells:
            zone_cell_set[c] = true
    for c in all:
        min_c = Vector2i(mini(min_c.x, c.x), mini(min_c.y, c.y))
        max_c = Vector2i(maxi(max_c.x, c.x), maxi(max_c.y, c.y))
    min_c -= Vector2i(SITE_MARGIN, SITE_MARGIN)
    max_c += Vector2i(SITE_MARGIN, SITE_MARGIN)
    site_rect = Rect2i(min_c, max_c - min_c + Vector2i.ONE)


func in_site(c: Vector2i) -> bool:
    return site_rect.has_point(c)


# ---------------------------------------------------------------- derived values

func total_task_cost() -> float:
    var s: float = 0.0
    for t in tasks:
        s += t.cost
    return s


## Contract date in weeks (scenario.contract_weeks, or baseline weeks * contract_factor).
func contract_weeks() -> int:
    if scenario.contract_weeks > 0:
        return scenario.contract_weeks
    return maxi(1, ceili(float(baseline_finish_week) * scenario.contract_factor))


## Total contract value (scenario.budget, or task cost * budget_factor when budget is 0).
func contract_budget() -> float:
    if scenario.budget > 0.0:
        return scenario.budget
    return total_task_cost() * scenario.budget_factor


func storey_y(storey_id: String) -> float:
    return float(storey_index_by_id.get(storey_id, 0)) * storey_height_m / cell_size_m


func storey_y_for_index(index: int) -> float:
    return float(index) * storey_height_m / cell_size_m


func zones_covering(storey_id: String, cell: Vector2i) -> Array[ZoneData]:
    var out: Array[ZoneData] = []
    if zones_by_cell.has(storey_id):
        var per_cell: Dictionary = zones_by_cell[storey_id]
        if per_cell.has(cell):
            out.assign(per_cell[cell])
    return out


func step_of(task: TaskData) -> StepDef:
    return steps_by_id.get(task.step_id, null)


## Discipline of an element: first task's step discipline (first task wins).
func element_discipline(guid: String) -> String:
    var list: Array = tasks_by_element.get(guid, [])
    if list.is_empty():
        return "general"
    var st: StepDef = steps_by_id.get(list[0].step_id, null)
    return st.discipline if st != null else "general"


## Phase order of a phase id (unknown phases sort first).
func order_of_phase(phase: String) -> int:
    return int(phase_order.get(phase, -1))


## Scope instance key of a task for a gate scope: zone id, storey id or "*" (project).
static func gate_scope_key(gate: GateDef, task: TaskData) -> String:
    match gate.scope:
        "zone":
            return task.zone_id
        "storey":
            return task.storey_id
    return "*"


## Cumulative gate set: every task in the same scope instance as `task` whose phase order is
## <= order(gate.after_phase). Cached (the sets are static).
func gate_scope_tasks(gate: GateDef, task: TaskData) -> Array[TaskData]:
    var skey: String = gate_scope_key(gate, task)
    var key: String = "%s|%s" % [gate.id, skey]
    if not _gate_scope_cache.has(key):
        var after_o: int = order_of_phase(gate.after_phase)
        var out: Array[TaskData] = []
        var source: Array = tasks
        match gate.scope:
            "zone":
                source = tasks_by_zone.get(task.zone_id, [])
            "storey":
                source = tasks_by_storey.get(task.storey_id, [])
        for t in source:
            if order_of_phase((t as TaskData).phase) <= after_o:
                out.append(t)
        _gate_scope_cache[key] = out
    return _gate_scope_cache[key]


## Tasks held by a gate in the same scope instance as `task`: phase order >= order(before_phase). Cached.
func gate_held_tasks(gate: GateDef, task: TaskData) -> Array[TaskData]:
    var key: String = "H%s|%s" % [gate.id, gate_scope_key(gate, task)]
    if not _gate_scope_cache.has(key):
        var before_o: int = order_of_phase(gate.before_phase)
        var out: Array[TaskData] = []
        var source: Array = tasks
        match gate.scope:
            "zone":
                source = tasks_by_zone.get(task.zone_id, [])
            "storey":
                source = tasks_by_storey.get(task.storey_id, [])
        for t in source:
            if order_of_phase((t as TaskData).phase) >= before_o:
                out.append(t)
        _gate_scope_cache[key] = out
    return _gate_scope_cache[key]


func trade_ids() -> Array[String]:
    var out: Array[String] = []
    for t in trades:
        out.append(t.id)
    return out


func building_footprint_cells() -> Dictionary:
    ## Set (Vector2i -> true) of cells carrying BIM elements on storeys with index <= 0.
    var out: Dictionary = {}
    for e in elements:
        if int(storey_index_by_id.get(e.storey_id, 0)) <= 0:
            for c in e.cells:
                out[c] = true
    return out
