extends TC
## Screenshot harness (tools/screenshot.gd): argument parsing everywhere, and one real render when a display exists.

const SHOT: GDScript = preload("res://tools/screenshot.gd")


func _args(list: Array) -> Dictionary:
    return SHOT.call("parse_args", PackedStringArray(list))


func test_parse_defaults_and_values() -> void:
    var o: Dictionary = _args(["--out=/tmp/a.png"])
    ok(bool(o["ok"]), "only --out is required: %s" % str(o["error"]))
    eq(o["scenario"], "minimal", "default scenario")
    eq(int(o["weeks"]), 0, "default weeks")
    eq(o["view_kind"], "overview", "default view")
    eq(o["size"], Vector2i(1280, 720), "default size")
    var p: Dictionary = _args(["--scenario=healthcare_standard", "--weeks=15", "--view=zone:L00-Z3",
            "--panels=gantt,editor,legend", "--hide=crews", "--size=1920x1080", "--scale=0.5", "--zone=L00-Z3", "--out=x/y.png"])
    ok(bool(p["ok"]), "full argument list parses: %s" % str(p["error"]))
    eq(p["scenario"], "healthcare_standard", "scenario")
    eq(int(p["weeks"]), 15, "weeks")
    eq(p["view_kind"], "zone", "view kind")
    eq(p["view_arg"], "L00-Z3", "view argument")
    eq(p["panels"], ["gantt", "editor", "legend"], "panels")
    eq(p["hide"], ["crews"], "hide list")
    eq(p["size"], Vector2i(1920, 1080), "size")
    near(float(p["scale"]), 0.5, "scale")
    eq(p["zone"], "L00-Z3", "zone")
    eq(p["out"], "x/y.png", "out path")
    eq(_args(["--view=storey:2", "--out=a.png"])["view_arg"], "2", "storey view")
    eq(_args(["--view=installation:7", "--out=a.png"])["view_kind"], "installation", "installation view")


func test_parse_refuses_bad_args() -> void:
    var bad: Array = [
        [],  # no --out
        ["--out=a.txt"],
        ["--out=a.png", "--weeks=abc"],
        ["--out=a.png", "--weeks=-1"],
        ["--out=a.png", "--view=moon"],
        ["--out=a.png", "--view=zone:"],
        ["--out=a.png", "--view=storey:x"],
        ["--out=a.png", "--view=installation"],
        ["--out=a.png", "--panels=gantt,nonsense"],
        ["--out=a.png", "--hide=report"],
        ["--out=a.png", "--size=1280"],
        ["--out=a.png", "--size=10x10"],
        ["--out=a.png", "--scale=0"],
        ["--out=a.png", "--scenario=../x"],
        ["--out=a.png", "--bogus=1"],
        ["--out=a.png", "stray"],
    ]
    for a in bad:
        var o: Dictionary = _args(a)
        ok(not bool(o["ok"]), "refused: %s" % str(a))
        ok(str(o["error"]) != "", "has an error message: %s" % str(a))


func test_render_unknown_scenario_fails_cleanly() -> void:
    var o: Dictionary = _args(["--scenario=does_not_exist", "--out=user://shots_test/none.png"])
    ok(bool(o["ok"]), "parses")
    var res: Dictionary = await SHOT.call("render", Engine.get_main_loop(), o)
    ok(not bool(res["ok"]), "render reports an error")
    ok(str(res["error"]).contains("not found"), "message names the problem: %s" % str(res["error"]))


func test_render_minimal_overview_when_display_available() -> void:
    if OS.has_feature("headless") or DisplayServer.get_name() == "headless":
        print("SKIP  test_screenshots::render (headless: no rendering display; use xvfb-run with --rendering-driver opengl3)")
        ok(true, "skipped without a display")
        return
    var path: String = "user://shots_test/minimal_overview.png"
    var o: Dictionary = _args(["--scenario=minimal", "--weeks=0", "--view=overview", "--size=1280x720", "--out=" + path])
    ok(bool(o["ok"]), "args parse")
    var res: Dictionary = await SHOT.call("render", Engine.get_main_loop(), o)
    ok(bool(res["ok"]), "render succeeded: %s" % str(res["error"]))
    ok(FileAccess.file_exists(path), "PNG exists")
    ok(int(res["bytes"]) > 20000, "PNG is non-trivial (%d bytes)" % int(res["bytes"]))
    var img := Image.load_from_file(ProjectSettings.globalize_path(path))
    ok(img != null and not img.is_empty(), "PNG loads")
    if img != null and not img.is_empty():
        eq(img.get_width(), 1280, "width")
        eq(img.get_height(), 720, "height")
        var distinct: Dictionary = {}
        for y in range(0, img.get_height(), 40):
            for x in range(0, img.get_width(), 40):
                distinct[img.get_pixel(x, y).to_html(false)] = true
        ok(distinct.size() > 12, "image is not a flat colour (%d distinct samples)" % distinct.size())
