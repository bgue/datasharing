class_name SaveGame
extends RefCounted
## Save / load the running level to user://save_<scenario_id>.json (F1 / F2).


static func path_for(scenario_id: String) -> String:
    return "user://save_%s.json" % scenario_id


static func save(gs: SimState, path: String = "") -> bool:
    var p: String = path if path != "" else path_for(gs.scenario.id)
    var f := FileAccess.open(p, FileAccess.WRITE)
    if f == null:
        push_error("save failed: %s" % p)
        return false
    f.store_string(JSON.stringify(gs.serialize()))
    f.close()
    return true


static func has_save(scenario_id: String) -> bool:
    return FileAccess.file_exists(path_for(scenario_id))


static func load_into(gs: SimState, path: String = "") -> bool:
    var p: String = path if path != "" else path_for(gs.scenario.id)
    if not FileAccess.file_exists(p):
        gs.last_error = "no save file"
        return false
    var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(p))
    if not (parsed is Dictionary):
        gs.last_error = "corrupt save"
        return false
    return gs.deserialize(parsed)
