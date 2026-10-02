extends Node
## Autoload `Scenarios`: discovers bundles under res://scenarios/<id>/sequence.json and
## hands the selected one to the game scene.
## Note: exported builds list resources, not directories, so DirAccess may need
## the scenario folders included via export filters (fine for now).

const SCENARIO_ROOT: String = "res://scenarios"

var selected_path: String = ""
var current_bundle: SequenceBundle = null


## Returns [{id, name, path, description, sector, difficulty}] sorted by id.
func list_bundles() -> Array[Dictionary]:
    var out: Array[Dictionary] = []
    var dir := DirAccess.open(SCENARIO_ROOT)
    if dir == null:
        return out
    var names: PackedStringArray = dir.get_directories()
    for n in names:
        var path: String = "%s/%s/sequence.json" % [SCENARIO_ROOT, n]
        if not FileAccess.file_exists(path):
            continue
        var info: Dictionary = {"id": n, "name": n, "path": path, "description": "", "sector": "", "difficulty": ""}
        var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(path))
        if parsed is Dictionary:
            var sc: Dictionary = (parsed as Dictionary).get("scenario", {})
            info["name"] = str(sc.get("name", n))
            info["description"] = str(sc.get("description", ""))
            info["sector"] = str(sc.get("sector", ""))
            info["difficulty"] = str(sc.get("difficulty", ""))
            info["id"] = str(sc.get("id", n))
        out.append(info)
    out.sort_custom(func(a: Dictionary, b: Dictionary) -> bool: return str(a["id"]) < str(b["id"]))
    return out


func select(path: String) -> bool:
    var b := SequenceBundle.load_from_path(path)
    if not b.valid:
        push_error("Scenario failed to load: %s" % ", ".join(b.errors))
        return false
    selected_path = path
    current_bundle = b
    return true
