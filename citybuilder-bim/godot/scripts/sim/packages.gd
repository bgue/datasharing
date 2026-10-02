class_name Packages
extends RefCounted
## Package runtime (docs/05 sections 1-2): which crews work which package, the crew demand
## curve, work faces and the player-facing package state.

const OVER_CAP_FACTORS: Array[float] = [1.0, 0.6, 0.35]


static func _is_work_state(st: int) -> bool:
    return st == TaskRuntime.State.ACTIVE or st == TaskRuntime.State.REWORK


## Tasks of the package that a crew could work today: READY ones only while the package is released,
## and not held back by a standing impediment (no access, out of crane reach, zone paused). A full
## laydown yard is transient (tasks finishing free it), so it does not count as an impediment here.
static func has_work(gs: SimState, p: PackageData) -> bool:
    var rt: PackageRuntime = gs.package_runtime[p.package_id]
    if rt.frozen:
        return false
    for t in p.tasks:
        var trt: TaskRuntime = gs.runtime[t.task_id]
        if _is_work_state(trt.state):
            return true
        if trt.state == TaskRuntime.State.READY and rt.released \
                and (trt.blocked_reason == "" or trt.blocked_reason.begins_with("No free laydown")):
            return true
    return false


static func is_done(gs: SimState, p: PackageData) -> bool:
    for t in p.tasks:
        if not TaskRuntime.is_finished((gs.runtime[t.task_id] as TaskRuntime).state):
            return false
    return true


## Packages of a trade in a zone, lowest priority value first.
static func zone_trade_packages(gs: SimState, zone_id: String, trade: String) -> Array[PackageData]:
    var out: Array[PackageData] = []
    for p in gs.bundle.packages_by_zone.get(zone_id, []):
        if (p as PackageData).trade == trade:
            out.append(p)
    out.sort_custom(func(a: PackageData, b: PackageData) -> bool:
        var pa: int = (gs.package_runtime[a.package_id] as PackageRuntime).priority
        var pb: int = (gs.package_runtime[b.package_id] as PackageRuntime).priority
        if pa != pb:
            return pa < pb
        return a.package_id < b.package_id)
    return out


## Workable packages (released with ready work, or with active work) of a trade in a zone, in pick order.
static func workable(gs: SimState, zone_id: String, trade: String) -> Array[PackageData]:
    var out: Array[PackageData] = []
    for p in zone_trade_packages(gs, zone_id, trade):
        if has_work(gs, p):
            out.append(p)
    return out


static func zone_trade_has_work(gs: SimState, zone_id: String, trade: String) -> bool:
    for p in gs.bundle.packages_by_zone.get(zone_id, []):
        if (p as PackageData).trade == trade and has_work(gs, p):
            return true
    return false


## Crews of each (zone, trade) are dealt over the workable packages in priority order: a package
## takes crews up to its max, the rest spill to the next package; leftovers idle.
## Returns {"by_package": {package_id: Array[int] crew ids}, "idle": {crew_id: reason}}.
static func allocate(gs: SimState) -> Dictionary:
    var by_package: Dictionary = {}
    var idle: Dictionary = {}
    var groups: Dictionary = {}  # "zone|trade" -> Array[int]
    for c in gs.crews:
        var z: String = str(c["zone_id"])
        if z == "":
            continue
        var key: String = "%s|%s" % [z, str(c["trade"])]
        if not groups.has(key):
            groups[key] = [] as Array[int]
        (groups[key] as Array[int]).append(int(c["id"]))
    for key in groups:
        var parts: PackedStringArray = (key as String).split("|")
        var crews: Array[int] = groups[key]
        var pkgs: Array[PackageData] = workable(gs, parts[0], parts[1])
        var idx: int = 0
        for p in pkgs:
            if idx >= crews.size():
                break
            var take: int = mini(p.crew_max, crews.size() - idx)
            var list: Array[int] = []
            for k in take:
                list.append(crews[idx + k])
            by_package[p.package_id] = list
            idx += take
        for k in range(idx, crews.size()):
            idle[crews[k]] = "package full" if not pkgs.is_empty() else "nothing ready"
    return {"by_package": by_package, "idle": idle}


# ----------------------------------------------------------------- faces

static func face_over_factor(count: int, cap: int) -> float:
    return OVER_CAP_FACTORS[clampi(count - cap, 0, 2)]


## Face multiplier for a package's crews in a zone given the crews per face of that zone.
## `counts`: face -> crews working on it today.
static func face_factor(gs: SimState, zone: ZoneData, face: String, counts: Dictionary) -> float:
    if face == "any":
        return 1.0
    var excl_active: bool = false
    for ef in gs.bundle.exclusive_faces:
        if int(counts.get(ef, 0)) > 0:
            excl_active = true
    var f: float = 1.0
    if excl_active and not gs.bundle.exclusive_faces.has(face):
        f = 0.35
    if zone.faces.has(face):
        f = minf(f, face_over_factor(int(counts.get(face, 0)), int(zone.faces[face])))
    return f


