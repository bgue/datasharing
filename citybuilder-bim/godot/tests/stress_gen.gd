class_name StressGen
extends RefCounted
## In-engine synthetic stress bundle: replicates a scenario bundle (default healthcare_standard) across N wings laid out
## on one world grid (cell offsets, renamed zones / elements / tasks / packages, scaled budget, crews and site
## cells). Used by tests/test_scale_big.gd until a pipeline-generated stress bundle is present, and by the loader
## tests (`write_split` writes the same bundle as .json.gz, task parts and per-zone detail files).

const BASE_PATH: String = "res://scenarios/healthcare_standard/sequence.json"
const GAP_CELLS: int = 2
## Task / package number offsets per wing (the base bundle numbers stay below them).
const TASK_STRIDE: int = 2000
const PACKAGE_STRIDE: int = 400


static func _off(cells: Array, dx: int, dz: int) -> Array:
    var out: Array = []
    out.resize(cells.size())
    for i in cells.size():
        var c: Array = cells[i]
        out[i] = [int(c[0]) + dx, int(c[1]) + dz]
    return out


static func _tid(id: String, w: int) -> String:
    return "T%06d" % (int(id.substr(1)) + w * TASK_STRIDE)


static func _pid(id: String, w: int) -> String:
    return "P%05d" % (int(id.substr(1)) + w * PACKAGE_STRIDE)


