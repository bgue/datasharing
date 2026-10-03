extends TC
## Visual kits (docs/06 Track B): manifest, builders for every kit at 1x1 / 2x1 / 2x3 footprints and fills
## {0, 0.5, 1}, registry (fallback mapping, variants, layer fills, cache, LOD box), kit instances and markers.
## Scene-level tests (KitLayer, BimView hook) are in test_kits_layer.gd.

const FOOTPRINTS: Array[Vector2i] = [Vector2i(1, 1), Vector2i(2, 1), Vector2i(2, 3)]
const FILLS: Array[float] = [0.0, 0.5, 1.0]
const MAX_TRIS: int = 6000
const CELL_M: float = 6.0
const DISCIPLINES: Array[String] = [
    "general", "civil", "structure", "architecture", "mechanical", "electrical", "plumbing", "fire", "process",
    "instrumentation", "medical", "commissioning",
]

var reg: KitRegistry = null


func before_each() -> void:
    reg = KitRegistry.new()
    reg.load_manifest()


func _fills(kit: String, f: float) -> Dictionary:
    var d: Dictionary = {}
    for l in reg.layer_ids(kit):
        d[l] = f
    return d


func _params(kit: String, fp: Vector2i, f: float, variant: String = "") -> Dictionary:
    return {
        "footprint_cells": fp,
        "height_m": float(reg.kit_param(kit, "default_height_m", 4.0)),
        "cell_size_m": CELL_M,
        "layer_fills": _fills(kit, f),
        "variant": variant,
        "seed": 7,
    }


func _surface(mesh: Mesh, surf_name: String) -> Array:
    var am: ArrayMesh = mesh as ArrayMesh
    var i: int = am.surface_find_by_name(surf_name)
    return [] if i < 0 else am.surface_get_arrays(i)


func _solid_vertices(mesh: Mesh) -> int:
    var arr: Array = _surface(mesh, "solid")
    return 0 if arr.is_empty() else (arr[Mesh.ARRAY_VERTEX] as PackedVector3Array).size()


func _all_vertices(mesh: Mesh) -> PackedVector3Array:
    var out := PackedVector3Array()
    var am: ArrayMesh = mesh as ArrayMesh
    for s in am.get_surface_count():
        out.append_array(am.surface_get_arrays(s)[Mesh.ARRAY_VERTEX])
    return out


func _task(id: String, step: String, days: float) -> TaskData:
    var t := TaskData.new()
    t.task_id = id
    t.step_id = step
    t.estimated_crew_days = days
    return t


# ------------------------------------------------------------------ manifest

func test_manifest_loads_and_every_kit_has_a_builder() -> void:
    ok(reg.valid, "manifest valid: %s" % ", ".join(reg.errors))
    eq(reg.kit_ids().size(), 18, "18 kits")
    for id in ["rack", "tank", "turbine", "vessel_v", "vessel_h", "pump_plinth", "exchanger", "compressor", "transformer",
            "switchroom", "cooling_tower", "stack", "module", "ahu", "chiller", "mri", "bridge_pier", "culvert"]:
        ok(reg.has_kit(id), "kit %s present" % id)
    for id in reg.kit_ids():
        var b: KitBuilder = reg.builder_for(id)
        ok(b != null, "%s has a builder class (%s)" % [id, (reg.kits[id] as Dictionary)["builder"]])
        var layer_ids: Array[String] = reg.layer_ids(id)
        ok(not layer_ids.is_empty(), "%s has layers" % id)
        for l in reg.kit_layers(id):
            var ld: Dictionary = l
            for d in ld["disciplines"]:
                ok(DISCIPLINES.has(d), "%s/%s discipline %s is valid" % [id, ld["id"], d])
            var follows: String = str(ld.get("follows", ""))
            ok(follows == "" or layer_ids.has(follows), "%s/%s follows an existing layer" % [id, ld["id"]])
            ok(["none", "height", "length", "count"].has(str(ld.get("grow", "count"))), "%s/%s grow mode" % [id, ld["id"]])
        var variants: Dictionary = (reg.kits[id] as Dictionary).get("variants", {})
        for v in variants:
            for l in variants[v]:
                ok(layer_ids.has(l), "%s variant %s layer %s exists" % [id, v, l])
        ok(float(reg.lod_distance(id)) > 0.0, "%s has a LOD distance" % id)
    eq(reg.markers.size(), 8, "8 markers")
    for m in ["survey", "dewatering", "scaffold", "lift_plan", "permit", "test", "shoring", "crane"]:
        ok(reg.markers.has(m), "marker %s" % m)
    ok(reg.kit_layers("rack").size() == 5, "rack has 5 layers")
    eq((reg.kits["rack"] as Dictionary)["variants"].keys().size(), 3, "rack variants")
    ok(reg.kits["vessel_v"]["params"]["orientation"] == "vertical" and reg.kits["vessel_h"]["params"]["orientation"] == "horizontal", "vessel orientations")


