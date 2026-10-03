class_name SequenceBundle
extends RefCounted
## Parses a sequence.json game bundle into typed objects and builds the indices
## used by the simulation (see docs/03-architecture.md section 3).

var valid: bool = false
var errors: Array[String] = []
var source_path: String = ""
## Task descriptions a light row may leave out; the per-zone detail files of a split bundle carry them
## (`{"zone_id": id, "tasks": [{"task_id": ..., <these keys>}]}`, merged by `ensure_zone_detail`).
const DETAIL_FIELDS: Array[String] = ["ifc_class", "element_name", "system_id", "quantity", "unit", "rule_id", "note"]
## Task part files are read and parsed on worker threads (one per part) while the main thread builds the rest of the bundle.
static var parallel_parts: bool = true


## What a worker thread leaves behind for one part file.
class PartResult extends RefCounted:
    var data: Variant = null
    var ms: float = 0.0
    var task_id: int = -1


## Worker thread body: reads (and gunzips) a JSON file and parses it with a private JSON instance.
static func _load_part(path: String, res: PartResult) -> void:
    var t0: int = Time.get_ticks_usec()
    if FileAccess.file_exists(path):
        var raw: PackedByteArray = FileAccess.get_file_as_bytes(path)
        if raw.size() > 2 and raw[0] == 0x1f and raw[1] == 0x8b:
            raw = raw.decompress_dynamic(-1, FileAccess.COMPRESSION_GZIP)
        var j := JSON.new()
        if j.parse(raw.get_string_from_utf8()) == OK:
            res.data = j.data
    res.ms = float(Time.get_ticks_usec() - t0) / 1000.0


## Light task rows (a bundle with `zone_detail_dir` whose parts omit DETAIL_FIELDS) are marked `detail_loaded = false`
## only when they really lack those fields: the check is the `ifc_class` key of the row.
## Bundles with more tasks than this drop the per-task raw JSON dictionaries and the source dictionary after parsing
## (memory); exports are then built from the typed fields and `pristine_copy` re-reads the file.
const KEEP_RAW_MAX_TASKS: int = 4000
## `bundle_format` of the file (compressed, task_parts, zone_detail_dir) and the directory it was loaded from.
var format: Dictionary = {}
var base_dir: String = ""
var keep_raw: bool = true
## Load timing: {read_ms, parse_ms (JSON), build_ms (typed objects + indices), parts, tasks, bytes}.
var load_stats: Dictionary = {}
## project.areas: [{id, name, cells: Array[Vector2i], zone_ids: Array[String], storey_ids: Array[String], camera: Dictionary}].
var areas: Array[Dictionary] = []
var areas_by_id: Dictionary = {}
var _detail_loaded: Dictionary = {}  # zone id -> true

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

## Construction logic recipes embedded in the bundle (`recipes[]`); nothing is synthesised when absent.
var recipes: Array[RecipeData] = []
var recipes_by_id: Dictionary = {}  # recipe id -> RecipeData
## The `manual` block (zones in manual mode, authored tasks, overrides, applied recipes), null when absent.
var manual: ManualSequenceData = null
## The parsed dictionary, kept so a bundle edited at runtime (manual tasks) can be re-parsed pristine.
var source_dict: Dictionary = {}
## True once manual tasks / links changed the task graph at runtime (SimState.start re-parses before reusing it).
var edited: bool = false
var virtual_tasks: Array[TaskData] = []
var manual_alias: Dictionary = {}  # manual id (M0001) -> task id
## Step definitions added at runtime (inline `step_def` of manual tasks / recipes), kept for saves.
var added_steps: Array[Dictionary] = []
var _next_package_num: int = 1

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
var _gate_scope_cache: Dictionary = {}
var _start_links: Dictionary = {}  # task id -> Array[String]: successors linked SS / FF (they care when it starts)
var _start_links_valid: bool = false
var _cost_cache: float = -1.0
var _cost_cache_n: int = -1  # "gate|scope key" -> Array[TaskData]


