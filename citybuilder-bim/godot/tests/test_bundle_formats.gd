extends TC
## Large-model bundle formats (docs/06 C.2 / C.3): .json.gz, split task parts, per-zone detail files, compact cell
## rectangles, load statistics and the memory policy for big bundles.

const DIR: String = "user://wpu_tests"


func _dir(name: String) -> String:
    var d: String = DIR.path_join(name)
    DirAccess.make_dir_recursive_absolute(d)
    return d


func _clean(dir: String) -> void:
    var da := DirAccess.open(dir)
    if da == null:
        return
    for f in da.get_files():
        da.remove(f)
    for sub in da.get_directories():
        _clean(dir.path_join(sub))
        da.remove(sub)
    DirAccess.remove_absolute(dir)


func _digest(b: SequenceBundle) -> String:
    var ids: PackedStringArray = PackedStringArray()
    for t in b.tasks:
        ids.append("%s:%s:%d" % [t.task_id, t.zone_id, t.predecessors.size()])
    return "|".join(ids)


func test_gzip_bundle_loads_like_the_plain_one() -> void:
    var d: Dictionary = minimal_dict()
    var dir: String = _dir("gz")
    var plain: String = StressGen.write_split(d, dir.path_join("plain"), {"gz": false})
    var gz: String = StressGen.write_split(d, dir.path_join("gz"), {"gz": true})
    ok(gz.ends_with("sequence.json.gz") and plain.ends_with("sequence.json"), "file names")
    var raw: PackedByteArray = FileAccess.get_file_as_bytes(gz)
    ok(raw.size() > 2 and raw[0] == 0x1f and raw[1] == 0x8b, "the file is real gzip (magic bytes)")
    var a := SequenceBundle.load_from_path(plain)
    var b := SequenceBundle.load_from_path(gz)
    ok(a.valid and b.valid, "both load: %s %s" % [a.errors, b.errors])
    eq(_digest(a), _digest(b), "same tasks")
    eq(b.elements.size(), a.elements.size(), "same elements")
    ok(b.load_stats.has("parse_ms") and float(b.load_stats["total_ms"]) > 0.0, "load statistics are reported")
    eq(int(b.load_stats["tasks"]), b.tasks.size(), "stats carry the task count")
    _clean(dir)


func test_parse_bytes_accepts_text_and_gzip() -> void:
    var txt: PackedByteArray = '{"a": [1, 2]}'.to_utf8_buffer()
    eq((SequenceBundle.parse_bytes(txt) as Dictionary)["a"], [1.0, 2.0], "plain JSON bytes")
    var gz: PackedByteArray = txt.compress(FileAccess.COMPRESSION_GZIP)
    eq((SequenceBundle.parse_bytes(gz) as Dictionary)["a"], [1.0, 2.0], "gzip bytes")
    ok(SequenceBundle.parse_bytes("nonsense".to_utf8_buffer()) == null, "garbage is not JSON")


func test_find_in_dir_prefers_plain_json_then_gz() -> void:
    var dir: String = _dir("find")
    ok(SequenceBundle.find_in_dir(dir) == "", "empty folder")
    StressGen.write_split(minimal_dict(), dir, {"gz": true})
    ok(SequenceBundle.find_in_dir(dir).ends_with("sequence.json.gz"), "gz found")
    StressGen.write_split(minimal_dict(), dir, {"gz": false})
    ok(SequenceBundle.find_in_dir(dir).ends_with("sequence.json"), "plain json first")
    _clean(dir)


func test_split_task_parts_load_in_order_and_play() -> void:
    var d: Dictionary = fixture_dict()
    var dir: String = _dir("parts")
    var path: String = StressGen.write_split(d, dir, {"gz": true, "parts": 3})
    var main: Dictionary = SequenceBundle.read_json(path)
    eq((main["tasks"] as Array).size(), 0, "the main file carries no tasks")
    eq((main["bundle_format"]["task_parts"] as Array).size(), 3, "three part files listed")
    var b := SequenceBundle.load_from_path(path)
    ok(b.valid, "split bundle valid: %s" % b.errors)
    eq(b.tasks.size(), (d["tasks"] as Array).size(), "every task loaded")
    eq(int(b.load_stats["parts"]), 3, "parts counted")
    var whole := SequenceBundle.from_dictionary(d)
    eq(_digest(b), _digest(whole), "same tasks in the same order as the unsplit bundle")
    var gs := SimState.new()
    ok(gs.start(b), "starts")
    ok(gs.advance_week(), "plays a week")
    gs.free()
    _clean(dir)