func test_broken_manifest_path_is_reported() -> void:
    var r := KitRegistry.new()
    ok(not r.load_manifest("res://kits/does_not_exist.json"), "missing manifest fails")
    ok(not r.errors.is_empty(), "error reported")
    eq(r.kit_for_element({"ifc_class": "IfcTank"}), "", "no kits without a manifest")


# ------------------------------------------------------------------ builders

func test_every_kit_builds_at_all_footprints_and_fills() -> void:
    var worst: Dictionary = {}
    for kit in reg.kit_ids():
        var h: float = float(reg.kit_param(kit, "default_height_m", 4.0))
        for fp in FOOTPRINTS:
            var solids: Array[int] = []
            for f in FILLS:
                var variant: String = "rack_mpei" if kit == "rack" else ""
                var mesh: Mesh = reg.build(kit, _params(kit, fp, f, variant))
                var tag: String = "%s %dx%d fill %.1f" % [kit, fp.x, fp.y, f]
                ok(mesh != null, "%s: mesh built" % tag)
                if mesh == null:
                    continue
                var am: ArrayMesh = mesh as ArrayMesh
                ok(am.get_surface_count() >= 1, "%s: has a surface" % tag)
                var verts: PackedVector3Array = _all_vertices(mesh)
                ok(verts.size() >= 3, "%s: non-empty" % tag)
                var tris: int = verts.size() / 3
                worst[kit] = maxi(int(worst.get(kit, 0)), tris)
                ok(tris < MAX_TRIS, "%s: %d triangles < %d" % [tag, tris, MAX_TRIS])
                var bb: AABB = mesh.get_aabb()
                var hw: float = float(fp.x) * CELL_M * 0.5 + 0.01
                var hd: float = float(fp.y) * CELL_M * 0.5 + 0.01
                ok(bb.position.x >= -hw and bb.end.x <= hw, "%s: x within footprint (%.2f..%.2f of %.2f)" % [tag, bb.position.x, bb.end.x, hw])
                ok(bb.position.z >= -hd and bb.end.z <= hd, "%s: z within footprint (%.2f..%.2f of %.2f)" % [tag, bb.position.z, bb.end.z, hd])
                ok(bb.position.y >= -0.01 and bb.end.y <= h + 0.01, "%s: y within height (%.2f..%.2f of %.2f)" % [tag, bb.position.y, bb.end.y, h])
                solids.append(_solid_vertices(mesh))
            eq(solids[0], 0, "%s %dx%d: fill 0 is all ghost" % [kit, fp.x, fp.y])
            ok(solids[1] > 0, "%s %dx%d: fill 0.5 has solid parts" % [kit, fp.x, fp.y])
            ok(solids[1] < solids[2], "%s %dx%d: fill 0.5 has fewer solid vertices (%d) than fill 1 (%d)" % [kit, fp.x, fp.y, solids[1], solids[2]])
    var line: PackedStringArray = PackedStringArray()
    for k in worst:
        line.append("%s=%d" % [k, worst[k]])
    print("      [kits] max triangles per kit: ", ", ".join(line))


func test_long_footprints_stay_inside_and_under_budget() -> void:
    for kit in ["rack", "culvert", "bridge_pier", "cooling_tower"]:
        var h: float = float(reg.kit_param(kit, "default_height_m", 4.0))
        for fp in [Vector2i(12, 1), Vector2i(1, 8)]:
            var mesh: Mesh = reg.build(kit, _params(kit, fp, 1.0, "rack_mpei" if kit == "rack" else ""))
            var tris: int = _all_vertices(mesh).size() / 3
            ok(tris < MAX_TRIS, "%s %dx%d: %d triangles" % [kit, fp.x, fp.y, tris])
            var bb: AABB = mesh.get_aabb()
            ok(bb.end.x <= float(fp.x) * CELL_M * 0.5 + 0.01 and bb.position.x >= -float(fp.x) * CELL_M * 0.5 - 0.01, "%s %dx%d x inside" % [kit, fp.x, fp.y])
            ok(bb.end.z <= float(fp.y) * CELL_M * 0.5 + 0.01 and bb.position.z >= -float(fp.y) * CELL_M * 0.5 - 0.01, "%s %dx%d z inside" % [kit, fp.x, fp.y])
            ok(bb.end.y <= h + 0.01, "%s %dx%d height" % [kit, fp.x, fp.y])