## Returns a bundle dictionary of `wings` copies of the base bundle (`cols` wings per row).
static func generate(wings: int = 13, cols: int = 4, base_path: String = BASE_PATH) -> Dictionary:
    var base: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(base_path))
    var grid: Dictionary = base["project"]["grid"]
    var ww: int = int(grid["width_cells"]) + 14  # room for the gate roads / live area of the base site
    var wd: int = int(grid["depth_cells"]) + 6
    var pitch_x: int = ww + GAP_CELLS
    var pitch_z: int = wd + GAP_CELLS
    var out: Dictionary = {}
    for k in ["schema_version", "storeys", "systems", "step_library", "generated_at", "generator"]:
        if base.has(k):
            out[k] = base[k]
    out["project"] = (base["project"] as Dictionary).duplicate(true)
    out["project"]["name"] = "Stress campus (%d wings)" % wings
    var rows: int = int(ceil(float(wings) / float(cols)))
    out["project"]["grid"]["width_cells"] = cols * pitch_x
    out["project"]["grid"]["depth_cells"] = rows * pitch_z
    var zones: Array = []
    var elements: Array = []
    var tasks: Array = []
    var packages: Array = []
    var critical: Array = []
    var sc: Dictionary = (base["scenario"] as Dictionary).duplicate(true)
    var site: Dictionary = sc["site"]
    var gates: Array = []
    var occupied: Array = []
    var blocked: Array = []
    var tiles: Array = []
    var systems: Array = []
    var base_systems: Array = base.get("systems", [])
    for w in wings:
        var dx: int = (w % cols) * pitch_x
        var dz: int = (w / cols) * pitch_z
        var pre: String = "W%02d-" % w
        for z in base["zones"]:
            var z2: Dictionary = (z as Dictionary).duplicate()
            z2["id"] = pre + str(z["id"])
            z2["name"] = "%s%s" % [pre, str(z["name"])]
            z2["cells"] = _off(z["cells"], dx, dz)
            zones.append(z2)
        for s in base_systems:
            var s2: Dictionary = (s as Dictionary).duplicate()
            s2["id"] = pre + str(s["id"])
            systems.append(s2)
        for e in base["elements"]:
            var e2: Dictionary = (e as Dictionary).duplicate()
            e2["guid"] = pre + str(e["guid"])
            e2["zone_id"] = pre + str(e["zone_id"])
            if e.get("system_id", null) != null:
                e2["system_id"] = pre + str(e["system_id"])
            e2["cells"] = _off(e["cells"], dx, dz)
            elements.append(e2)
        for t in base["tasks"]:
            var t2: Dictionary = (t as Dictionary).duplicate()
            t2["task_id"] = _tid(str(t["task_id"]), w)
            if t.get("element_guid", null) != null:
                t2["element_guid"] = pre + str(t["element_guid"])
            t2["zone_id"] = pre + str(t["zone_id"])
            if t.get("system_id", null) != null:
                t2["system_id"] = pre + str(t["system_id"])
            t2["cells"] = _off(t["cells"], dx, dz)
            t2["package_id"] = _pid(str(t["package_id"]), w)
            var preds: Array = []
            for p in t["predecessors"]:
                var p2: Dictionary = (p as Dictionary).duplicate()
                p2["task_id"] = _tid(str(p["task_id"]), w)
                preds.append(p2)
            t2["predecessors"] = preds
            tasks.append(t2)
        for p in base["packages"]:
            var p2: Dictionary = (p as Dictionary).duplicate()
            p2["package_id"] = _pid(str(p["package_id"]), w)
            p2["zone_id"] = pre + str(p["zone_id"])
            var ids: Array = []
            for tid in p["task_ids"]:
                ids.append(_tid(str(tid), w))
            p2["task_ids"] = ids
            p2["name"] = "%s%s" % [pre, str(p["name"])]
            packages.append(p2)
        for id in base["baseline"]["critical_task_ids"]:
            critical.append(_tid(str(id), w))
        for g in site["gates"]:
            gates.append([int(g[0]) + dx, int(g[1]) + dz])
        for c in site["occupied_cells"]:
            occupied.append([int(c[0]) + dx, int(c[1]) + dz])
        for c in site["blocked_cells"]:
            blocked.append([int(c[0]) + dx, int(c[1]) + dz])
        for it in site["initial_tiles"]:
            var it2: Dictionary = (it as Dictionary).duplicate()
            var cc: Array = it["cell"]
            it2["cell"] = [int(cc[0]) + dx, int(cc[1]) + dz]
            tiles.append(it2)
    out["zones"] = zones
    out["systems"] = systems
    out["elements"] = elements
    out["tasks"] = tasks
    out["packages"] = packages
    out["recipes"] = []
    var bl: Dictionary = (base["baseline"] as Dictionary).duplicate(true)
    bl["critical_task_ids"] = critical
    bl["total_cost"] = float(bl["total_cost"]) * wings
    if bl.has("total_labour_cost"):
        bl["total_labour_cost"] = float(bl["total_labour_cost"]) * wings
    bl["total_crew_days"] = float(bl["total_crew_days"]) * wings
    var wp: Array = []
    for v in bl["weekly_planned_cost"]:
        wp.append(float(v) * wings)
    bl["weekly_planned_cost"] = wp
    out["baseline"] = bl
    site["gates"] = gates
    site["occupied_cells"] = occupied
    site["blocked_cells"] = blocked
    site["initial_tiles"] = tiles
    sc["id"] = "stress_synthetic"
    sc["name"] = "Stress campus (synthetic)"
    sc["budget"] = float(sc["budget"]) * wings
    sc["start_cash"] = float(sc["start_cash"]) * wings
    sc["overdraft_limit"] = float(sc["overdraft_limit"]) * wings
    var crews: Dictionary = sc["crews_available"]
    for k in crews:
        crews[k] = int(crews[k]) * wings
    for eq in sc["equipment"]:
        eq["max_count"] = int(eq["max_count"]) * wings
    out["scenario"] = sc
    return out


## Writes `bundle` as <dir>/sequence.json(.gz) with optional split task parts and per-zone detail files.
## opts: gz (bool), parts (int, 0 = tasks inline), detail (bool: tasks become light rows, descriptive fields move to
## <dir>/zones/<zone_id>.json(.gz)). Returns the main file path.
static func write_split(bundle: Dictionary, dir: String, opts: Dictionary = {}) -> String:
    DirAccess.make_dir_recursive_absolute(dir)
    var gz: bool = bool(opts.get("gz", false))
    var parts: int = int(opts.get("parts", 0))
    var detail: bool = bool(opts.get("detail", false))
    var ext: String = ".json.gz" if gz else ".json"
    var main: Dictionary = bundle.duplicate()
    var tasks: Array = bundle["tasks"]
    var fmt: Dictionary = {"compressed": gz}
    if detail:
        var by_zone: Dictionary = {}
        var light: Array = []
        for t in tasks:
            var td: Dictionary = t
            var row: Dictionary = td.duplicate()
            var det: Dictionary = {"task_id": td["task_id"]}
            for k in SequenceBundle.DETAIL_FIELDS:
                if row.has(k):
                    det[k] = row[k]
                    row.erase(k)
            light.append(row)
            var zid: String = str(td["zone_id"])
            if not by_zone.has(zid):
                by_zone[zid] = []
            (by_zone[zid] as Array).append(det)
        tasks = light
        DirAccess.make_dir_recursive_absolute(dir.path_join("zones"))
        for zid in by_zone:
            _write_json(dir.path_join("zones").path_join(str(zid) + ext), {"zone_id": zid, "tasks": by_zone[zid]}, gz)
        fmt["zone_detail_dir"] = "zones"
    if parts > 0:
        var names: Array = []
        var per: int = int(ceil(float(tasks.size()) / float(parts)))
        for p in parts:
            var slice: Array = tasks.slice(p * per, mini((p + 1) * per, tasks.size()))
            var name: String = "tasks.part-%d%s" % [p, ext]
            _write_json(dir.path_join(name), slice, gz)
            names.append(name)
        fmt["task_parts"] = names
        main["tasks"] = []
    else:
        main["tasks"] = tasks
    main["bundle_format"] = fmt
    var path: String = dir.path_join("sequence" + ext)
    _write_json(path, main, gz)
    return path


