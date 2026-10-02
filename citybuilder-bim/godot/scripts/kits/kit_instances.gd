class_name KitInstances
extends RefCounted
## Groups the elements of a bundle that map to a kit into kit instances (docs/06 B.4).
##
## Per (storey, kit) the elements are clustered by cell connectivity: kits with params.instancing =
## "group" (rack, culvert, bridge pier, cooling tower) join elements on touching cells (4-neighbourhood);
## "element" kits (tanks, pumps, transformers, ...) only join elements that share a cell, so adjacent
## tanks stay separate. The instance footprint is the bounding rectangle of the union of cells; its tasks
## are the union of its elements' tasks, plus the tasks of `member_guids` aggregates.
##
## instances() entries: {id, kit, variant, cells, rect, storey_id, storey_index, element_guids,
##   height_m, counts, seed, lod_distance, layer_fills, overall_fill, present_layers, layer_states}.

var bundle: SequenceBundle = null
var registry: KitRegistry = null

var _list: Array[Dictionary] = []
var _tasks: Array = []  # instance index -> Array[TaskData]
var _by_guid: Dictionary = {}  # element guid -> instance index
var _kit_of: Dictionary = {}  # element guid -> kit id


func _init(b: SequenceBundle = null, reg: KitRegistry = null) -> void:
    if b != null:
        build(b, reg)


func build(b: SequenceBundle, reg: KitRegistry = null) -> void:
    bundle = b
    registry = reg if reg != null else KitRegistry.shared()
    registry.bind_bundle(b)
    _list = []
    _tasks = []
    _by_guid = {}
    _kit_of = {}
    var buckets: Dictionary = {}  # "storey|kit|variant hint" -> Array[ElementData]
    var order: Array[String] = []
    # variant hints (visual_kit = "rack_ei"): per cell the highest hinted variant, so that unhinted members
    # (pipes) join the section they sit in and sections with different variants become separate instances
    var cell_hint: Dictionary = {}  # "storey|kit" -> {cell -> variant rank}
    var kits_of: Dictionary = {}  # guid -> kit
    for e in b.elements:
        var el: ElementData = e
        if el.cells.is_empty():
            continue
        var kit: String = registry.kit_for_element(el)
        if kit == "":
            continue
        kits_of[el.guid] = kit
        var vh: String = registry.variant_hint(el)
        if vh != "":
            var ck: String = "%s|%s" % [el.storey_id, kit]
            if not cell_hint.has(ck):
                cell_hint[ck] = {}
            var rank: int = registry.variant_rank(kit, vh)
            for c in el.cells:
                cell_hint[ck][c] = maxi(rank, int((cell_hint[ck] as Dictionary).get(c, -1)))
    for e in b.elements:
        var el2: ElementData = e
        if not kits_of.has(el2.guid):
            continue
        var kit2: String = kits_of[el2.guid]
        var rank2: int = -1
        var ck2: String = "%s|%s" % [el2.storey_id, kit2]
        if cell_hint.has(ck2):
            for c in el2.cells:
                rank2 = maxi(rank2, int((cell_hint[ck2] as Dictionary).get(c, -1)))
        var key: String = "%s|%s|%d" % [el2.storey_id, kit2, rank2]
        if not buckets.has(key):
            buckets[key] = [] as Array[ElementData]
            order.append(key)
        (buckets[key] as Array[ElementData]).append(el2)
        _kit_of[el2.guid] = kit2
    var raw: Array[Dictionary] = []
    for key in order:
        var list: Array[ElementData] = buckets[key]
        var kit3: String = _kit_of[list[0].guid]
        var adjacent: bool = str(registry.kit_param(kit3, "instancing", "element")) == "group"
        var hint_rank: int = int(key.get_slice("|", 2))
        for cluster in _cluster(list, adjacent):
            var d: Dictionary = _make(kit3, list[0].storey_id, cluster)
            d["hint_rank"] = hint_rank
            raw.append(d)
    raw.sort_custom(func(a: Dictionary, c: Dictionary) -> bool:
        if int(a["storey_index"]) != int(c["storey_index"]):
            return int(a["storey_index"]) < int(c["storey_index"])
        var ra: Rect2i = a["rect"]
        var rc: Rect2i = c["rect"]
        if ra.position.y != rc.position.y:
            return ra.position.y < rc.position.y
        if ra.position.x != rc.position.x:
            return ra.position.x < rc.position.x
        return str(a["kit"]) < str(c["kit"]))
    for d in raw:
        var tasks: Array = d["_tasks"]
        d.erase("_tasks")
        d["id"] = _list.size()
        _list.append(d)
        _tasks.append(tasks)
        for g in d["element_guids"]:
            _by_guid[g] = int(d["id"])
    for i in _list.size():
        var inst: Dictionary = _list[i]
        var tasks2: Array = _tasks[i]
        var variant: String = registry.variant_for(inst["kit"], registry.disciplines_of_tasks(tasks2))
        var hr: int = int(inst.get("hint_rank", -1))
        if hr > registry.variant_rank(inst["kit"], variant):  # a hinted variant is a lower bound
            variant = str(((registry.kits[inst["kit"]] as Dictionary)["variants"] as Dictionary).keys()[hr])
        inst["variant"] = variant
        inst["layer_fills"] = {}
        inst["overall_fill"] = 0.0
        inst["present_layers"] = registry.layer_ids(inst["kit"])
        inst["layer_states"] = {}