## Loads a bundle from `.json` or `.json.gz` (gzip detected by the magic bytes): split task parts and the lazy
## per-zone detail directory named by `bundle_format` are resolved relative to the bundle's directory.
static func load_from_path(path: String) -> SequenceBundle:
    var b := SequenceBundle.new()
    b.source_path = path
    b.base_dir = path.get_base_dir()
    if not FileAccess.file_exists(path):
        b.errors.append("file not found: %s" % path)
        return b
    var t0: int = Time.get_ticks_usec()
    var raw: PackedByteArray = FileAccess.get_file_as_bytes(path)
    var t1: int = Time.get_ticks_usec()
    var parsed: Variant = parse_bytes(raw)
    var t2: int = Time.get_ticks_usec()
    if not (parsed is Dictionary):
        b.errors.append("not a JSON object: %s" % path)
        return b
    b.load_stats = {"bytes": raw.size(), "read_ms": float(t1 - t0) / 1000.0, "parse_ms": float(t2 - t1) / 1000.0,
            "parts": 0, "tasks": 0}
    b.parse(parsed)
    b.load_stats["build_ms"] = float(Time.get_ticks_usec() - t2) / 1000.0 - float(b.load_stats.get("part_ms", 0.0))
    b.load_stats["total_ms"] = float(Time.get_ticks_usec() - t0) / 1000.0
    b.load_stats["tasks"] = b.tasks.size()
    return b


## JSON text or gzip-compressed JSON bytes -> Variant (null when the content is not JSON).
static func parse_bytes(raw: PackedByteArray) -> Variant:
    if raw.size() > 2 and raw[0] == 0x1f and raw[1] == 0x8b:
        raw = raw.decompress_dynamic(-1, FileAccess.COMPRESSION_GZIP)
    return JSON.parse_string(raw.get_string_from_utf8())


## Reads a `.json` / `.json.gz` file (null when missing or not JSON).
static func read_json(path: String) -> Variant:
    if not FileAccess.file_exists(path):
        return null
    return parse_bytes(FileAccess.get_file_as_bytes(path))


## Path of a bundle file inside a scenario folder: sequence.json, else sequence.json.gz ("" when neither exists).
static func find_in_dir(dir: String) -> String:
    for n in ["sequence.json", "sequence.json.gz"]:
        var p: String = dir.path_join(n)
        if FileAccess.file_exists(p):
            return p
    return ""


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

    source_dict = d
    var bf: Variant = d.get("bundle_format", {})
    format = bf if bf is Dictionary else {}
    var part_files: Array = format.get("task_parts", [])
    var part_jobs: Array[PartResult] = []
    for pf in part_files:  # start reading the parts now: they parse while the elements, zones and steps are built below
        var job := PartResult.new()
        var path: String = base_dir.path_join(str(pf))
        if parallel_parts and part_files.size() > 1:
            job.task_id = WorkerThreadPool.add_task(Callable(SequenceBundle, "_load_part").bind(path, job), false, "bundle part")
        part_jobs.append(job)
    var inline_tasks: Array = d["tasks"]
    keep_raw = inline_tasks.size() <= KEEP_RAW_MAX_TASKS and part_files.is_empty()
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

    var rl: Variant = d.get("recipes", [])
    if rl is Array:
        for r in rl:
            if r is Dictionary:
                var rd := RecipeData.from_dict(r)
                if rd.id != "":
                    recipes.append(rd)
                    recipes_by_id[rd.id] = rd
    var mn: Variant = d.get("manual", null)
    if mn is Dictionary:
        manual = ManualSequenceData.from_dict(mn)

    _ingest_tasks(inline_tasks)
    var tp0: int = Time.get_ticks_usec()
    var part_wait_us: int = 0
    var part_cpu_ms: float = 0.0
    for k in part_files.size():
        var job: PartResult = part_jobs[k]
        var tj: int = Time.get_ticks_usec()
        if job.task_id >= 0:
            WorkerThreadPool.wait_for_task_completion(job.task_id)
        else:
            _load_part(base_dir.path_join(str(part_files[k])), job)
        part_wait_us += Time.get_ticks_usec() - tj
        part_cpu_ms += job.ms
        var rows: Variant = job.data
        job.data = null
        if rows is Dictionary:  # {"schema_version", "part", "tasks": [...]} as the pipeline writes them
            rows = (rows as Dictionary).get("tasks", null)
        if not (rows is Array):
            errors.append("task part missing or not an array: %s" % str(part_files[k]))
            continue
        _ingest_tasks(rows)
        load_stats["parts"] = int(load_stats.get("parts", 0)) + 1
    load_stats["part_ms"] = float(Time.get_ticks_usec() - tp0) / 1000.0
    load_stats["part_wait_ms"] = float(part_wait_us) / 1000.0  # main thread waiting for part files to parse
    load_stats["part_json_ms"] = part_cpu_ms  # CPU time of the part reads (all threads together)
    if str(format.get("zone_detail_dir", "")) != "":
        for task in tasks:
            task.detail_loaded = task.ifc_class != ""
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
    _parse_areas(proj)
    if not keep_raw and source_path != "":
        source_dict = {}  # pristine_copy() re-reads source_path
    valid = errors.is_empty()


