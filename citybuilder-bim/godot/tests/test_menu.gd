extends TC


func test_scenarios_discovers_bundles() -> void:
    var sc: Node = Engine.get_main_loop().root.get_node("Scenarios")
    var list: Array = sc.call("list_bundles")
    var ids: Array[String] = []
    for info in list:
        ids.append(str((info as Dictionary)["id"]))
    ok(ids.has("minimal"), "minimal bundle listed: %s" % str(ids))
    for info in list:
        var d: Dictionary = info
        ok(FileAccess.file_exists(str(d["path"])), "path exists for %s" % d["id"])


func test_every_bundle_loads() -> void:
    var sc: Node = Engine.get_main_loop().root.get_node("Scenarios")
    for info in sc.call("list_bundles"):
        var d: Dictionary = info
        var b := SequenceBundle.load_from_path(str(d["path"]))
        ok(b.valid, "%s loads: %s" % [d["id"], ", ".join(b.errors)])
        if b.valid:
            var gs := SimState.new()
            ok(gs.start(b), "%s starts" % d["id"])
            ok(gs.advance_week(), "%s advances a week" % d["id"])
            gs.free()


func test_menu_scene_lists_scenarios() -> void:
    var menu: Node = (load("res://scenes/menu.tscn") as PackedScene).instantiate()
    Engine.get_main_loop().root.add_child(menu)
    var buttons: int = 0
    var stack: Array[Node] = [menu]
    while not stack.is_empty():
        var n: Node = stack.pop_back()
        if n is Button and (n as Button).text.contains("Minimal"):
            buttons += 1
        for c in n.get_children():
            stack.append(c)
    ok(buttons >= 1, "a button for the minimal scenario")
    Engine.get_main_loop().root.remove_child(menu)
    menu.free()
