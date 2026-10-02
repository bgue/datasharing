extends SceneTree
## Headless test runner:
##   godot --headless --path godot --script res://tests/run_tests.gd
## Loads every tests/test_*.gd, runs methods starting with `test_`, prints PASS/FAIL.

var _failures: Array[String] = []
var _checks: int = 0
var _current: String = ""


func _initialize() -> void:
    # Wait one frame so the root window is ready: scenes added by tests then run _ready().
    await process_frame
    _run()


func _run() -> void:
    var total: int = 0
    var failed: int = 0
    var files: Array[String] = []
    var dir := DirAccess.open("res://tests")
    if dir == null:
        printerr("cannot open res://tests")
        quit(1)
        return
    for f in dir.get_files():
        if f.begins_with("test_") and f.ends_with(".gd"):
            files.append(f)
    files.sort()
    var filter: String = ""
    for a in OS.get_cmdline_user_args():
        filter = a
    for f in files:
        if filter != "" and not f.contains(filter):
            continue
        var script: GDScript = load("res://tests/" + f)
        if script == null:
            print("FAIL  %s (could not load)" % f)
            total += 1
            failed += 1
            continue
        if not script.can_instantiate():
            print("FAIL  %s (script does not compile)" % f)
            total += 1
            failed += 1
            continue
        var inst: Object = script.new()
        if inst.has_method("set_runner"):
            inst.call("set_runner", self)
        for m in inst.get_method_list():
            var mname: String = m["name"]
            if not mname.begins_with("test_"):
                continue
            total += 1
            _failures.clear()
            _current = "%s::%s" % [f.get_basename(), mname]
            if inst.has_method("before_each"):
                inst.call("before_each")
            await inst.call(mname)  # tests may be coroutines (await process_frame)
            if inst.has_method("after_each"):
                inst.call("after_each")
            if _failures.is_empty():
                print("PASS  %s" % _current)
            else:
                failed += 1
                print("FAIL  %s" % _current)
                for msg in _failures:
                    print("      - %s" % msg)
        if inst is Node:
            (inst as Node).free()
    print("")
    print("SUMMARY: %d tests, %d passed, %d failed, %d assertions" % [total, total - failed, failed, _checks])
    quit(1 if failed > 0 else 0)


## Called by test classes: assert_true(cond, message).
func check(cond: bool, message: String) -> void:
    _checks += 1
    if not cond:
        _failures.append(message)