## Typed TaskData rows for a list of task dictionaries (the inline `tasks` or one part file).
func _ingest_tasks(rows: Array) -> void:
    for t in rows:
        var task := TaskData.from_dict(t, keep_raw)
        if manual != null and task.origin == "manual" and task.manual_id != "" and not task.is_virtual:
            # a manual task binds to every element of its manual-block entry (the task row names the first one)
            var els: Array = manual.task_by_id(task.manual_id).get("elements", [])
            if not els.is_empty():
                task.element_guids.clear()
                for g in els:
                    task.element_guids.append(str(g))
                task.element_guid = task.element_guids[0]
        tasks.append(task)
        _index_task(task)


func _parse_areas(proj: Dictionary) -> void:
    areas.clear()
    areas_by_id.clear()
    var raw_areas: Variant = proj.get("areas", [])
    if not (raw_areas is Array):
        return
    for a in raw_areas:
        if not (a is Dictionary):
            continue
        var ad: Dictionary = a
        var id: String = str(ad.get("id", ""))
        if id == "":
            continue
        var area: Dictionary = {"id": id, "name": str(ad.get("name", id)), "cells": ZoneData.cells_from_variant(ad.get("cells", [])),
                "zone_ids": [] as Array[String], "storey_ids": [] as Array[String], "camera": {}}
        for sid in ad.get("storey_ids", []):
            (area["storey_ids"] as Array[String]).append(str(sid))
        var cam: Variant = ad.get("camera_bookmark", {})
        if cam is Dictionary:
            area["camera"] = cam
        var explicit: Variant = ad.get("zone_ids", null)
        if explicit is Array:  # the bundle names the zones of the area: use them as given
            for zid in explicit:
                if zones_by_id.has(str(zid)):
                    (area["zone_ids"] as Array[String]).append(str(zid))
            if (area["cells"] as Array).is_empty():
                for zid in area["zone_ids"]:
                    (area["cells"] as Array).append_array((zones_by_id[zid] as ZoneData).cells)
        else:
            # the zones whose centre cell lies in the area (on its storeys)
            var cell_set: Dictionary = {}
            for c in area["cells"]:
                cell_set[c] = true
            for z in zones:
                if not (area["storey_ids"] as Array).is_empty() and not (area["storey_ids"] as Array).has(z.storey_id):
                    continue
                if not cell_set.is_empty() and cell_set.has(z.centre_cell()):
                    (area["zone_ids"] as Array[String]).append(z.id)
            if (area["cells"] as Array).is_empty():  # no cells given: the area is its storeys (or the whole site)
                for z in zones:
                    if (area["storey_ids"] as Array).is_empty() or (area["storey_ids"] as Array).has(z.storey_id):
                        (area["zone_ids"] as Array[String]).append(z.id)
                for z in zones:
                    if (area["zone_ids"] as Array).has(z.id):
                        (area["cells"] as Array).append_array(z.cells)
        areas.append(area)
        areas_by_id[id] = area


## True when the bundle keeps per-zone task detail in separate files that are merged on first access.
func has_lazy_detail() -> bool:
    return str(format.get("zone_detail_dir", "")) != ""


