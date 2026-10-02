class_name Manual
extends RefCounted
## Manual sequencing (docs/06 track A.3): zones in manual mode, hand-authored and virtual tasks, links, recipe
## expansion at runtime and the manual_sequence.json export. Static helpers over SimState; every mutation goes
## through SimState.register_task / unregister_task / graph_changed so readiness, gates, packages and the
## indices stay consistent. Methods return false / "" / {} and set `gs.last_error` on failure.

const LINK_TYPES: Array[String] = ["FS", "SS", "FF"]
const UNIT_OF_BASIS: Dictionary = {"volume_m3": "m3", "area_m2": "m2", "length_m": "m", "count": "ea", "weight_t": "t"}
const MAX_LINK_FANOUT: int = 20
const MAX_RECIPE_ELEMENTS: int = 50
## Element visuals / classes counted as a foundation for `from_element: foundation`.
const FOUNDATION_VISUALS: Array[String] = ["footing", "pile", "earthwork", "slab", "pavement"]
const FOUNDATION_CLASSES: Array[String] = ["IfcFooting", "IfcPile", "IfcSlab", "IfcEarthworks", "IfcGeographicElement"]


# ----------------------------------------------------------------- ids

## First free runtime id number after every manual id and every M-prefixed task id of the bundle.
static func first_free_id(b: SequenceBundle) -> int:
    var n: int = 0
    for t in b.tasks:
        for id in [t.manual_id, t.task_id]:
            var sid: String = id
            if sid.length() > 1 and sid.begins_with("M") and sid.substr(1).is_valid_int():
                n = maxi(n, sid.substr(1).to_int())
    if b.manual != null:
        for mt in b.manual.tasks:
            var sid2: String = str(mt["id"])
            if sid2.length() > 1 and sid2.begins_with("M") and sid2.substr(1).is_valid_int():
                n = maxi(n, sid2.substr(1).to_int())
    return n + 1


static func _new_id(gs: SimState) -> String:
    var id: String = "M%06d" % gs.manual_next_id
    gs.manual_next_id += 1
    return id


static func _fail(gs: SimState, msg: String) -> bool:
    gs.last_error = msg
    return false


# ----------------------------------------------------------------- manual mode

static func is_manual(gs: SimState, zone_id: String) -> bool:
    return gs.manual_zones.has(zone_id)


## Switches a zone's manual mode. On: its generated packages are frozen (held, no work, tasks excluded from
## readiness, gates and completion) and only authored tasks run; off: they are released as before.
static func set_mode(gs: SimState, zone_id: String, on: bool) -> bool:
    if not gs.bundle.zones_by_id.has(zone_id):
        return _fail(gs, "no such zone: %s" % zone_id)
    if on:
        gs.manual_zones[zone_id] = true
    else:
        gs.manual_zones.erase(zone_id)
    gs.apply_manual_flags()
    gs.graph_changed()
    gs.manual_changed.emit()
    return true


# ----------------------------------------------------------------- task creation

static func _states_not_started(gs: SimState, task: TaskData) -> bool:
    var st: int = (gs.runtime[task.task_id] as TaskRuntime).state
    return st == TaskRuntime.State.NOT_STARTED or st == TaskRuntime.State.READY or st == TaskRuntime.State.BLOCKED


## Quantity of the generated tasks of the same step on these elements (so a manual task replacing / rebuilding them
## keeps the BIM quantity), else the element count.
static func _default_quantity(gs: SimState, step_id: String, elements: Array[String]) -> float:
    var sum: float = 0.0
    var found: bool = false
    for g in elements:
        for t in gs.bundle.tasks_by_element.get(g, []):
            var task: TaskData = t
            if task.step_id == step_id and not task.is_authored():
                sum += task.quantity
                found = true
                break
    return sum if found else float(maxi(1, elements.size()))


static func _parse_after(gs: SimState, spec: Dictionary, own_id: String = "") -> Variant:
    ## Returns Array of {task_id, type, lag_days} or null (last_error set).
    var out: Array[Dictionary] = []
    var def_type: String = str(spec.get("link_type", "FS"))
    var def_lag: int = int(spec.get("lag_days", 0))
    if not LINK_TYPES.has(def_type):
        gs.last_error = "link_type must be FS, SS or FF"
        return null
    var raw: Variant = spec.get("after", [])
    if raw is String:
        raw = [raw]
    if not (raw is Array):
        gs.last_error = "after must be an array of task ids"
        return null
    for a in raw:
        var tid: String = ""
        var ltype: String = def_type
        var lag: int = def_lag
        if a is Dictionary:
            var ad: Dictionary = a
            tid = str(ad.get("task_id", ad.get("id", "")))
            ltype = str(ad.get("type", def_type))
            lag = int(ad.get("lag_days", def_lag))
        else:
            tid = str(a)
        var resolved: String = gs.bundle.resolve_task_id(tid)
        if resolved == "" or resolved == own_id:
            gs.last_error = "unknown predecessor task: %s" % tid
            return null
        if not LINK_TYPES.has(ltype):
            gs.last_error = "link type must be FS, SS or FF"
            return null
        out.append({"task_id": resolved, "type": ltype, "lag_days": lag})
    return out