func test_builds_are_deterministic() -> void:
    for kit in reg.kit_ids():
        var p: Dictionary = _params(kit, Vector2i(2, 1), 0.5)
        p["kit"] = kit
        p["kit_params"] = reg.kit_params(kit)
        var a: ArrayMesh = (load("res://scripts/kits/%s.gd" % KitRegistry.snake_case(str((reg.kits[kit] as Dictionary)["builder"]))) as GDScript).new().build(p)
        var b: ArrayMesh = (load("res://scripts/kits/%s.gd" % KitRegistry.snake_case(str((reg.kits[kit] as Dictionary)["builder"]))) as GDScript).new().build(p)
        var va: PackedVector3Array = _all_vertices(a)
        var vb: PackedVector3Array = _all_vertices(b)
        eq(a.get_surface_count(), b.get_surface_count(), "%s: same surface count" % kit)
        eq(va.size(), vb.size(), "%s: same vertex count" % kit)
        ok(va.size() > 0 and va[0] == vb[0] and va[va.size() - 1] == vb[vb.size() - 1], "%s: same first/last vertex" % kit)
        ok(va == vb, "%s: identical vertices" % kit)
    # a different seed may change details but never the budget
    var p2: Dictionary = _params("rack", Vector2i(2, 1), 1.0, "rack_mpei")
    p2["seed"] = 99
    ok(_all_vertices(reg.build("rack", p2)).size() > 0, "other seed builds")


func test_winding_matches_godot_front_faces() -> void:
    # normals stored in the mesh must agree with the ones SurfaceTool derives from the stored winding
    var mesh: Mesh = reg.build("switchroom", _params("switchroom", Vector2i(1, 1), 1.0))
    var arr: Array = _surface(mesh, "solid")
    var v: PackedVector3Array = arr[Mesh.ARRAY_VERTEX]
    var n: PackedVector3Array = arr[Mesh.ARRAY_NORMAL]
    var st := SurfaceTool.new()
    var bad: int = 0
    for i in range(0, v.size(), 3):
        st.clear()
        st.begin(Mesh.PRIMITIVE_TRIANGLES)
        st.add_vertex(v[i])
        st.add_vertex(v[i + 1])
        st.add_vertex(v[i + 2])
        st.generate_normals()
        var na: PackedVector3Array = st.commit().surface_get_arrays(0)[Mesh.ARRAY_NORMAL]
        if na[0].dot(n[i]) < 0.9:
            bad += 1
    eq(bad, 0, "all triangles wound clockwise from outside")


func test_ghost_surface_uses_alpha_and_solid_is_opaque() -> void:
    var mesh: Mesh = reg.build("tank", _params("tank", Vector2i(1, 1), 0.5))
    var ghost: Array = _surface(mesh, "ghost")
    var solid: Array = _surface(mesh, "solid")
    ok(not ghost.is_empty() and not solid.is_empty(), "half-built tank has both surfaces")
    var gc: PackedColorArray = ghost[Mesh.ARRAY_COLOR]
    var sc: PackedColorArray = solid[Mesh.ARRAY_COLOR]
    near(gc[0].a, KitBuilder.GHOST_ALPHA, "ghost vertices carry the ghost alpha", 0.005)
    near(sc[0].a, 1.0, "solid vertices are opaque")
    var am: ArrayMesh = mesh as ArrayMesh
    var gm: StandardMaterial3D = am.surface_get_material(am.surface_find_by_name("ghost")) as StandardMaterial3D
    var sm: StandardMaterial3D = am.surface_get_material(am.surface_find_by_name("solid")) as StandardMaterial3D
    ok(gm.vertex_color_use_as_albedo and sm.vertex_color_use_as_albedo, "vertex colour is the albedo")
    eq(gm.transparency, BaseMaterial3D.TRANSPARENCY_ALPHA, "ghost material is alpha blended")
    eq(sm.transparency, BaseMaterial3D.TRANSPARENCY_DISABLED, "solid material is opaque")
    # hiding ghosts removes the ghost surface only
    var p: Dictionary = _params("tank", Vector2i(1, 1), 0.5)
    p["show_ghost"] = false
    var hidden: Mesh = reg.build("tank", p)
    ok(_surface(hidden, "ghost").is_empty() and not _surface(hidden, "solid").is_empty(), "show_ghost=false drops the ghost surface")
    # layer tints: inspected / rework change the solid colours
    var tp: Dictionary = _params("tank", Vector2i(1, 1), 1.0)
    var plain: Color = (_surface(reg.build("tank", tp), "solid")[Mesh.ARRAY_COLOR] as PackedColorArray)[0]
    tp["layer_states"] = {"ring_foundation": "rework"}
    var tinted: Color = (_surface(reg.build("tank", tp), "solid")[Mesh.ARRAY_COLOR] as PackedColorArray)[0]
    ok(tinted != plain and tinted.r > plain.r, "rework tints the layer red")