func test_parts_may_be_objects_with_a_tasks_list() -> void:
    var d: Dictionary = minimal_dict()
    var dir: String = _dir("objparts")
    var tasks: Array = d["tasks"]
    var half: int = tasks.size() / 2
    var f1 := FileAccess.open(dir.path_join("tasks.part-0.json"), FileAccess.WRITE)
    f1.store_string(JSON.stringify({"schema_version": "1.0", "part": 0, "tasks": tasks.slice(0, half)}))
    f1.close()
    var f2 := FileAccess.open(dir.path_join("tasks.part-1.json"), FileAccess.WRITE)
    f2.store_string(JSON.stringify({"schema_version": "1.0", "part": 1, "tasks": tasks.slice(half)}))
    f2.close()
    d["tasks"] = []
    d["bundle_format"] = {"compressed": false, "task_parts": ["tasks.part-0.json", "tasks.part-1.json"]}
    var f := FileAccess.open(dir.path_join("sequence.json"), FileAccess.WRITE)
    f.store_string(JSON.stringify(d))
    f.close()
    var b := SequenceBundle.load_from_path(dir.path_join("sequence.json"))
    ok(b.valid, "valid: %s" % b.errors)
    eq(b.tasks.size(), tasks.size(), "all tasks of both objects")
    _clean(dir)


func test_missing_part_is_an_error() -> void:
    var d: Dictionary = minimal_dict()
    var dir: String = _dir("missing")
    d["tasks"] = []
    d["bundle_format"] = {"task_parts": ["nope.json.gz"]}
    var f := FileAccess.open(dir.path_join("sequence.json"), FileAccess.WRITE)
    f.store_string(JSON.stringify(d))
    f.close()
    var b := SequenceBundle.load_from_path(dir.path_join("sequence.json"))
    ok(not b.valid, "a missing part makes the bundle invalid")
    ok(", ".join(b.errors).contains("nope.json.gz"), "the error names the file")
    _clean(dir)


func test_zone_detail_is_loaded_on_first_access() -> void:
    var d: Dictionary = minimal_dict()
    var dir: String = _dir("detail")
    var path: String = StressGen.write_split(d, dir, {"gz": true, "detail": true})
    var b := SequenceBundle.load_from_path(path)
    ok(b.valid and b.has_lazy_detail(), "light bundle with a detail directory")
    var t: TaskData = b.tasks_by_id["T000001"]
    eq(t.element_name, "", "the light row has no element name")
    ok(not t.detail_loaded and not b.zone_detail_loaded("L00-Z1"), "detail pending")
    var n: int = b.ensure_zone_detail("L00-Z1")
    ok(n > 0, "detail rows merged (%d)" % n)
    eq(t.element_name, "Footing F1", "the element name came from the zone file")
    eq(t.ifc_class, "IfcFooting", "so did the class")
    ok(t.detail_loaded and b.zone_detail_loaded("L00-Z1"), "loaded")
    eq(b.ensure_zone_detail("L00-Z1"), 0, "a second access reads nothing")
    var other: TaskData = b.tasks_by_id["T000009"]
    ok(not other.detail_loaded, "the other zone is still light")
    var gs := SimState.new()
    gs.start(b)
    ok(gs.ensure_zone_detail("L01-Z1") > 0, "SimState.ensure_zone_detail goes through the bundle")
    eq(other.element_name, "Slab L01", "slab name merged")
    gs.free()
    _clean(dir)


func test_zone_detail_members_fill_aggregate_elements() -> void:
    var d: Dictionary = minimal_dict()
    var dir: String = _dir("members")
    DirAccess.make_dir_recursive_absolute(dir.path_join("zones"))
    d["bundle_format"] = {"zone_detail_dir": "zones"}
    var zf := FileAccess.open(dir.path_join("zones").path_join("L00-Z1.json"), FileAccess.WRITE)
    zf.store_string(JSON.stringify({"zone_id": "L00-Z1", "task_ids": [], "package_ids": [], "members": {"FOOT0": ["M-1", "M-2"]}}))
    zf.close()
    var f := FileAccess.open(dir.path_join("sequence.json"), FileAccess.WRITE)
    f.store_string(JSON.stringify(d))
    f.close()
    var b := SequenceBundle.load_from_path(dir.path_join("sequence.json"))
    ok(b.valid, "valid")
    var e: ElementData = b.elements_by_guid["FOOT0"]
    ok(e.member_guids.is_empty(), "members are not loaded yet")
    b.ensure_zone_detail("L00-Z1")
    eq(e.member_guids, ["M-1", "M-2"] as Array[String], "member guids merged")
    _clean(dir)