## Fills the derived fields of a task from its step, zone and elements (not the id, links or package).
static func _fill_task(gs: SimState, t: TaskData, st: StepDef, zone: ZoneData, quantity: float, has_quantity: bool) -> void:
    var b: SequenceBundle = gs.bundle
    t.step_id = st.id
    t.phase = st.phase
    t.trade = st.trade
    t.zone_id = zone.id
    t.storey_id = zone.storey_id
    t.work_face = st.work_face
    t.unit = str(UNIT_OF_BASIS.get(st.quantity_basis, "ea"))
    t.requires_crane = st.requires_crane
    t.requires_access = st.requires_access
    t.laydown_cells = st.laydown_cells
    t.lead_time_weeks = st.lead_time_weeks
    t.risk = st.risk
    t.weather_sensitive = st.weather_sensitive
    t.noisy = st.noisy
    t.dusty = st.dusty
    t.inspection = st.inspection
    t.inspection_type = st.inspection_type
    if t.is_virtual:
        t.element_guid = ""
        t.ifc_class = ""
        t.system_id = ""
        t.cells = [zone.centre_cell()]
    else:
        t.element_guid = t.element_guids[0] if not t.element_guids.is_empty() else ""
        var first: ElementData = b.elements_by_guid.get(t.element_guid, null)
        t.ifc_class = first.ifc_class if first != null else ""
        t.system_id = first.system_id if first != null else ""
        var cells: Array[Vector2i] = []
        for g in t.element_guids:
            var e: ElementData = b.elements_by_guid.get(g, null)
            if e != null:
                for c in e.cells:
                    if not cells.has(c):
                        cells.append(c)
        if cells.is_empty():
            cells.append(zone.centre_cell())
        t.cells = cells
    if has_quantity:
        t.quantity = maxf(0.0, quantity)
    if t.duration_days > 0:
        t.estimated_crew_days = float(t.duration_days)
    else:
        t.estimated_crew_days = snappedf(t.quantity / maxf(st.rate_per_crew_day, 0.0001), 0.0001)
    t.cost = snappedf(t.quantity * st.unit_cost, 0.01)


## Builds (not registers) a task from an add_task spec. Returns null with `gs.last_error` set.
static func build_task(gs: SimState, spec: Dictionary) -> TaskData:
    var b: SequenceBundle = gs.bundle
    var step_id: String = str(spec.get("step", ""))
    var inline: Variant = spec.get("step_def", null)
    if inline is Dictionary:
        var sd: Dictionary = (inline as Dictionary).duplicate()
        if not sd.has("id"):
            sd["id"] = step_id
        if str(sd["id"]) != "" and not b.steps_by_id.has(str(sd["id"])):
            b.add_step(StepDef.from_dict(sd), sd)
        step_id = str(sd["id"])
    if step_id == "":
        gs.last_error = "missing parameter: step"
        return null
    var st: StepDef = b.steps_by_id.get(step_id, null)
    if st == null:
        gs.last_error = "unknown step: %s" % step_id
        return null
    if not b.trades_by_id.has(st.trade):
        gs.last_error = "step %s uses trade %s which is not a trade of this scenario" % [step_id, st.trade]
        return null
    var elements: Array[String] = []
    var el_raw: Variant = spec.get("elements", [])
    if el_raw is Array:
        for g in el_raw:
            var guid: String = str(g)
            if not b.elements_by_guid.has(guid):
                gs.last_error = "unknown element: %s" % guid
                return null
            if not elements.has(guid):
                elements.append(guid)
    var zone_id: String = str(spec.get("zone_id", ""))
    if zone_id == "" and not elements.is_empty():
        zone_id = (b.elements_by_guid[elements[0]] as ElementData).zone_id
    var zone: ZoneData = b.zones_by_id.get(zone_id, null)
    if zone == null:
        gs.last_error = "no such zone: %s" % zone_id
        return null
    var is_virt: bool = bool(spec.get("virtual", false))
    if not is_virt and elements.is_empty():
        gs.last_error = "a task needs elements, or virtual=true"
        return null
    var t := TaskData.new()
    t.task_id = str(spec.get("task_id", ""))
    if t.task_id == "":
        t.task_id = _new_id(gs)
    t.manual_id = str(spec.get("manual_id", t.task_id))
    t.is_virtual = is_virt
    t.origin = str(spec.get("origin", "manual"))
    t.recipe_id = str(spec.get("recipe_id", ""))
    t.marker = str(spec.get("marker", ""))
    t.note = str(spec.get("note", ""))
    t.rule_id = "R-manual" if t.origin == "manual" else "R-recipe"
    t.runtime_added = true
    t.element_guids = elements
    var dd: Variant = spec.get("duration_days", null)
    t.duration_days = 0 if dd == null else maxi(0, int(dd))
    var has_q: bool = spec.has("quantity") and spec["quantity"] != null
    if is_virt and t.duration_days == 0 and not has_q:
        t.duration_days = maxi(1, st.min_duration_days)
    var q: float = float(spec["quantity"]) if has_q else (1.0 if is_virt else _default_quantity(gs, step_id, elements))
    _fill_task(gs, t, st, zone, q, true)
    if spec.has("unit") and str(spec["unit"]) != "":
        t.unit = str(spec["unit"])
    for flag in ["requires_access", "requires_crane"]:
        if spec.has(flag):
            t.set(flag, bool(spec[flag]))
    if spec.has("hold_point") and spec["hold_point"] != null and str(spec["hold_point"]) != "":
        t.inspection = true
        t.inspection_type = str(spec["hold_point"])
    var nm: String = str(spec.get("name", ""))
    if nm != "":
        t.element_name = nm
    elif is_virt or elements.is_empty():
        t.element_name = st.name
    else:
        var first: ElementData = b.elements_by_guid[elements[0]]
        t.element_name = first.name + (" +%d" % (elements.size() - 1) if elements.size() > 1 else "")
    var preds: Variant = _parse_after(gs, spec, t.task_id)
    if preds == null:
        return null
    for p in preds:
        t.predecessors.append(p)
    plan_dates(gs, t)
    return t


