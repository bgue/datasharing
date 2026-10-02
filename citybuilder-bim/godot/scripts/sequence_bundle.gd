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
    valid = errors.is_empty()


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