## Union-find clustering of elements over their cells.
func _cluster(list: Array[ElementData], adjacent: bool) -> Array:
    var parent: Array[int] = []
    for i in list.size():
        parent.append(i)
    var cell_owner: Dictionary = {}  # Vector2i -> first element index
    var find: Callable = func(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i in list.size():
        for c in list[i].cells:
            if cell_owner.has(c):
                var ra: int = find.call(i)
                var rb: int = find.call(int(cell_owner[c]))
                if ra != rb:
                    parent[ra] = rb
            else:
                cell_owner[c] = i
    if adjacent:
        var dirs: Array[Vector2i] = [Vector2i(1, 0), Vector2i(0, 1)]
        for c in cell_owner:
            for dv in dirs:
                var n: Vector2i = (c as Vector2i) + dv
                if cell_owner.has(n):
                    var ra2: int = find.call(int(cell_owner[c]))
                    var rb2: int = find.call(int(cell_owner[n]))
                    if ra2 != rb2:
                        parent[ra2] = rb2
    var groups: Dictionary = {}
    var roots: Array[int] = []
    for i in list.size():
        var r: int = find.call(i)
        if not groups.has(r):
            groups[r] = [] as Array[ElementData]
            roots.append(r)
        (groups[r] as Array[ElementData]).append(list[i])
    var out: Array = []
    for r in roots:
        out.append(groups[r])
    return out


func _make(kit: String, storey_id: String, members: Array[ElementData]) -> Dictionary:
    var cell_set: Dictionary = {}
    var guids: Array[String] = []
    var tasks: Array = []
    var seen_tasks: Dictionary = {}
    var minx: int = 1 << 30
    var maxx: int = -(1 << 30)
    var minz: int = 1 << 30
    var maxz: int = -(1 << 30)
    var hint_h: float = 0.0
    var systems: Dictionary = {}
    for el in members:
        guids.append(el.guid)
        for c in el.cells:
            cell_set[c] = true
            minx = mini(minx, c.x)
            maxx = maxi(maxx, c.x)
            minz = mini(minz, c.y)
            maxz = maxi(maxz, c.y)
        if el.has_size_hint:
            hint_h = maxf(hint_h, el.size_hint.y)
        if el.ifc_class == "IfcPipeSegment" and el.system_id != "":
            systems[el.system_id] = true
        var ids: Array[String] = [el.guid]
        var mg: Variant = el.get("member_guids")
        if mg is Array:
            for m in mg:
                ids.append(str(m))
        for g in ids:
            for t in bundle.tasks_by_element.get(g, []):
                var task: TaskData = t
                if not seen_tasks.has(task.task_id):
                    seen_tasks[task.task_id] = true
                    tasks.append(task)
    var cells: Array[Vector2i] = []
    for c in cell_set:
        cells.append(c)
    cells.sort_custom(func(a: Vector2i, b: Vector2i) -> bool: return a.y < b.y or (a.y == b.y and a.x < b.x))
    var default_h: float = float(registry.kit_param(kit, "default_height_m", 4.0))
    var min_h: float = float(registry.kit_param(kit, "min_height_m", 1.0))
    var h: float = default_h
    if str(registry.kit_param(kit, "height_from", "element")) == "element" and hint_h > 0.0:
        h = maxf(min_h, hint_h)
    h = clampf(h, 1.0, 80.0)
    var counts: Dictionary = {}
    if kit == "rack" and systems.size() > 0:
        counts["pipes"] = systems.size()
    var storey_index: int = int(bundle.storey_index_by_id.get(storey_id, 0))
    return {
        "kit": kit,
        "storey_id": storey_id,
        "storey_index": storey_index,
        "cells": cells,
        "rect": Rect2i(minx, minz, maxx - minx + 1, maxz - minz + 1),
        "element_guids": guids,
        "height_m": h,
        "counts": counts,
        "seed": absi(hash(guids[0])) & 0x7fffffff,
        "lod_distance": registry.lod_distance(kit),
        "_tasks": tasks,
    }


# ------------------------------------------------------------------ access

func instances() -> Array[Dictionary]:
    return _list


func count() -> int:
    return _list.size()


func tasks_of(index: int) -> Array:
    return _tasks[index]


## Instance index of an element, -1 when the element is not drawn by a kit.
func instance_of(guid: String) -> int:
    return int(_by_guid.get(guid, -1))


## Per-element view of the instance it belongs to (for the API's element_visual_layers); {} when the element has no
## kit. Fills are those of the last refresh().
func element_layers(guid: String) -> Dictionary:
    var i: int = instance_of(guid)
    if i < 0:
        return {}
    var inst: Dictionary = _list[i]
    return {
        "instance": i, "kit": inst["kit"], "variant": inst["variant"],
        "layers": (inst["layer_fills"] as Dictionary).duplicate(),
        "present": (inst["present_layers"] as Array).duplicate(),
        "states": (inst["layer_states"] as Dictionary).duplicate(),
        "overall": inst["overall_fill"],
    }


func kit_guids() -> Dictionary:
    return _by_guid


## Recomputes layer_fills / overall_fill / present_layers / layer_states of every instance.
## progress: Callable(task) -> crew-days done; task_state: optional Callable(task) -> TaskRuntime.State.
func refresh(progress: Callable, task_state: Callable = Callable()) -> void:
    for i in _list.size():
        refresh_one(i, progress, task_state)


func refresh_one(i: int, progress: Callable, task_state: Callable = Callable()) -> void:
    var inst: Dictionary = _list[i]
    var r: Dictionary = registry.analyze(inst["kit"], _tasks[i], progress, task_state, inst["variant"])
    inst["layer_fills"] = r["fills"]
    inst["present_layers"] = r["present"]
    inst["layer_states"] = r["states"]
    inst["overall_fill"] = r["overall"]