## Creates a task (elements and / or virtual) and returns its id, "" on error. Spec keys: step, zone_id, elements[],
## virtual, quantity, unit, duration_days, after[] (task ids or {task_id, type, lag_days}), link_type, lag_days,
## marker, name, note, hold_point, recipe_id, origin, step_def (inline step).
static func add_task(gs: SimState, spec: Dictionary) -> String:
    var t: TaskData = build_task(gs, spec)
    if t == null:
        return ""
    if not gs.register_task(t):
        return ""
    return t.task_id


# ----------------------------------------------------------------- planned dates

static func _duration_of(gs: SimState, t: TaskData) -> int:
    var st: StepDef = gs.bundle.steps_by_id.get(t.step_id, null)
    if t.duration_days > 0:
        return t.duration_days
    return maxi(maxi(st.min_duration_days if st != null else 1, ceili(t.estimated_crew_days - 0.000001)), 1)


## Planned start / finish (working days, finish exclusive) of an authored task from its predecessors, never before today.
static func plan_dates(gs: SimState, t: TaskData) -> void:
    var floor_day: int = gs.current_day()
    var dur: int = _duration_of(gs, t)
    var start: int = floor_day
    for p in t.predecessors:
        var pt: TaskData = gs.bundle.tasks_by_id.get(str(p["task_id"]), null)
        if pt == null:
            continue
        var lag: int = int(p["lag_days"])
        match str(p["type"]):
            "SS":
                start = maxi(start, pt.planned_start_day + lag)
            "FF":
                start = maxi(start, pt.planned_finish_day + lag - dur)
            _:
                start = maxi(start, pt.planned_finish_day + lag)
    t.planned_start_day = start
    t.planned_finish_day = start + dur


## Re-plans every not-started authored task in dependency order and refreshes their packages.
static func replan(gs: SimState) -> void:
    var done: Dictionary = {}
    var touched: Dictionary = {}
    for t in gs.bundle.tasks:
        if t.is_authored():
            _replan_visit(gs, t, done, touched, 0)
    for pid in touched:
        var p: PackageData = gs.bundle.packages_by_id.get(pid, null)
        if p != null:
            gs.bundle.refresh_package(p)


static func _replan_visit(gs: SimState, t: TaskData, done: Dictionary, touched: Dictionary, depth: int) -> void:
    if done.has(t.task_id) or depth > 5000:
        return
    done[t.task_id] = true
    for p in t.predecessors:
        var pt: TaskData = gs.bundle.tasks_by_id.get(str(p["task_id"]), null)
        if pt != null and pt.is_authored():
            _replan_visit(gs, pt, done, touched, depth + 1)
    if gs.runtime.has(t.task_id) and _states_not_started(gs, t):
        plan_dates(gs, t)
        touched[t.package_id] = true


# ----------------------------------------------------------------- update / remove

static func _resolve_authored(gs: SimState, id: String, what: String) -> TaskData:
    var rid: String = gs.bundle.resolve_task_id(id)
    var t: TaskData = gs.bundle.tasks_by_id.get(rid, null)
    if t == null:
        gs.last_error = "no such task: %s" % id
        return null
    if not t.is_authored():
        gs.last_error = "%s: task %s is generated (only manual / recipe tasks can be %s)" % [what, id, what]
        return null
    return t