## Merges the detail file of a zone the first time the zone is asked for: descriptive fields of its tasks (`tasks`
## rows, see DETAIL_FIELDS) and the member guid lists of its aggregate elements (`members`); returns the number of
## tasks / aggregates updated. A no-op for bundles without `zone_detail_dir` or when the zone was loaded already.
func ensure_zone_detail(zone_id: String) -> int:
    if _detail_loaded.has(zone_id) or not has_lazy_detail():
        return 0
    _detail_loaded[zone_id] = true
    var dir: String = base_dir.path_join(str(format["zone_detail_dir"]))
    var doc: Variant = null
    for ext in [".json.gz", ".json"]:
        var p: String = dir.path_join(zone_id + ext)
        if FileAccess.file_exists(p):
            doc = read_json(p)
            break
    var n: int = 0
    if doc is Dictionary:
        for row in (doc as Dictionary).get("tasks", []):
            var t: TaskData = tasks_by_id.get(str((row as Dictionary).get("task_id", "")), null)
            if t != null:
                t.merge_detail(row)
                n += 1
        # aggregate elements: member guid lists (`members`: element guid -> [guid, ...])
        var members: Variant = (doc as Dictionary).get("members", {})
        if members is Dictionary:
            for agg in members:
                var el: ElementData = elements_by_guid.get(str(agg), null)
                if el != null and el.member_guids.is_empty():
                    for g in members[agg]:
                        el.member_guids.append(str(g))
                    n += 1
    for t in tasks_by_zone.get(zone_id, []):
        (t as TaskData).detail_loaded = true
    return n


func zone_detail_loaded(zone_id: String) -> bool:
    return _detail_loaded.has(zone_id) or not has_lazy_detail()


## Adds a task to every lookup index (not to `tasks`). Virtual tasks have no element and are not indexed by element.
func _index_task(task: TaskData) -> void:
    tasks_by_id[task.task_id] = task
    if task.manual_id != "":
        manual_alias[task.manual_id] = task.task_id
    if not tasks_by_zone.has(task.zone_id):
        tasks_by_zone[task.zone_id] = [] as Array[TaskData]
    (tasks_by_zone[task.zone_id] as Array[TaskData]).append(task)
    if not task.is_virtual:
        for g in task.element_guids:
            if not tasks_by_element.has(g):
                tasks_by_element[g] = [] as Array[TaskData]
            (tasks_by_element[g] as Array[TaskData]).append(task)
    else:
        virtual_tasks.append(task)
    if not tasks_by_storey.has(task.storey_id):
        tasks_by_storey[task.storey_id] = [] as Array[TaskData]
    (tasks_by_storey[task.storey_id] as Array[TaskData]).append(task)
    if not tasks_by_phase.has(task.phase):
        tasks_by_phase[task.phase] = [] as Array[TaskData]
    (tasks_by_phase[task.phase] as Array[TaskData]).append(task)
    successors_by_task[task.task_id] = [] as Array[String]


## Successors of a task that depend on its start (an SS or FF link): the only ones a task starting can release.
## Built on first use from the predecessor lists; dropped by invalidate_start_links() (edits, full readiness passes).
func start_successors(task_id: String) -> Array:
    if not _start_links_valid:
        _start_links.clear()
        for t in tasks:
            for p in t.predecessors:
                var ty: String = str(p["type"])
                if ty == "SS" or ty == "FF":
                    var pid: String = str(p["task_id"])
                    if not _start_links.has(pid):
                        _start_links[pid] = []
                    (_start_links[pid] as Array).append(t.task_id)
        _start_links_valid = true
    return _start_links.get(task_id, [])


func invalidate_start_links() -> void:
    _start_links_valid = false


## Resolves a task id or a manual id (M0001) to the task id, "" when unknown.
func resolve_task_id(id: String) -> String:
    if tasks_by_id.has(id):
        return id
    return str(manual_alias.get(id, ""))


## A pristine copy of this bundle re-parsed from the source dictionary (drops every runtime edit).
func pristine_copy() -> SequenceBundle:
    if source_dict.is_empty() and source_path != "":
        return SequenceBundle.load_from_path(source_path)
    var b := SequenceBundle.from_dictionary(source_dict)
    b.source_path = source_path
    b.base_dir = base_dir
    return b


func add_step(st: StepDef, raw_def: Dictionary = {}) -> void:
    if not steps_by_id.has(st.id):
        steps.append(st)
        if not raw_def.is_empty():
            added_steps.append(raw_def.duplicate(true))
        edited = true
    steps_by_id[st.id] = st


func invalidate_gate_caches() -> void:
    _start_links_valid = false
    _gate_scope_cache.clear()
    _cost_cache = -1.0


## Adds a task at runtime: appends it, updates every index, links it as successor of its predecessors and
## invalidates the gate caches. Package assignment is separate (assign_package).
func add_task(task: TaskData) -> void:
    tasks.append(task)
    _index_task(task)
    for p in task.predecessors:
        var pid: String = str(p["task_id"])
        if successors_by_task.has(pid):
            (successors_by_task[pid] as Array[String]).append(task.task_id)
    invalidate_gate_caches()
    edited = true