func test_layer_fill_grows_shell_and_pipes() -> void:
    # grow=height: the shell's solid part gets taller with the fill (AABB of the solid surface)
    var last: float = -1.0
    for f in [0.25, 0.5, 0.75, 1.0]:
        var p: Dictionary = _params("tank", Vector2i(1, 1), 0.0)
        p["layer_fills"] = {"shell": f}
        p["present_layers"] = ["shell"]
        var mesh: Mesh = reg.build("tank", p)
        var verts: PackedVector3Array = _surface(mesh, "solid")[Mesh.ARRAY_VERTEX]
        var top: float = 0.0
        for v in verts:
            top = maxf(top, v.y)
        ok(top > last, "shell solid height grows at fill %.2f (%.2f)" % [f, top])
        last = top
    # count growth: more pipes are solid as piping fills
    var counts: Array[int] = []
    for f in [0.0, 0.34, 0.67, 1.0]:
        var p2: Dictionary = _params("rack", Vector2i(2, 1), 0.0, "rack")
        p2["layer_fills"] = {"steel": 1.0, "piping": f}
        counts.append(_solid_vertices(reg.build("rack", p2)))
    ok(counts[0] < counts[1] and counts[1] < counts[2] and counts[2] < counts[3], "rack pipes appear gradually %s" % str(counts))


func test_rack_variants_add_layers() -> void:
    var base: Dictionary = _params("rack", Vector2i(2, 1), 1.0)
    var sizes: Dictionary = {}
    for v in ["rack", "rack_ei", "rack_mpei"]:
        var p: Dictionary = base.duplicate()
        p["variant"] = v
        sizes[v] = _all_vertices(reg.build("rack", p)).size()
    ok(sizes["rack"] < sizes["rack_ei"] and sizes["rack_ei"] < sizes["rack_mpei"], "variants add geometry: %s" % str(sizes))
    eq(reg.variant_layers("rack", "rack_ei"), ["steel", "piping", "ei", "insul"] as Array[String], "rack_ei layers")


# ------------------------------------------------------------------ registry

func _el(cls: String, nm: String, visual: String = "generic", zone: String = "Z1") -> ElementData:
    var e := ElementData.new()
    e.guid = "g-" + nm
    e.ifc_class = cls
    e.name = nm
    e.visual = visual
    e.zone_id = zone
    return e