## Updates an authored, not started task. Fields: name, note, marker, quantity, unit, duration_days (0 clears),
## step, zone_id, elements, virtual, hold_point, after (replaces the predecessors), link_type / lag_days (with `after`,
## or applied to the existing links).
static func update_task(gs: SimState, id: String, fields: Dictionary) -> bool:
    var t: TaskData = _resolve_authored(gs, id, "updated")
    if t == null:
        return false
    if not _states_not_started(gs, t):
        return _fail(gs, "task %s already started" % t.task_id)
    var b: SequenceBundle = gs.bundle
    var succ: Array[String] = (b.successors_by_task.get(t.task_id, []) as Array[String]).duplicate()
    # merged spec: the current values overridden by the given fields, then rebuilt in place
    var spec: Dictionary = {
        "task_id": t.task_id, "manual_id": t.manual_id, "origin": t.origin, "recipe_id": t.recipe_id,
        "step": t.step_id, "zone_id": t.zone_id, "elements": t.element_guids.duplicate(), "virtual": t.is_virtual,
        "marker": t.marker, "note": t.note, "quantity": t.quantity,
        "duration_days": t.duration_days if t.duration_days > 0 else null,
        "name": t.element_name,
    }
    for k in ["step", "zone_id", "elements", "virtual", "duration_days", "quantity", "name", "note", "marker", "unit",
            "hold_point", "requires_access", "requires_crane"]:
        if fields.has(k):
            spec[k] = fields[k]
    if fields.has("step") or fields.has("virtual") or fields.has("elements"):
        if not fields.has("quantity"):
            spec.erase("quantity")  # re-derived from the new binding
        if not fields.has("name"):
            spec.erase("name")
    if fields.has("duration_days") and fields["duration_days"] != null and int(fields["duration_days"]) <= 0:
        spec["duration_days"] = null
    var preds: Array[Dictionary] = t.predecessors.duplicate(true)
    if fields.has("after"):
        spec["after"] = fields["after"]
        spec["link_type"] = fields.get("link_type", "FS")
        spec["lag_days"] = fields.get("lag_days", 0)
        var parsed: Variant = _parse_after(gs, spec, t.task_id)
        if parsed == null:
            return false
        preds = []
        for p in parsed:
            preds.append(p)
        # a cycle check against the successors of the task
        for p in preds:
            if _reaches(gs, str(p["task_id"]), t.task_id, true):
                return _fail(gs, "link would create a cycle through %s" % str(p["task_id"]))
    elif fields.has("link_type") or fields.has("lag_days"):
        for p in preds:
            if fields.has("link_type"):
                var lt: String = str(fields["link_type"])
                if not LINK_TYPES.has(lt):
                    return _fail(gs, "link_type must be FS, SS or FF")
                p["type"] = lt
            if fields.has("lag_days"):
                p["lag_days"] = int(fields["lag_days"])
    spec["after"] = []
    var fresh: TaskData = build_task(gs, spec)
    if fresh == null:
        return false
    # swap: remove the old task from the structures, add the rebuilt one under the same id, restore the successors
    var old_pkg_id: String = t.package_id
    var old_index: int = b.tasks.find(t)
    b.remove_task(t)
    fresh.predecessors = preds
    fresh.package_id = ""
    plan_dates(gs, fresh)
    if not t.runtime_added:
        # a loaded (pipeline) manual task is replaced by a runtime copy with the same id: saves carry it as such
        gs.manual_removed[t.task_id] = true
    fresh.runtime_added = true
    b.add_task(fresh)
    if old_index >= 0:  # keep the task's position in the list
        b.tasks.erase(fresh)
        b.tasks.insert(old_index, fresh)
    b.successors_by_task[fresh.task_id] = succ
    b.assign_package(fresh)
    var rt: TaskRuntime = gs.runtime[fresh.task_id]
    rt.required = fresh.estimated_crew_days
    gs.manual_touched[fresh.task_id] = true
    if old_pkg_id != fresh.package_id and not b.packages_by_id.has(old_pkg_id):
        gs.package_runtime.erase(old_pkg_id)
    replan(gs)
    gs.graph_changed()
    return true


static func remove_task(gs: SimState, id: String, bridge: bool = true) -> bool:
    var t: TaskData = _resolve_authored(gs, id, "removed")
    if t == null:
        return false
    if not _states_not_started(gs, t):
        return _fail(gs, "task %s already started" % t.task_id)
    var b: SequenceBundle = gs.bundle
    var succs: Array[String] = (b.successors_by_task.get(t.task_id, []) as Array[String]).duplicate()
    for sid in succs:
        var s: TaskData = b.tasks_by_id[sid]
        var link_to_t: Dictionary = {}
        for p in s.predecessors:
            if str(p["task_id"]) == t.task_id:
                link_to_t = p
        b.remove_link(s, t.task_id)
        gs.manual_touched[sid] = true
        if not bridge:
            continue
        for pp in t.predecessors:
            var from_id: String = str(pp["task_id"])
            var ltype: String = str(link_to_t.get("type", "FS"))
            var typ: String = ltype if str(pp["type"]) == "FS" else str(pp["type"])
            var lag: int = int(pp["lag_days"]) + int(link_to_t.get("lag_days", 0))
            _merge_link(b, s, from_id, typ, lag)
    gs.unregister_task(t)
    # applied recipe records stay (they describe what the player asked for)
    replan(gs)
    gs.graph_changed()
    return true


# ----------------------------------------------------------------- links

## True when `target` is reachable from `start` over predecessor links (`start` depends on `target`). With
## `successors` the search runs over successors instead (is `target` downstream of `start`).
static func _reaches(gs: SimState, start: String, target: String, include_start: bool = false) -> bool:
    if include_start and start == target:
        return true
    var seen: Dictionary = {start: true}
    var queue: Array[String] = [start]
    var head: int = 0
    while head < queue.size():
        var cur: TaskData = gs.bundle.tasks_by_id.get(queue[head], null)
        head += 1
        if cur == null:
            continue
        for p in cur.predecessors:
            var pid: String = str(p["task_id"])
            if pid == target:
                return true
            if not seen.has(pid):
                seen[pid] = true
                queue.append(pid)
    return false