## Removes a task from every index and its package (an emptied package is dropped). Links in other tasks'
## predecessor lists are NOT touched (the caller bridges or drops them first).
func remove_task(task: TaskData) -> void:
    tasks.erase(task)
    tasks_by_id.erase(task.task_id)
    if task.manual_id != "" and str(manual_alias.get(task.manual_id, "")) == task.task_id:
        manual_alias.erase(task.manual_id)
    (tasks_by_zone.get(task.zone_id, []) as Array).erase(task)
    for g in task.element_guids:
        (tasks_by_element.get(g, []) as Array).erase(task)
    (tasks_by_storey.get(task.storey_id, []) as Array).erase(task)
    (tasks_by_phase.get(task.phase, []) as Array).erase(task)
    virtual_tasks.erase(task)
    for p in task.predecessors:
        var plist: Variant = successors_by_task.get(str(p["task_id"]), null)
        if plist is Array:
            (plist as Array).erase(task.task_id)
    successors_by_task.erase(task.task_id)
    unassign_package(task)
    invalidate_gate_caches()
    edited = true


## Recomputes every successor list from the predecessor links (after bulk edits).
func rebuild_successors() -> void:
    for t in tasks:
        successors_by_task[t.task_id] = [] as Array[String]
    for t in tasks:
        for p in t.predecessors:
            var sl: Variant = successors_by_task.get(str(p["task_id"]), null)
            if sl is Array and not (sl as Array).has(t.task_id):
                (sl as Array).append(t.task_id)
    invalidate_gate_caches()


func add_link(succ: TaskData, pred_id: String, type: String, lag_days: int) -> void:
    succ.predecessors.append({"task_id": pred_id, "type": type, "lag_days": lag_days})
    var sl: Array[String] = successors_by_task[pred_id]
    if not sl.has(succ.task_id):
        sl.append(succ.task_id)
    invalidate_gate_caches()
    edited = true


func remove_link(succ: TaskData, pred_id: String) -> void:
    for i in range(succ.predecessors.size() - 1, -1, -1):
        if str(succ.predecessors[i]["task_id"]) == pred_id:
            succ.predecessors.remove_at(i)
    (successors_by_task[pred_id] as Array[String]).erase(succ.task_id)
    invalidate_gate_caches()
    edited = true


func _key_of(zone_id: String, phase: String, trade: String, face: String, discipline: String, authored: bool) -> String:
    var parts: PackedStringArray = []
    for g in packaging_group_by:
        match g:
            "zone_id":
                parts.append(zone_id)
            "phase":
                parts.append(phase)
            "trade":
                parts.append(trade)
            "work_face":
                parts.append(face)
            "discipline":
                parts.append(discipline)
    if authored:
        parts.append("manual")
    return "|".join(parts)


func next_package_id() -> String:
    var id: String = "P%05d" % _next_package_num
    _next_package_num += 1
    return id


## Puts a task into the authored package of its zone / phase / trade / face (a new one when there is none or it is
## full; same grouping as the synthesis). Authored packages are separate from the generated ones.
func assign_package(task: TaskData, package_id: String = "") -> PackageData:
    var st: StepDef = steps_by_id.get(task.step_id, null)
    var disc: String = st.discipline if st != null else "general"
    var authored: bool = task.is_authored()
    var key: String = _key_of(task.zone_id, task.phase, task.trade, task.work_face, disc, authored)
    var target: PackageData = null
    if package_id != "" and packages_by_id.has(package_id):
        target = packages_by_id[package_id]
    else:
        for p in packages_by_zone.get(task.zone_id, []):
            var pk: PackageData = p
            if pk.manual != authored or _key_of(pk.zone_id, pk.phase, pk.trade, pk.work_face, pk.discipline, pk.manual) != key:
                continue
            if pk.tasks.is_empty() or pk.total_crew_days + task.estimated_crew_days <= packaging_max_crew_days:
                target = pk
                break
    if target == null:
        target = PackageData.new()
        if package_id != "":
            target.package_id = package_id
            if package_id.substr(1).is_valid_int():
                _next_package_num = maxi(_next_package_num, package_id.substr(1).to_int() + 1)
        else:
            target.package_id = next_package_id()
        target.synthesized = true
        target.manual = authored
        target.zone_id = task.zone_id
        target.storey_id = task.storey_id
        target.phase = task.phase
        target.trade = task.trade
        target.work_face = task.work_face
        target.discipline = disc
        target.planned_start_day = task.planned_start_day
        target.planned_finish_day = task.planned_finish_day
        var ph_name: String = task.phase
        for ph in phases:
            if ph["id"] == task.phase:
                ph_name = str(ph["name"])
        target.name = "%s - %s - %s - %s%s" % [task.zone_id, ph_name, task.trade, task.work_face.replace("_", " "),
                " (manual)" if authored else ""]
        packages.append(target)
        packages_by_id[target.package_id] = target
        if not packages_by_zone.has(target.zone_id):
            packages_by_zone[target.zone_id] = [] as Array[PackageData]
        (packages_by_zone[target.zone_id] as Array[PackageData]).append(target)
    target.tasks.append(task)
    target.task_ids.append(task.task_id)
    task.package_id = target.package_id
    refresh_package(target)
    return target