func test_cell_rects_are_the_compact_form_of_cells() -> void:
    var d: Dictionary = minimal_dict()
    var t: Dictionary = (d["tasks"] as Array)[0]
    t.erase("cells")
    t["cell_rects"] = [[2, 2, 2, 2]]
    var e: Dictionary = (d["elements"] as Array)[0]
    e.erase("cells")
    e["cell_rects"] = [[3, 3, 1, 2]]
    var b := SequenceBundle.from_dictionary(d)
    ok(b.valid, "valid")
    var task: TaskData = b.tasks[0]
    eq(task.cells.size(), 4, "a 2x2 rectangle is four cells")
    ok(task.cells.has(Vector2i(3, 3)) and task.cells.has(Vector2i(2, 3)), "cells of the rectangle")
    eq(b.elements[0].cells, [Vector2i(3, 3), Vector2i(3, 4)] as Array[Vector2i], "element rectangle 1x2")


func test_task_cells_decode_lazily() -> void:
    var b := load_bundle()
    var t: TaskData = b.tasks[0]
    ok(t._cells_src != null, "the cell list is kept raw until first use")
    var n: int = t.cells.size()
    ok(n >= 1 and t._cells_src == null, "decoded on first access (%d cells)" % n)
    t.cells = [Vector2i(9, 9)] as Array[Vector2i]
    eq(t.cells, [Vector2i(9, 9)] as Array[Vector2i], "assignment replaces the cells")


func test_big_bundles_drop_raw_rows_and_reload_for_pristine_copies() -> void:
    var d: Dictionary = StressGen.zone_heavy(400)  # 4800 tasks: above KEEP_RAW_MAX_TASKS
    var dir: String = _dir("big")
    var path: String = StressGen.write_split(d, dir, {"gz": true})
    var b := SequenceBundle.load_from_path(path)
    ok(b.valid, "valid: %s" % b.errors)
    eq(b.tasks.size(), 4800, "4800 tasks")
    ok(not b.keep_raw and b.tasks[0].raw.is_empty(), "no per-task JSON dictionaries kept")
    ok(b.source_dict.is_empty(), "no source dictionary kept")
    ok(not b.tasks[0].export_dict().is_empty(), "exports are built from the typed fields")
    var fresh: SequenceBundle = b.pristine_copy()
    ok(fresh.valid and fresh.tasks.size() == 4800, "pristine_copy re-reads the file")
    var small := load_bundle()
    ok(small.keep_raw and not small.source_dict.is_empty(), "small bundles keep both")
    _clean(dir)


func test_areas_are_parsed_from_the_project() -> void:
    var d: Dictionary = minimal_dict()
    d["project"]["areas"] = [
        {"id": "A", "name": "Ground area", "cells": [[2, 2], [3, 2], [2, 3], [3, 3]], "storey_ids": ["L00"]},
        {"id": "B", "name": "Everything", "storey_ids": ["L00", "L01"], "camera_bookmark": {"yaw": 90}},
    ]
    var b := SequenceBundle.from_dictionary(d)
    ok(b.valid, "valid")
    eq(b.areas.size(), 2, "two areas")
    var a: Dictionary = b.areas_by_id["A"]
    eq((a["cells"] as Array).size(), 4, "cells")
    eq(a["zone_ids"], ["L00-Z1"] as Array[String], "the zone whose centre lies in the area")
    var all: Dictionary = b.areas_by_id["B"]
    eq((all["zone_ids"] as Array).size(), 2, "an area without cells holds the zones of its storeys")
    ok((all["cells"] as Array).size() >= 8, "and their cells")
    eq(int(all["camera"]["yaw"]), 90, "camera bookmark kept")


func test_scenarios_resolve_folders_and_scenario_ids() -> void:
    var sc: Node = Engine.get_main_loop().root.get_node("Scenarios")
    ok(str(sc.call("path_for", "minimal")).ends_with("minimal/sequence.json"), "folder name resolves")
    eq(str(sc.call("path_for", "no_such_scenario")), "", "unknown name")
    var list: Array = sc.call("list_bundles")
    ok(list.size() >= 5, "bundles listed")
    for info in list:
        var d: Dictionary = info
        eq(str(sc.call("path_for", str(d["id"]))), str(d["path"]), "scenario id %s resolves to its bundle" % str(d["id"]))
    # the second listing comes from the cache and says the same
    var again: Array = sc.call("list_bundles")
    eq(again.size(), list.size(), "cached listing has the same entries")
    ok(FileAccess.file_exists("user://scenario_index.json"), "the index cache was written")


func test_area_zone_ids_given_by_the_bundle_are_used() -> void:
    var d: Dictionary = minimal_dict()
    d["project"]["areas"] = [{"id": "Z", "name": "Named zones", "zone_ids": ["L01-Z1", "nope"]}]
    var b := SequenceBundle.from_dictionary(d)
    ok(b.valid, "valid")
    var a: Dictionary = b.areas_by_id["Z"]
    eq(a["zone_ids"], ["L01-Z1"] as Array[String], "listed zones that exist")
    eq((a["cells"] as Array).size(), 4, "cells come from the zones")