## Adds the link pred -> succ, or raises the lag / type of an existing one. Returns true when something changed.
static func _merge_link(b: SequenceBundle, succ: TaskData, pred_id: String, type: String, lag: int) -> bool:
    for p in succ.predecessors:
        if str(p["task_id"]) == pred_id:
            if str(p["type"]) == type and int(p["lag_days"]) >= lag:
                return false
            if str(p["type"]) == type:
                p["lag_days"] = lag
            else:
                p["type"] = type
                p["lag_days"] = lag
            b.invalidate_gate_caches()
            b.edited = true
            return true
    b.add_link(succ, pred_id, type, lag)
    return true


## Links two tasks: `to` follows `from` (FS / SS / FF with a lag in days). `to` must not have started; a cycle is refused.
static func link(gs: SimState, from_id: String, to_id: String, type: String = "FS", lag_days: int = 0) -> bool:
    var b: SequenceBundle = gs.bundle
    var f: TaskData = b.tasks_by_id.get(b.resolve_task_id(from_id), null)
    var t: TaskData = b.tasks_by_id.get(b.resolve_task_id(to_id), null)
    if f == null:
        return _fail(gs, "no such task: %s" % from_id)
    if t == null:
        return _fail(gs, "no such task: %s" % to_id)
    if f == t:
        return _fail(gs, "a task cannot follow itself")
    if not LINK_TYPES.has(type):
        return _fail(gs, "link type must be FS, SS or FF")
    if not _states_not_started(gs, t):
        return _fail(gs, "task %s already started" % t.task_id)
    if _reaches(gs, f.task_id, t.task_id):
        return _fail(gs, "link would create a cycle")
    var existing: bool = false
    for p in t.predecessors:
        if str(p["task_id"]) == f.task_id:
            existing = true
    if existing:
        for p in t.predecessors:
            if str(p["task_id"]) == f.task_id:
                p["type"] = type
                p["lag_days"] = lag_days
        b.invalidate_gate_caches()
        b.edited = true
    else:
        b.add_link(t, f.task_id, type, lag_days)
    gs.manual_touched[t.task_id] = true
    replan(gs)
    gs.graph_changed()
    return true


static func unlink(gs: SimState, from_id: String, to_id: String) -> bool:
    var b: SequenceBundle = gs.bundle
    var f: TaskData = b.tasks_by_id.get(b.resolve_task_id(from_id), null)
    var t: TaskData = b.tasks_by_id.get(b.resolve_task_id(to_id), null)
    if f == null or t == null:
        return _fail(gs, "no such task: %s" % (from_id if f == null else to_id))
    var found: bool = false
    for p in t.predecessors:
        if str(p["task_id"]) == f.task_id:
            found = true
    if not found:
        return _fail(gs, "no link from %s to %s" % [from_id, to_id])
    b.remove_link(t, f.task_id)
    gs.manual_touched[t.task_id] = true
    replan(gs)
    gs.graph_changed()
    return true


# ----------------------------------------------------------------- recipes

## Elements a recipe step with `from_element` binds to, relative to `base` ("self" semantics when none apply).
static func bind_elements(gs: SimState, base: ElementData, from_element: String, zone_id: String) -> Array[String]:
    var b: SequenceBundle = gs.bundle
    var out: Array[String] = []
    if base == null:
        if from_element == "zone":
            for e in b.elements:
                if e.zone_id == zone_id:
                    out.append(e.guid)
        return out
    var base_storey: int = int(b.storey_index_by_id.get(base.storey_id, 0))
    match from_element:
        "foundation":
            for e in b.elements:
                if e.guid == base.guid or not _cells_overlap(e.cells, base.cells):
                    continue
                if int(b.storey_index_by_id.get(e.storey_id, 0)) > base_storey:
                    continue
                if FOUNDATION_VISUALS.has(e.visual) or FOUNDATION_CLASSES.has(e.ifc_class):
                    out.append(e.guid)
        "host":
            for e in b.elements:
                if e.guid != base.guid and e.storey_id == base.storey_id and _cells_overlap(e.cells, base.cells):
                    out.append(e.guid)
        "system":
            if base.system_id != "":
                for e in b.elements:
                    if e.system_id == base.system_id:
                        out.append(e.guid)
        "zone":
            for e in b.elements:
                if e.zone_id == base.zone_id:
                    out.append(e.guid)
    if out.is_empty() and from_element != "zone":
        out.append(base.guid)  # "foundation|self": fall back to the element itself
    return out


static func _cells_overlap(a: Array[Vector2i], b: Array[Vector2i]) -> bool:
    for c in a:
        if b.has(c):
            return true
    return false