## Removes the task from its package; an emptied package is dropped. Returns the package (still known or not).
func unassign_package(task: TaskData) -> PackageData:
    var p: PackageData = packages_by_id.get(task.package_id, null)
    if p == null:
        return null
    p.tasks.erase(task)
    p.task_ids.erase(task.task_id)
    if p.tasks.is_empty():
        packages.erase(p)
        packages_by_id.erase(p.package_id)
        (packages_by_zone.get(p.zone_id, []) as Array).erase(p)
    else:
        refresh_package(p)
    return p


## Recomputes totals, planned span and (for authored / synthesised packages) the crew profile from the members.
func refresh_package(p: PackageData) -> void:
    p.total_crew_days = 0.0
    p.cost = 0.0
    p.requires_crane = false
    p.lead_time_weeks = 0
    p.laydown_cells = 0
    var first: bool = true
    for t in p.tasks:
        p.total_crew_days += t.estimated_crew_days
        p.cost += t.cost
        p.requires_crane = p.requires_crane or t.requires_crane
        p.lead_time_weeks = maxi(p.lead_time_weeks, t.lead_time_weeks)
        p.laydown_cells = maxi(p.laydown_cells, t.laydown_cells)
        if first:
            p.planned_start_day = t.planned_start_day
            p.planned_finish_day = t.planned_finish_day
            first = false
        else:
            p.planned_start_day = mini(p.planned_start_day, t.planned_start_day)
            p.planned_finish_day = maxi(p.planned_finish_day, t.planned_finish_day)
    if p.manual or p.synthesized:
        derive_profile(p)


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
    if not p.tasks.is_empty() and _all_duration_driven(p):
        p.crew_min = 1
        p.crew_ideal = 1
        p.crew_max = 1
        return
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


## Time-driven packages (every task has duration_days: surveys, dewatering, permits) need exactly one crew.
func _all_duration_driven(p: PackageData) -> bool:
    for t in p.tasks:
        if not t.is_duration_driven():
            return false
    return true


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
            for p in packages:
                p.manual = not p.tasks.is_empty() and _all_authored(p)
                if not p.tasks.is_empty() and _all_duration_driven(p):
                    p.crew_min = 1
                    p.crew_ideal = 1
                    p.crew_max = 1
        else:
            packages.clear()
            packages_by_id.clear()
    if packages.is_empty():
        _synthesise_packages()
    _next_package_num = 1
    for p in packages:
        if not packages_by_zone.has(p.zone_id):
            packages_by_zone[p.zone_id] = [] as Array[PackageData]
        (packages_by_zone[p.zone_id] as Array[PackageData]).append(p)
        if p.package_id.length() > 1 and p.package_id.substr(1).is_valid_int():
            _next_package_num = maxi(_next_package_num, p.package_id.substr(1).to_int() + 1)


func _all_authored(p: PackageData) -> bool:
    for t in p.tasks:
        if not t.is_authored():
            return false
    return true


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
    if t.is_authored():
        parts.append("manual")
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
                current.manual = task.is_authored()
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
    if _cost_cache_n != tasks.size() or _cost_cache < 0.0:
        var s: float = 0.0
        for t in tasks:
            s += t.cost
        _cost_cache = s
        _cost_cache_n = tasks.size()
    return _cost_cache


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