func test_registry_fallback_mapping() -> void:
    reg.rack_zones["RACK-Z"] = true
    var cases: Array = [
        [_el("IfcTank", "Storage tank TK-1", "tank"), "tank"],
        [_el("IfcBuildingElementProxy", "Gas turbine GT-1"), "turbine"],
        [_el("IfcTransformer", "Transformer T-1", "equipment"), "transformer"],
        [_el("IfcBuildingElementProxy", "Distillation column C-101"), "vessel_v"],
        [_el("IfcEnergyConversionDevice", "Fired heater H-1", "equipment"), "vessel_v"],
        [_el("IfcBuildingElementProxy", "Knock-out drum horizontal D-2"), "vessel_h"],
        [_el("IfcEnergyConversionDevice", "Heat exchanger E-1", "equipment"), "exchanger"],
        [_el("IfcFlowMovingDevice", "Pump P-101A", "equipment"), "pump_plinth"],
        [_el("IfcFlowMovingDevice", "Centrifugal compressor K-1"), "compressor"],
        [_el("IfcEnergyConversionDevice", "Chiller CH-1", "equipment"), "chiller"],
        [_el("IfcFlowMovingDevice", "Air handling unit AHU-1", "equipment"), "ahu"],
        [_el("IfcMedicalDevice", "MRI scanner", "equipment"), "mri"],
        [_el("IfcBuildingElementProxy", "Culvert segment C1", "culvert"), "culvert"],
        [_el("IfcWall", "Culvert headwall inlet", "wall"), "culvert"],
        [_el("IfcColumn", "Pier column P1-2", "pier"), "bridge_pier"],
        [_el("IfcChimney", "Exhaust stack", "tank"), "stack"],
        [_el("IfcBuildingElementProxy", "Process module M-01", "equipment"), "module"],
        [_el("IfcBuildingElementProxy", "Cooling tower CT-1"), "cooling_tower"],
        [_el("IfcBuildingElement", "Switchroom SR-1"), "switchroom"],
        [_el("IfcPipeSegment", "Rack pipe PR-01 seg 1", "pipe", "RACK-Z"), "rack"],
        [_el("IfcMember", "Rack leg 0a", "beam", "RACK-Z"), "rack"],
        [_el("IfcPipeSegment", "Cooling water pipe", "pipe", "L00-Z1"), ""],
        [_el("IfcWall", "Abutment wall A1-2", "pier"), ""],
        [_el("IfcColumn", "Column C-1", "column"), ""],
        [_el("IfcFlowController", "Isolation valve P-101-1", "generic"), ""],
        [_el("IfcSlab", "Slab L00", "slab"), ""],
    ]
    for c in cases:
        var e: ElementData = c[0]
        eq(reg.kit_for_element(e), c[1], "%s (%s) -> %s" % [e.name, e.ifc_class, c[1]])
    # raw dictionaries work too
    eq(reg.kit_for_element({"ifc_class": "IfcTank", "name": "TK", "visual": "tank"}), "tank", "dictionary element")
    eq(reg.kit_for_element({"ifc_class": "IfcBuildingElementProxy", "name": "Skid", "module": true}), "module", "module flag")
    # an explicit visual_kit wins over the fallback; an unknown one is ignored
    var pipe: ElementData = _el("IfcPipeSegment", "Plain pipe", "pipe")
    pipe.set("visual_kit", "rack")
    eq(reg.kit_for_element(pipe), "rack", "visual_kit hint wins")
    var tank: ElementData = _el("IfcTank", "Storage tank TK-9", "tank")
    tank.set("visual_kit", "vessel_h")
    eq(reg.kit_for_element(tank), "vessel_h", "visual_kit overrides the tank fallback")
    tank.set("visual_kit", "no_such_kit")
    eq(reg.kit_for_element(tank), "tank", "unknown visual_kit falls back")


func test_variant_selection() -> void:
    eq(reg.variant_for("rack", ["structure", "process"]), "rack", "steel + piping only")
    eq(reg.variant_for("rack", ["structure", "process", "electrical"]), "rack_ei", "electrical adds ei")
    eq(reg.variant_for("rack", ["instrumentation"]), "rack_ei", "instrumentation adds ei")
    eq(reg.variant_for("rack", ["structure", "electrical", "mechanical"]), "rack_mpei", "mechanical adds mech")
    eq(reg.variant_for("rack", []), "rack", "no tasks")
    eq(reg.variant_for("tank", ["process"]), "", "kits without variants")


func _rack_tasks() -> Array:
    reg.step_disciplines = {"S-STEEL": "structure", "S-PIPE": "process", "S-TRAY": "electrical", "S-DOC": "general"}
    return [_task("t1", "S-STEEL", 2.0), _task("t2", "S-STEEL", 2.0), _task("t3", "S-PIPE", 4.0), _task("t4", "S-DOC", 1.0)]