## Existing tasks (not frozen, not authored by this call) covering a step on the bound elements: generated tasks for
## `ref` on any of the elements; frozen ones (zone in manual mode) do not count.
static func existing_for_step(gs: SimState, ref: String, elements: Array[String]) -> Array[TaskData]:
    var out: Array[TaskData] = []
    for g in elements:
        for t in gs.bundle.tasks_by_element.get(g, []):
            var task: TaskData = t
            if task.step_id == ref and not gs.is_frozen_task(task) and not out.has(task):
                out.append(task)
    return out


## An existing virtual task for the step in the zone (virtual steps are shared by every element of the zone).
static func existing_virtual(gs: SimState, ref: String, zone_id: String) -> TaskData:
    for t in gs.bundle.virtual_tasks:
        if t.step_id == ref and t.zone_id == zone_id:
            return t
    return null


## Expands a recipe at runtime in a zone, for one element (`element_guid`) or, without it, for every element of the
## zone the recipe applies to (up to 50; zone-level steps only when none match). Library steps bind to the
## element(s) per `from_element`; an existing generated task for the step is reused (its links are added), virtual
## steps create virtual tasks; steps chain FS (+ lag_days), `parallel_with` links SS, recipe `logic` adds links
## (the larger lag wins), `hold_point` makes the task an inspection. Returns {ok, created, reused, links,
## skipped_links, steps}.
static func apply_recipe(gs: SimState, recipe_id: String, zone_id: String = "", element_guid: String = "",
        include_optional: bool = false) -> Dictionary:
    var b: SequenceBundle = gs.bundle
    var recipe: RecipeData = gs.recipe_by_id(recipe_id)
    if recipe == null:
        gs.last_error = "unknown recipe: %s" % recipe_id
        return {}
    var base_list: Array[ElementData] = []
    if element_guid != "":
        var e: ElementData = b.elements_by_guid.get(element_guid, null)
        if e == null:
            gs.last_error = "unknown element: %s" % element_guid
            return {}
        if zone_id == "":
            zone_id = e.zone_id
        base_list.append(e)
    if zone_id == "" or not b.zones_by_id.has(zone_id):
        gs.last_error = "no such zone: %s" % zone_id
        return {}
    if element_guid == "":
        for e in b.elements:
            if e.zone_id == zone_id and LogicLib.element_matches(gs, recipe, e).size() > 0 and base_list.size() < MAX_RECIPE_ELEMENTS:
                base_list.append(e)
    var flat: Dictionary = recipe.flatten(func(id: String) -> RecipeData: return gs.recipe_by_id(id))
    var result: Dictionary = {"ok": true, "recipe_id": recipe_id, "zone_id": zone_id, "element_guid": element_guid,
            "created": [] as Array[String], "reused": [] as Array[String], "links": 0, "skipped_links": [] as Array,
            "steps": [] as Array}
    var targets: Array = base_list if not base_list.is_empty() else [null]
    for base in targets:
        _apply_to(gs, recipe, flat, zone_id, base, include_optional, result)
    manual_applied_record(gs, recipe_id, zone_id, element_guid, include_optional)
    replan(gs)
    gs.graph_changed()
    return result


static func manual_applied_record(gs: SimState, recipe_id: String, zone_id: String, element_guid: String, include_optional: bool) -> void:
    gs.manual_applied.append({"recipe": recipe_id, "zone_id": zone_id, "element_guid": element_guid,
            "include_optional": include_optional})