static func _write_json(path: String, data: Variant, gz: bool) -> void:
    var text: String = JSON.stringify(data)
    var f := FileAccess.open(path, FileAccess.WRITE)
    if gz:
        f.store_buffer(text.to_utf8_buffer().compress(FileAccess.COMPRESSION_GZIP))
    else:
        f.store_string(text)
    f.close()


## Many small zones: `copies` copies of the minimal bundle (2 zones, 12 tasks each) side by side on one grid, for the
## timeline tests at 600+ zones without the cost of a 20k-task model.
static func zone_heavy(copies: int, cols: int = 20, base_path: String = "res://scenarios/minimal/sequence.json") -> Dictionary:
    var base: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(base_path))
    var out: Dictionary = {}
    for k in ["schema_version", "storeys", "systems", "step_library", "generated_at", "generator", "baseline", "scenario"]:
        if base.has(k):
            var v: Variant = base[k]
            out[k] = v.duplicate(true) if (v is Dictionary or v is Array) else v
    out["project"] = (base["project"] as Dictionary).duplicate(true)
    out["project"]["grid"]["width_cells"] = cols * 8
    out["project"]["grid"]["depth_cells"] = int(ceil(float(copies) / float(cols))) * 8
    var zones: Array = []
    var elements: Array = []
    var tasks: Array = []
    var critical: Array = []
    for i in copies:
        var dx: int = (i % cols) * 8
        var dz: int = (i / cols) * 8
        var pre: String = "K%03d-" % i
        for z in base["zones"]:
            var z2: Dictionary = (z as Dictionary).duplicate()
            z2["id"] = pre + str(z["id"])
            z2["name"] = pre + str(z["name"])
            z2["cells"] = _off(z["cells"], dx, dz)
            zones.append(z2)
        for e in base["elements"]:
            var e2: Dictionary = (e as Dictionary).duplicate()
            e2["guid"] = pre + str(e["guid"])
            e2["zone_id"] = pre + str(e["zone_id"])
            e2["cells"] = _off(e["cells"], dx, dz)
            elements.append(e2)
        for t in base["tasks"]:
            var t2: Dictionary = (t as Dictionary).duplicate()
            t2["task_id"] = "T%06d" % (int(str(t["task_id"]).substr(1)) + i * 100)
            if t.get("element_guid", null) != null:
                t2["element_guid"] = pre + str(t["element_guid"])
            t2["zone_id"] = pre + str(t["zone_id"])
            t2["cells"] = _off(t["cells"], dx, dz)
            var preds: Array = []
            for p in t["predecessors"]:
                var p2: Dictionary = (p as Dictionary).duplicate()
                p2["task_id"] = "T%06d" % (int(str(p["task_id"]).substr(1)) + i * 100)
                preds.append(p2)
            t2["predecessors"] = preds
            tasks.append(t2)
        for id in base["baseline"]["critical_task_ids"]:
            critical.append("T%06d" % (int(str(id).substr(1)) + i * 100))
    out["zones"] = zones
    out["elements"] = elements
    out["tasks"] = tasks
    (out["baseline"] as Dictionary)["critical_task_ids"] = critical
    (out["scenario"] as Dictionary)["id"] = "zone_heavy"
    return out