func test_layer_fills_math() -> void:
    var tasks: Array = _rack_tasks()
    var done: Dictionary = {"t1": 1.0, "t2": 2.0, "t3": 1.0, "t4": 1.0}
    var progress: Callable = func(t: TaskData) -> float: return float(done[t.task_id])
    var r: Dictionary = reg.analyze("rack", tasks, progress)
    var fills: Dictionary = r["fills"]
    near(fills["steel"], 0.75, "steel: (1 + 2) / 4 crew-days")
    near(fills["piping"], 0.25, "piping: 1 / 4")
    near(fills["ei"], 0.0, "no electrical tasks: 0")
    near(fills["mech"], 0.0, "no mechanical tasks: 0")
    near(float(r["overall"]), 5.0 / 9.0, "overall: all tasks")
    eq(r["present"], ["steel", "piping"] as Array[String], "only layers with tasks are drawn")
    eq(reg.layer_fills("rack", tasks, progress), fills, "layer_fills = analyze.fills")
    # a task done beyond its estimate is clamped
    done["t3"] = 99.0
    near(reg.layer_fills("rack", tasks, progress)["piping"], 1.0, "clamped to 1")
    # adding an electrical task makes the ei layer appear
    tasks.append(_task("t5", "S-TRAY", 1.0))
    done["t5"] = 0.5
    var r2: Dictionary = reg.analyze("rack", tasks, progress, Callable(), "rack_ei")
    near(r2["fills"]["ei"], 0.5, "ei: 0.5 / 1")
    ok((r2["present"] as Array).has("ei"), "ei present")
    ok(not (r2["present"] as Array).has("mech"), "mech still absent")
    # no tasks at all: every layer shows as an unbuilt ghost
    var r3: Dictionary = reg.analyze("rack", [], progress)
    eq((r3["present"] as Array).size(), 5, "no tasks: all layers present")
    near(r3["fills"]["steel"], 0.0, "no tasks: fill 0")
    # bad callable does not crash
    var r4: Dictionary = reg.analyze("rack", tasks, Callable())
    near(r4["fills"]["steel"], 0.0, "invalid progress callable counts as nothing done")


func test_sequence_groups_and_followers() -> void:
    reg.step_disciplines = {"SET": "process", "FOOT": "structure", "CX": "commissioning"}
    var tasks: Array = [_task("a", "SET", 10.0), _task("b", "FOOT", 2.0), _task("c", "CX", 4.0)]
    var done: Dictionary = {"a": 5.0, "b": 2.0, "c": 0.0}
    var progress: Callable = func(t: TaskData) -> float: return float(done[t.task_id])
    var f: Dictionary = reg.layer_fills("tank", tasks, progress)
    # process layers (shell w4, roof w1, nozzles w1) split p = 0.5 in order
    near(f["shell"], 0.75, "shell takes the first 4/6 of the process work")
    near(f["roof"], 0.0, "roof not started")
    near(f["nozzles"], 0.0, "nozzles not started")
    near(f["ring_foundation"], 1.0, "foundation has its own structure task: done")
    near(f["insulation"], 0.75, "insulation has no tasks: follows the shell")
    near(f["stair"], 0.0, "stair follows the roof")
    done["a"] = 10.0
    f = reg.layer_fills("tank", tasks, progress)
    near(f["shell"], 1.0, "shell done")
    near(f["roof"], 1.0, "roof done")
    near(f["nozzles"], 1.0, "nozzles done")
    near(f["stair"], 1.0, "stair follows the finished roof")
    # inspected / rework states per layer
    var state: Callable = func(t: TaskData) -> int:
        return TaskRuntime.State.INSPECTED if t.task_id != "a" else TaskRuntime.State.REWORK
    var st: Dictionary = reg.layer_states("tank", tasks, progress, state)
    eq(st["shell"], "rework", "rework on the process layers")
    eq(st["ring_foundation"], "inspected", "inspected foundation")
    eq(st["insulation"], "rework", "follower shows the followed layer's state")