static func _apply_to(gs: SimState, recipe: RecipeData, flat: Dictionary, zone_id: String, base: ElementData,
        include_optional: bool, result: Dictionary) -> void:
    var b: SequenceBundle = gs.bundle
    var node_by_key: Dictionary = {}  # key or ref -> Array[String] task ids
    var prev: Array[String] = []
    var pending_links: Array[Dictionary] = []
    for s in flat["steps"]:
        var step: RecipeStepData = s
        if step.optional and not include_optional:
            continue
        var row: Dictionary = {"key": step.key, "ref": step.ref, "virtual": step.is_virtual, "status": "", "task_ids": []}
        var nodes: Array[String] = []
        if step.is_virtual:
            var vt: TaskData = existing_virtual(gs, step.ref, zone_id) if step.ref != "" else null
            if vt != null:
                nodes.append(vt.task_id)
                (result["reused"] as Array).append(vt.task_id)
                row["status"] = "reused"
            else:
                var spec: Dictionary = _recipe_spec(recipe, step, zone_id, [])
                spec["virtual"] = true
                var tid: String = _create_recipe_task(gs, spec)
                if tid == "":
                    row["status"] = "error"
                    row["error"] = gs.last_error
                    (result["steps"] as Array).append(row)
                    continue
                nodes.append(tid)
                (result["created"] as Array).append(tid)
                row["status"] = "created"
        else:
            var bound: Array[String] = bind_elements(gs, base, step.from_element, zone_id)
            if bound.is_empty():
                row["status"] = "skipped"
                row["error"] = "no element to bind (apply the recipe to an element)"
                (result["steps"] as Array).append(row)
                continue
            var existing: Array[TaskData] = existing_for_step(gs, step.ref, bound)
            if not existing.is_empty():
                for t in existing:
                    nodes.append(t.task_id)
                    (result["reused"] as Array).append(t.task_id)
                row["status"] = "reused"
            else:
                var spec2: Dictionary = _recipe_spec(recipe, step, zone_id, bound)
                var tid2: String = _create_recipe_task(gs, spec2)
                if tid2 == "":
                    row["status"] = "error"
                    row["error"] = gs.last_error
                    (result["steps"] as Array).append(row)
                    continue
                nodes.append(tid2)
                (result["created"] as Array).append(tid2)
                row["status"] = "created"
        row["task_ids"] = nodes.duplicate()
        (result["steps"] as Array).append(row)
        node_by_key[step.key] = nodes
        if step.ref != "" and not node_by_key.has(step.ref):
            node_by_key[step.ref] = nodes
        if step.parallel_with != "" and node_by_key.has(step.parallel_with):
            for from_id in node_by_key[step.parallel_with]:
                for to_id in nodes:
                    pending_links.append({"from": str(from_id), "to": to_id, "type": "SS", "lag": step.lag_days})
        else:
            for from_id in prev:
                for to_id in nodes:
                    pending_links.append({"from": from_id, "to": to_id, "type": "FS", "lag": step.lag_days})
            prev = nodes
    for l in flat["logic"]:
        var ld: Dictionary = l
        var after_nodes: Array = node_by_key.get(str(ld["after"]), [])
        var before_nodes: Array = node_by_key.get(str(ld["before"]), [])
        for a in after_nodes:
            for bn in before_nodes:
                pending_links.append({"from": str(a), "to": str(bn), "type": str(ld["type"]), "lag": int(ld["lag_days"])})
    var made: int = 0
    for pl in pending_links:
        if int(made) >= 4000:
            break
        var f: TaskData = b.tasks_by_id.get(str(pl["from"]), null)
        var t2: TaskData = b.tasks_by_id.get(str(pl["to"]), null)
        if f == null or t2 == null or f == t2:
            continue
        if not _states_not_started(gs, t2):
            (result["skipped_links"] as Array).append({"from": f.task_id, "to": t2.task_id, "reason": "task already started"})
            continue
        if _reaches(gs, f.task_id, t2.task_id):
            (result["skipped_links"] as Array).append({"from": f.task_id, "to": t2.task_id, "reason": "cycle"})
            continue
        if _merge_link(b, t2, f.task_id, str(pl["type"]), int(pl["lag"])):
            result["links"] = int(result["links"]) + 1
            made += 1
            gs.manual_touched[t2.task_id] = true


static func _recipe_spec(recipe: RecipeData, step: RecipeStepData, zone_id: String, elements: Array[String]) -> Dictionary:
    var spec: Dictionary = {"step": step.ref, "zone_id": zone_id, "elements": elements, "origin": "recipe",
            "recipe_id": recipe.id, "note": step.note}
    if not step.step.is_empty():
        spec["step_def"] = step.step
    if step.duration_days > 0:
        spec["duration_days"] = step.duration_days
    var mk: String = step.marker if step.marker != "" else (recipe.virtual_visual if step.is_virtual else "")
    if mk != "":
        spec["marker"] = mk
    if step.hold_point != "":
        spec["hold_point"] = step.hold_point
    if not step.quantity.is_empty() and step.quantity.has("value"):
        spec["quantity"] = float(step.quantity["value"]) * float(step.quantity.get("factor", 1.0))
    elif not step.quantity.is_empty() and step.quantity.has("factor") and not step.is_virtual:
        pass  # factor against the BIM quantity: handled by the default quantity of the bound elements
    return spec


static func _create_recipe_task(gs: SimState, spec: Dictionary) -> String:
    var t: TaskData = build_task(gs, spec)
    if t == null:
        return ""
    if not gs.register_task(t, false):
        return ""
    return t.task_id


# ----------------------------------------------------------------- export

## manual_sequence.schema.json document of the current state: zones in manual mode, every authored task (manual
## and recipe) with its links, overrides and applied recipes. Links of authored tasks to generated tasks use the
## generated T-ids; links added onto generated tasks cannot be expressed in the document.
static func export_doc(gs: SimState) -> Dictionary:
    var b: SequenceBundle = gs.bundle
    var zones: Array = gs.manual_zones.keys()
    zones.sort()
    var tasks: Array = []
    for t in b.tasks:
        if not t.is_authored():
            continue
        tasks.append(_export_task(gs, t))
    var overrides: Array = []
    if b.manual != null:
        for o in b.manual.overrides:
            overrides.append((o as Dictionary).duplicate(true))
    var applied: Array = []
    for a in gs.manual_applied:
        var ad: Dictionary = {"recipe": str(a["recipe"]), "zone_id": str(a["zone_id"]),
                "element_guid": str(a["element_guid"]) if str(a["element_guid"]) != "" else null,
                "include_optional": bool(a["include_optional"])}
        applied.append(ad)
    var doc: Dictionary = {
        "schema_version": "1.0", "project": b.project_name, "zones_in_manual_mode": zones,
        "inherit_logic": b.manual.inherit_logic if b.manual != null else true,
        "tasks": tasks, "overrides": overrides, "applied_recipes": applied,
    }
    if b.manual != null and b.manual.author != "":
        doc["author"] = b.manual.author
    return doc