## Rebuilds gs.zone_face_state from an allocation: zone -> face -> {crews, cap, over, excluded, discipline}.
static func compute_face_state(gs: SimState, alloc: Dictionary) -> void:
    var state: Dictionary = {}
    for pid in alloc["by_package"]:
        var p: PackageData = gs.bundle.packages_by_id[pid]
        var n: int = (alloc["by_package"][pid] as Array).size()
        if n < p.crew_min or p.work_face == "any":
            continue
        if not state.has(p.zone_id):
            state[p.zone_id] = {}
        var zs: Dictionary = state[p.zone_id]
        if not zs.has(p.work_face):
            zs[p.work_face] = {"crews": 0, "cap": -1, "over": false, "excluded": false, "discipline": p.discipline}
        zs[p.work_face]["crews"] = int(zs[p.work_face]["crews"]) + n
    for zid in state:
        var zone: ZoneData = gs.bundle.zones_by_id[zid]
        var zs: Dictionary = state[zid]
        var counts: Dictionary = {}
        for f in zs:
            counts[f] = int(zs[f]["crews"])
        for f in zs:
            var e: Dictionary = zs[f]
            if zone.faces.has(f):
                e["cap"] = int(zone.faces[f])
                e["over"] = int(e["crews"]) > int(zone.faces[f])
            e["excluded"] = _excl_active(gs, counts) and not gs.bundle.exclusive_faces.has(f)
    gs.zone_face_state = state


static func _excl_active(gs: SimState, counts: Dictionary) -> bool:
    for ef in gs.bundle.exclusive_faces:
        if int(counts.get(ef, 0)) > 0:
            return true
    return false


# ----------------------------------------------------------------- state for the player

static func crew_days_done(gs: SimState, p: PackageData) -> float:
    var s: float = 0.0
    for t in p.tasks:
        var rt: TaskRuntime = gs.runtime[t.task_id]
        s += minf(rt.progress, rt.required) if not TaskRuntime.is_finished(rt.state) else rt.required
    return s


## Player-facing state (docs/05 section 1.3) and reason for the given crews now on the package.
static func compute_state(gs: SimState, p: PackageData, crews_now: int) -> Dictionary:
    var rt: PackageRuntime = gs.package_runtime[p.package_id]
    if is_done(gs, p):
        return {"state": "done", "reason": ""}
    if rt.frozen:
        return {"state": "held", "reason": "zone in manual mode"}
    if not rt.released:
        return {"state": "held", "reason": "held"}
    var ready: bool = has_work(gs, p)
    if not ready:
        # released READY tasks held back by a standing impediment: ready, with the reason
        for t in p.tasks:
            var itr: TaskRuntime = gs.runtime[t.task_id]
            if itr.state == TaskRuntime.State.READY and itr.blocked_reason != "":
                return {"state": "ready" if crews_now <= 0 else "active", "reason": itr.blocked_reason}
        var reason: String = ""
        for t in p.tasks:
            var trt: TaskRuntime = gs.runtime[t.task_id]
            if trt.state == TaskRuntime.State.BLOCKED or trt.state == TaskRuntime.State.NOT_STARTED:
                reason = trt.blocked_reason
                break
            if trt.state == TaskRuntime.State.AWAITING_INSPECTION:
                reason = "awaiting inspection"
        return {"state": "waiting", "reason": reason}
    # impeded?
    var impeded: String = ""
    var all_impeded: bool = true
    for t in p.tasks:
        var trt2: TaskRuntime = gs.runtime[t.task_id]
        if trt2.state == TaskRuntime.State.READY:
            if trt2.blocked_reason == "":
                all_impeded = false
            elif impeded == "":
                impeded = trt2.blocked_reason
        elif _is_work_state(trt2.state):
            all_impeded = false
    if crews_now <= 0:
        return {"state": "ready", "reason": impeded if all_impeded and impeded != "" else ""}
    if crews_now < p.crew_min:
        return {"state": "understaffed", "reason": "needs %d crews" % p.crew_min}
    if crews_now > p.crew_ideal:
        return {"state": "over_ideal", "reason": ""}
    return {"state": "active", "reason": impeded if all_impeded and impeded != "" else ""}


## Recomputes state / crews_now / crew_days_done of every package (prospective crew counts from the
## current assignments) and emits package_state_changed for changes.
static func refresh_states(gs: SimState) -> void:
    var alloc: Dictionary = allocate(gs)
    for p in gs.bundle.packages:
        var rt: PackageRuntime = gs.package_runtime[p.package_id]
        var n: int = (alloc["by_package"].get(p.package_id, []) as Array).size()
        rt.crews_now = n
        var st: Dictionary = compute_state(gs, p, n)
        rt.blocked_reason = str(st["reason"])
        rt.crew_days_done = crew_days_done(gs, p)
        if rt.state != st["state"]:
            rt.state = st["state"]
            gs.package_state_changed.emit(p.package_id, rt.state)
