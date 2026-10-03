extends Node
## Autoload `Scenarios`: discovers bundles under res://scenarios/<id>/sequence.json or sequence.json.gz and
## hands the selected one to the game scene. The scenario id is the one inside the bundle (`scenario.id`), not
## the folder name; the folder name is accepted wherever an id is looked up.
## Note: exported builds list resources, not directories, so DirAccess may need
## the scenario folders included via export filters (`*.json`, `*.gz`).

const SCENARIO_ROOT: String = "res://scenarios"
const INDEX_CACHE: String = "user://scenario_index.json"

var selected_path: String = ""
var current_bundle: SequenceBundle = null
## Seconds the last select() spent loading the bundle (the menu shows it for big models).
var last_load_ms: float = 0.0
var _index: Dictionary = {}  # path -> {key, info}
var _index_loaded: bool = false
var _index_dirty: bool = false


## The bundle file of a scenario folder (.json, else .json.gz), "" when the folder has none.
static func bundle_path(folder: String) -> String:
    return SequenceBundle.find_in_dir("%s/%s" % [SCENARIO_ROOT, folder])


## Resolves a scenario id or folder name to its bundle path ("" when unknown).
func path_for(id_or_folder: String) -> String:
    var p: String = bundle_path(id_or_folder)
    if p != "":
        return p
    for info in list_bundles():
        if str((info as Dictionary)["id"]) == id_or_folder:
            return str((info as Dictionary)["path"])
    return ""


## Returns [{id, name, path, description, sector, difficulty, tasks}] sorted by id. Reading a bundle for its
## metadata means parsing it (and gunzipping .json.gz), so the result is cached in user:// per file size and
## modification time.
func list_bundles() -> Array[Dictionary]:
    var out: Array[Dictionary] = []
    var dir := DirAccess.open(SCENARIO_ROOT)
    if dir == null:
        return out
    _load_index()
    var names: PackedStringArray = dir.get_directories()
    for n in names:
        var path: String = bundle_path(n)
        if path == "":
            continue
        out.append(_info_for(n, path))
    if _index_dirty:
        _save_index()
    out.sort_custom(func(a: Dictionary, b: Dictionary) -> bool: return str(a["id"]) < str(b["id"]))
    return out


func _info_for(folder: String, path: String) -> Dictionary:
    var key: String = "%d|%d" % [FileAccess.get_size(path) if FileAccess.file_exists(path) else 0, FileAccess.get_modified_time(path)]
    var hit: Variant = _index.get(path, null)
    if hit is Dictionary and str((hit as Dictionary).get("key", "")) == key:
        var cached: Dictionary = (hit as Dictionary)["info"]
        var copy: Dictionary = cached.duplicate()
        copy["path"] = path
        return copy
    var info: Dictionary = {"id": folder, "name": folder, "path": path, "description": "", "sector": "", "difficulty": "", "tasks": 0}
    var parsed: Variant = SequenceBundle.read_json(path)
    if parsed is Dictionary:
        var sc: Dictionary = (parsed as Dictionary).get("scenario", {})
        info["name"] = str(sc.get("name", folder))
        info["description"] = str(sc.get("description", ""))
        info["sector"] = str(sc.get("sector", ""))
        info["difficulty"] = str(sc.get("difficulty", ""))
        info["id"] = str(sc.get("id", folder))
        var tl: Variant = (parsed as Dictionary).get("tasks", [])
        var n: int = (tl as Array).size() if tl is Array else 0
        var fmt: Variant = (parsed as Dictionary).get("bundle_format", {})
        if fmt is Dictionary and (fmt as Dictionary).has("task_count"):
            n = int((fmt as Dictionary)["task_count"])
        info["tasks"] = n
    _index[path] = {"key": key, "info": info.duplicate()}
    _index_dirty = true
    return info


func _load_index() -> void:
    if _index_loaded:
        return
    _index_loaded = true
    var v: Variant = SequenceBundle.read_json(INDEX_CACHE) if FileAccess.file_exists(INDEX_CACHE) else null
    if v is Dictionary:
        _index = v


func _save_index() -> void:
    _index_dirty = false
    var f := FileAccess.open(INDEX_CACHE, FileAccess.WRITE)
    if f != null:
        f.store_string(JSON.stringify(_index))
        f.close()


func select(path: String) -> bool:
    var t0: int = Time.get_ticks_usec()
    var b := SequenceBundle.load_from_path(path)
    last_load_ms = float(Time.get_ticks_usec() - t0) / 1000.0
    if not b.valid:
        push_error("Scenario failed to load: %s" % ", ".join(b.errors))
        return false
    selected_path = path
    current_bundle = b
    return true