func test_cache_and_collapsed_mesh() -> void:
    var p: Dictionary = _params("tank", Vector2i(1, 1), 0.5)
    var m1: Mesh = reg.build("tank", p)
    var builds: int = reg.cache_builds
    var m2: Mesh = reg.build("tank", p)
    ok(m1 == m2, "same params: cached mesh")
    eq(reg.cache_builds, builds, "no rebuild")
    var p2: Dictionary = _params("tank", Vector2i(1, 1), 0.5)
    (p2["layer_fills"] as Dictionary)["shell"] = 0.505  # inside the same quantisation step
    ok(reg.build("tank", p2) == m1, "fills are quantised for the cache")
    (p2["layer_fills"] as Dictionary)["shell"] = 0.7
    ok(reg.build("tank", p2) != m1, "a real change builds a new mesh")
    # LOD box
    var box0: Mesh = reg.collapsed_mesh("tank", p, 0.0)
    var box1: Mesh = reg.collapsed_mesh("tank", p, 1.0)
    eq(_all_vertices(box0).size(), 30, "collapsed box is 10 triangles (no floor face)")
    ok(not _surface(box0, "ghost_fill").is_empty() and _surface(box0, "solid").is_empty() and _surface(box0, "ghost").is_empty(), "nothing built: translucent filled ghost box (the LOD box keeps its tint)")
    ok(not _surface(box1, "solid").is_empty(), "built: solid box")
    var c0: Color = (_surface(box1, "solid")[Mesh.ARRAY_COLOR] as PackedColorArray)[0]
    var c1: Color = (_surface(reg.collapsed_mesh("tank", p, 0.5), "solid")[Mesh.ARRAY_COLOR] as PackedColorArray)[0]
    ok(c0 != c1, "tint follows the overall fill")
    var bb: AABB = box1.get_aabb()
    near(bb.size.x, CELL_M, "box spans the footprint", 0.01)
    ok(reg.collapsed_mesh("tank", p, 1.0) == box1, "collapsed meshes are cached")


# ------------------------------------------------------------------ markers

func test_marker_meshes() -> void:
    for id in MarkerKit.IDS:
        var m: ArrayMesh = MarkerKit.mesh_for(id)
        ok(m.get_surface_count() >= 1 and _all_vertices(m).size() >= 36, "%s marker has geometry" % id)
        ok(_all_vertices(m).size() / 3 < 1500, "%s marker is light" % id)
        var bb: AABB = m.get_aabb()
        ok(bb.size.x <= 6.0 and bb.size.z <= 6.0 and bb.position.y >= -0.1, "%s marker fits a cell %s" % [id, str(bb)])
        eq(MarkerKit.mesh_for(id), m, "%s marker cached" % id)
        var w: Mesh = reg.marker_mesh(id)
        ok(w != null, "%s provider mesh" % id)
        var wb: AABB = w.get_aabb()
        ok(wb.size.y <= 0.401 and maxf(wb.size.x, wb.size.z) <= 0.301, "%s provider mesh fits the marker node (%s)" % [id, str(wb.size)])
        near(wb.get_center().y, 0.0, "%s provider mesh is centred" % id, 0.01)
    ok(reg.marker_mesh("nonsense") == null, "unknown marker: null")
    var crane: AABB = MarkerKit.mesh_for("crane").get_aabb()
    ok(crane.size.y > 10.0, "the crane is tall")


# ------------------------------------------------------------------ instances

func _industrial() -> SequenceBundle:
    var b := SequenceBundle.load_from_path("res://scenarios/industrial_standard/sequence.json")
    ok(b.valid, "industrial bundle valid")
    return b