static func _manual_ref(gs: SimState, task_id: String) -> String:
    var t: TaskData = gs.bundle.tasks_by_id.get(task_id, null)
    if t != null and t.is_authored() and t.manual_id != "":
        return t.manual_id
    return task_id


static func _export_task(gs: SimState, t: TaskData) -> Dictionary:
    var d: Dictionary = {"id": t.manual_id if t.manual_id != "" else t.task_id, "step": t.step_id,
            "name": t.element_name, "zone_id": t.zone_id, "virtual": t.is_virtual,
            "quantity": t.quantity, "unit": t.unit}
    if not t.element_guids.is_empty():
        d["elements"] = t.element_guids.duplicate()
    if t.duration_days > 0:
        d["duration_days"] = t.duration_days
    var after: Array = []
    var mixed: bool = false
    for p in t.predecessors:
        after.append(_manual_ref(gs, str(p["task_id"])))
        if str(p["type"]) != str(t.predecessors[0]["type"]) or int(p["lag_days"]) != int(t.predecessors[0]["lag_days"]):
            mixed = true
    d["after"] = after
    if not t.predecessors.is_empty():
        d["link_type"] = str(t.predecessors[0]["type"])
        d["lag_days"] = int(t.predecessors[0]["lag_days"])
    if mixed:
        var links: Array = []
        for p in t.predecessors:
            links.append({"id": _manual_ref(gs, str(p["task_id"])), "type": str(p["type"]), "lag_days": int(p["lag_days"])})
        d["links"] = links
    if t.recipe_id != "":
        d["recipe_id"] = t.recipe_id
    if t.marker != "":
        d["marker"] = t.marker
    if t.note != "":
        d["note"] = t.note
    return d


# ----------------------------------------------------------------- save / load

static func serialize(gs: SimState) -> Dictionary:
    var b: SequenceBundle = gs.bundle
    var tasks_out: Array = []
    var preds: Dictionary = {}
    for tid in gs.manual_touched:
        var t: TaskData = gs.bundle.tasks_by_id.get(tid, null)
        if t == null:
            continue
        preds[tid] = (t.to_dict()["predecessors"] as Array)
        if t.runtime_added:
            var d: Dictionary = t.to_dict()
            d["predecessors"] = []
            tasks_out.append(d)
    var zones: Array = gs.manual_zones.keys()
    zones.sort()
    return {"zones": zones, "next_id": gs.manual_next_id, "tasks": tasks_out, "preds": preds,
            "removed": gs.manual_removed.keys(), "applied": gs.manual_applied.duplicate(true),
            "steps": b.added_steps.duplicate(true)}


## Replays a saved manual block on a freshly started (pristine) bundle: removed tasks, runtime-added tasks (without
## links), then every saved predecessor list. An empty dictionary restores the bundle's own manual zones.
static func restore(gs: SimState, d: Dictionary) -> void:
    var b: SequenceBundle = gs.bundle
    gs.manual_zones.clear()
    gs.manual_applied.clear()
    if d.is_empty():
        if b.manual != null:
            for zid in b.manual.zones_in_manual_mode:
                gs.manual_zones[zid] = true
        gs.apply_manual_flags()
        return
    for zid in d.get("zones", []):
        if b.zones_by_id.has(str(zid)):
            gs.manual_zones[str(zid)] = true
    for a in d.get("applied", []):
        var ad: Dictionary = a
        gs.manual_applied.append({"recipe": str(ad["recipe"]), "zone_id": str(ad["zone_id"]),
                "element_guid": str(ad.get("element_guid", "")), "include_optional": bool(ad.get("include_optional", false))})
    gs.manual_next_id = maxi(gs.manual_next_id, int(d.get("next_id", 1)))
    for sd in d.get("steps", []):
        var sdd: Dictionary = sd
        if not b.steps_by_id.has(str(sdd.get("id", ""))):
            b.add_step(StepDef.from_dict(sdd), sdd)
    for id in d.get("removed", []):
        var t: TaskData = b.tasks_by_id.get(str(id), null)
        if t != null:
            gs.unregister_task(t)  # links to it are dropped through the saved predecessor lists / the replacement
    for td in d.get("tasks", []):
        var task := TaskData.from_dict(td)
        task.runtime_added = true
        gs.register_task(task, false, task.package_id)
    var preds: Dictionary = d.get("preds", {})
    for tid in preds:
        var task2: TaskData = b.tasks_by_id.get(str(tid), null)
        if task2 == null:
            continue
        task2.predecessors.clear()
        for p in preds[tid]:
            var pd: Dictionary = p
            if b.tasks_by_id.has(str(pd["task_id"])):
                var link: Dictionary = {"task_id": str(pd["task_id"]), "type": str(pd["type"]), "lag_days": int(pd["lag_days"])}
                if pd.has("reason"):
                    link["reason"] = str(pd["reason"])
                task2.predecessors.append(link)
        gs.manual_touched[task2.task_id] = true
    b.rebuild_successors()
    gs.apply_manual_flags()
    gs.graph_changed()