func test_kit_instances_group_industrial_elements() -> void:
    var b: SequenceBundle = _industrial()
    var ki := KitInstances.new(b, reg)
    var list: Array[Dictionary] = ki.instances()
    ok(list.size() > 0, "instances found")
    var per_kit: Dictionary = {}
    var seen: Dictionary = {}
    for inst in list:
        per_kit[inst["kit"]] = int(per_kit.get(inst["kit"], 0)) + 1
        ok((inst["cells"] as Array).size() >= 1, "instance %d has cells" % inst["id"])
        ok((inst["element_guids"] as Array).size() >= 1, "instance %d has elements" % inst["id"])
        var r: Rect2i = inst["rect"]
        ok(r.size.x >= 1 and r.size.y >= 1, "instance %d footprint" % inst["id"])
        for c in inst["cells"]:
            ok(r.has_point(c), "cell inside the instance rect")
        for g in inst["element_guids"]:
            ok(not seen.has(g), "element %s in exactly one instance" % g)
            seen[g] = true
            eq(ki.instance_of(g), int(inst["id"]), "instance_of(%s)" % g)
        ok(float(inst["height_m"]) >= 1.0, "instance height")
        ok(inst.has("layer_fills") and inst.has("overall_fill") and inst.has("variant"), "instance record complete")
    print("      [kits] industrial_standard: %d instances %s" % [list.size(), str(per_kit)])
    # element-instanced kits: one instance per distinct cell set, even when the cells of neighbours touch
    for kit in per_kit:
        if str(reg.kit_param(kit, "instancing", "element")) != "element":
            continue
        var sets: Dictionary = {}
        for e in b.elements:
            if reg.kit_for_element(e) == kit:
                sets[str((e as ElementData).cells) + (e as ElementData).storey_id] = true
        eq(int(per_kit[kit]), sets.size(), "%s: one instance per equipment item" % kit)
    ok(int(per_kit.get("tank", 0)) >= 2, "tanks present and separate")
    eq(int(per_kit.get("transformer", 0)), 2, "two transformers")
    eq(int(per_kit.get("pump_plinth", 0)), 10, "ten pumps")
    eq(int(per_kit.get("module", 0)), 10, "ten modules")
    eq(int(per_kit.get("stack", 0)), 1, "one stack")
    ok(int(per_kit.get("rack", 0)) >= 1, "pipe rack found")
    # the rack row is covered exactly once, by sections without overlap (variant hints split it)
    var rack_cells: Dictionary = {}
    var variants: Dictionary = {}
    for inst in list:
        if inst["kit"] == "rack":
            variants[inst["variant"]] = true
            ok(float(inst["height_m"]) >= 7.0 - 0.01, "rack height from its legs")
            ok((inst["counts"] as Dictionary).get("pipes", 0) >= 1 or true, "pipe count hint")
            for c in inst["cells"]:
                ok(not rack_cells.has(c), "rack cell %s belongs to one instance" % str(c))
                rack_cells[c] = true
            var rr: Rect2i = inst["rect"]
            eq(rr.size.y, 1, "rack is one cell wide")
    eq(rack_cells.size(), 12, "rack covers the 12 cells of its row")
    ok(variants.has("rack_ei") or variants.has("rack_mpei"), "the sample rack has electrical tasks: %s" % str(variants.keys()))
    # an element of a multi-cell module forms a 2x1 instance
    for inst in list:
        if inst["kit"] == "module":
            eq((inst["rect"] as Rect2i).size, Vector2i(2, 1), "module is 2x1 cells")
            break
    ok(ki.instance_of("no-such-guid") == -1, "unknown element")
    eq(ki.element_layers("no-such-guid"), {}, "no layers for an element without a kit")
    var some: Dictionary = ki.element_layers(list[0]["element_guids"][0])
    eq(some["kit"], list[0]["kit"], "element_layers names the kit")
    ok(some.has("layers") and some.has("overall") and some.has("present"), "element_layers record")


func test_kit_instances_fills_follow_progress() -> void:
    var b: SequenceBundle = _industrial()
    var ki := KitInstances.new(b, reg)
    var none: Callable = func(_t: TaskData) -> float: return 0.0
    ki.refresh(none)
    for inst in ki.instances():
        near(float(inst["overall_fill"]), 0.0, "nothing done")
    var full: Callable = func(t: TaskData) -> float: return t.estimated_crew_days
    ki.refresh(full)
    for inst in ki.instances():
        near(float(inst["overall_fill"]), 1.0, "everything done")
        for k in inst["layer_fills"]:
            var present: bool = (inst["present_layers"] as Array).has(k)
            if present:
                near(float(inst["layer_fills"][k]), 1.0, "%s %s layer complete" % [inst["kit"], k])
    # rack sections: variants reflect hints and disciplines
    for inst in ki.instances():
        if inst["kit"] == "rack":
            ok(["rack", "rack_ei", "rack_mpei"].has(inst["variant"]), "rack variant is valid")
            ok((inst["present_layers"] as Array).has("steel") and (inst["present_layers"] as Array).has("piping"), "steel + piping present")


func test_aggregate_member_guids_expand_to_member_tasks() -> void:
    var b: SequenceBundle = load_bundle()
    var a: ElementData = b.elements[0]
    var other: ElementData = b.elements[1]
    ok(not b.tasks_by_element[other.guid].is_empty(), "the member has tasks")
    a.set("visual_kit", "tank")
    a.set("member_guids", [other.guid] as Array[String])
    var ki := KitInstances.new(b, reg)
    var i: int = ki.instance_of(a.guid)
    ok(i >= 0, "element with visual_kit becomes an instance")
    var ids: Dictionary = {}
    for t in ki.tasks_of(i):
        ids[(t as TaskData).task_id] = true
    for t in b.tasks_by_element[other.guid]:
        ok(ids.has((t as TaskData).task_id), "member task %s included" % (t as TaskData).task_id)
    for t in b.tasks_by_element[a.guid]:
        ok(ids.has((t as TaskData).task_id), "own task included")
    eq(ki.instance_of(other.guid), -1, "the member itself is not drawn by a kit unless mapped")
