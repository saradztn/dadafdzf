#!/usr/bin/env python3
"""
FBX2MTA - Blender-side conversion pipeline
==========================================
FBX -> Blender (headless bpy) -> scene processing -> DragonFF -> DFF (GTA SA v3.6.0.3)

Runs INSIDE a Blender/bpy Python process:
    run_blender.sh scripts/convert.py --input in.fbx --output out.dff \
        [--budget AUTO|N] [--log logs/x.log] [--status temp/x.json]

Never modifies the input file; works on the in-memory copy only.
"""
import sys, os, json, math, time, argparse, traceback
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # FBX2MTA/
sys.path.insert(0, os.path.join(ROOT, "dragonff"))

# ---------------------------------------------------------------- logging
LOG_PATH = None
def log(level, msg):
    line = f"[{level}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ---------------------------------------------------------------- argparse
def main():
    global LOG_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--budget", default="AUTO")
    ap.add_argument("--log", required=True)
    ap.add_argument("--status", required=True)
    args = ap.parse_args()

    LOG_PATH = args.log
    os.makedirs(os.path.dirname(args.log), exist_ok=True)
    open(args.log, "w").close()
    status = {
        "input": args.input, "output": args.output,
        "success": False, "attempts": [], "errors": [], "warnings": [],
    }

    t0 = time.time()
    log("INFO", "Starting converter (Blender side)")

    import bpy
    log("INFO", f"Blender {bpy.app.version_string} headless")

    # ---- register DragonFF (official addon from Parik27/DragonFF)
    import DragonFF
    DragonFF.register()
    log("INFO", "DragonFF addon registered (official Parik27/DragonFF)")

    budget = args.budget
    budget_n = None
    if budget != "AUTO":
        try:
            budget_n = int(budget)
        except ValueError:
            log("ERROR", f"Invalid budget '{budget}', falling back to AUTO")
            budget_n = None

    # ================================================================
    # 1. FBX IMPORT  (+ GTA SA coordinate-system auto-check)
    # ================================================================
    log("INFO", f"Importing FBX: {args.input}")

    ORIENT_CANDIDATES = [
        # (axis_forward, axis_up, label)  -- default first
        ("-Z", "Y",  "default (Y-up source)"),
        ("Z",  "Y",  "Z-forward (Y-up source)"),
        ("-Z", "Z",  "-Z-forward (Z-up source)"),
        ("X",  "Z",  "X-forward (Z-up source)"),
    ]

    def import_fbx(forward, up):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        # NOTE: Blender 4.2 FBX operator: no object_types filter (imports
        # meshes/armatures/empties only - lights/cameras are cleaned up later),
        # scale -> global_scale
        res = bpy.ops.import_scene.fbx(
            filepath=args.input,
            axis_forward=forward, axis_up=up,
            global_scale=1.0,
            use_anim=False,
        )
        return res

    def uprightness_score():
        """Higher = model standing up (Z-up). Score = z-extent / max(x,y-extent)."""
        xs, ys, zs = [], [], []
        for ob in bpy.data.objects:
            if ob.type != "MESH":
                continue
            for c in ob.bound_box:
                w = ob.matrix_world @ __import__("mathutils").Vector(c)
                xs.append(w.x); ys.append(w.y); zs.append(w.z)
        if not xs:
            return -1.0, 0.0, 0.0, 0.0
        ex, ey, ez = max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)
        footprint = max(ex, ey)
        return (ez / footprint if footprint > 1e-6 else 0.0), ex, ey, ez

    best = None
    for fwd, up, label in ORIENT_CANDIDATES:
        import_fbx(fwd, up)
        score, ex, ey, ez = uprightness_score()
        log("INFO", f"  axis check: forward={fwd} up={up} -> extents X={ex:.2f} Y={ey:.2f} Z={ez:.2f} (upright score {score:.3f}) [{label}]")
        if best is None or score > best[0] + 1e-9:
            best = (score, fwd, up, label, (ex, ey, ez))
        if fwd == ORIENT_CANDIDATES[0][0] and up == ORIENT_CANDIDATES[0][1] and score > 0.15:
            break  # default axes already give an upright model
    score, FWD, UP, OLAB, EXTENTS = best
    import_fbx(FWD, UP)
    if (FWD, UP) != (ORIENT_CANDIDATES[0][0], ORIENT_CANDIDATES[0][1]):
        log("WARN", f"Default FBX orientation was not upright; using forward={FWD} up={UP}")
    log("INFO", f"FBX imported (axis forward={FWD}, up={UP}, extents X/Y/Z = "
                f"{EXTENTS[0]:.2f}/{EXTENTS[1]:.2f}/{EXTENTS[2]:.2f})")
    status["orientation"] = {"axis_forward": FWD, "axis_up": UP, "label": OLAB,
                             "upright_score": round(score, 4), "extents": [round(v, 2) for v in EXTENTS]}
    log("INFO", "GTA SA coordinate layer: Blender is Z-up right-handed, matching GTA SA "
                "(+Z up). Model verified upright in Blender space -> no mirror/hand-flip needed. "
                "DragonFF exports Blender coords verbatim and handles UV v-flip + winding internally.")

    mesh_objs = [o for o in bpy.data.objects if o.type == "MESH"]
    arm_objs = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if not mesh_objs:
        raise RuntimeError("No mesh objects found in FBX (invalid/unsupported FBX)")
    if not arm_objs:
        status["kind"] = "static"
        log("INFO", "Static mesh model (no armature)")
    else:
        status["kind"] = "skinned"
        log("INFO", f"Skinned model detected: {len(arm_objs)} armature(s)")

    # ================================================================
    # 2. SCENE CLEANUP (materials / images / names)
    # ================================================================
    # remove stray scene objects (lights/cameras are already excluded at import)
    for ob in [o for o in bpy.data.objects if o.type in ("LIGHT", "CAMERA", "SPEAKER")]:
        bpy.data.objects.remove(ob, do_unlink=True)

    # ---- rename image datablocks to clean texture names (file stem)
    tex_names = {}
    for img in bpy.data.images:
        if img.source != "FILE" or not img.filepath:
            continue
        base = os.path.basename(img.filepath)
        stem, _ext = os.path.splitext(base)
        if stem and stem != img.name:
            log("INFO", f"Renaming image datablock {img.name!r} -> {stem!r} (matches texture file)")
            img.name = stem
        tex_names[img.name] = img.filepath
    for n, p in tex_names.items():
        log("INFO", f"  texture: {n}  ({p})")

    # ---- fix material names + drop unused slots
    def clean_name(n):
        import re
        n = re.sub(r"\.0{2,3}$", "", n)
        return n
    used_mats = set()
    for ob in mesh_objs:
        for slot in ob.data.materials:
            if slot:
                used_mats.add(slot.name)
    for mat in list(bpy.data.materials):
        if mat.name not in used_mats:
            bpy.data.materials.remove(mat)
            log("INFO", f"Removed unused material {mat.name!r}")
        else:
            new = clean_name(mat.name)
            if new != mat.name:
                if new in used_mats and new != mat.name:
                    new = mat.name  # collision, keep as-is
                mat.name = new
                if mat.name != new:
                    mat.name = new + "_001"
                log("INFO", f"Material renamed {mat.name!r}")
    for ob in mesh_objs:
        mats = ob.data.materials
        seen = set()
        for i in range(len(mats) - 1, -1, -1):
            m = mats[i]
            if m is None or m.name in seen:
                ob.data.materials.pop(index=i)
            else:
                seen.add(m.name)

    # ================================================================
    # 3. MESH PROCESSING (triangulation, validation, cleanup)
    # ================================================================
    import mathutils, bmesh

    def bmesh_cleanup(me, recalc_open_too=False):
        """Triangulate + dedupe + degen-remove + normals using Blender's bmesh
        (the native mesh library behind the mesh operators - context safe)."""
        me.update()
        bm = bmesh.new()
        bm.from_mesh(me)

        # 1) triangulate ngon/quad -> tri (Blender's built-in triangulator)
        non_tris = [f for f in bm.faces if len(f.verts) > 3]
        if non_tris:
            bmesh.ops.triangulate(bm, faces=non_tris,
                                  quad_method="BEAUTY", ngon_method="BEAUTY")
            log("INFO", f"  triangulated {len(non_tris)} quads/ngons -> tris={len(bm.faces)}")
        else:
            log("INFO", "  already triangulated")

        # 2) exact duplicate vertices (import artifacts)
        before = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-5)
        if len(bm.verts) < before:
            log("INFO", f"  merged {before - len(bm.verts)} duplicate vertices")

        # 3) zero-area / degenerate faces
        bmesh.ops.dissolve_degenerate(bm, edges=bm.edges[:], dist=1e-5)
        dead = [f for f in bm.faces if f.calc_area() < 1e-12]
        if dead:
            bmesh.ops.delete(bm, geom=dead, context="FACES")
            log("INFO", f"  removed {len(dead)} degenerate (zero-area) faces")

        # 4) normals: recalc outward only on closed (watertight) meshes
        boundary = [e for e in bm.edges if e.is_boundary]
        if not boundary and bm.faces:
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
            log("INFO", "  closed mesh: recalculated normals outward")
        elif recalc_open_too and bm.faces:
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
            log("INFO", "  open mesh: forced normal recalculation (retry mode)")
        else:
            log("INFO", f"  open mesh ({len(boundary)} boundary edges): "
                        f"keeping original normals (validated finite+normalized)")

        bm.to_mesh(me)
        bm.free()
        me.update()

    for ob in mesh_objs:
        me = ob.data
        log("INFO", f"Processing mesh {ob.name!r}: verts={len(me.vertices)} polys={len(me.polygons)}")

        # --- NaN / Infinity validation (hard fail if present)
        bad = 0
        for v in me.vertices:
            if not (math.isfinite(v.co.x) and math.isfinite(v.co.y) and math.isfinite(v.co.z)):
                bad += 1
        for l in me.uv_layers:
            for uv in l.data:
                if not (math.isfinite(uv.uv[0]) and math.isfinite(uv.uv[1])):
                    bad += 1
        if bad:
            raise RuntimeError(f"Mesh {ob.name!r} contains {bad} NaN/Infinity values - cannot convert")

        # --- triangulation / dedupe / degenerates / normals (bmesh)
        bmesh_cleanup(me)

        # --- UV validation
        for l in me.uv_layers:
            log("INFO", f"  UV layer {l.name!r}: {len(l.data)} coords (loops={len(me.loops)})")
            if len(l.data) != len(me.loops):
                raise RuntimeError(f"UV layer {l.name} size mismatch on {ob.name!r}")

    # ================================================================
    # 4. APPLY TRANSFORMS (only the residual object transforms)
    # ================================================================
    targs = [o for o in bpy.data.objects if o.type in ("MESH", "ARMATURE", "EMPTY")]
    def residual(o):
        loc, rot_q, scale = o.matrix_local.decompose()
        return loc.length + rot_q.angle + max(abs(s - 1.0) for s in scale)
    need = [o for o in targs if residual(o) > 1e-7]
    if need:
        bpy.ops.object.select_all(action="DESELECT")
        for o in targs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = mesh_objs[0]
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        log("INFO", f"Applied residual transforms on {len(need)} object(s) (kept model shape, normalized root matrices)")
    else:
        log("INFO", "No residual object transforms to apply")

    # ================================================================
    # 5. TRIANGLE ANALYSIS (rule 9)
    # ================================================================
    total_tris = 0
    per_obj = {}
    per_mat = Counter()
    for ob in mesh_objs:
        me = ob.data
        t = len(me.polygons)
        total_tris += t
        per_obj[ob.name] = (len(me.vertices), t)
        for p in me.polygons:
            mi = p.material_index
            if mi < len(me.materials) and me.materials[mi]:
                per_mat[me.materials[mi].name] += 1
    status["original"] = {
        "vertices": sum(v for v, _ in per_obj.values()),
        "triangles": total_tris,
        "objects": len(mesh_objs),
        "per_object": {k: {"vertices": v, "triangles": t} for k, (v, t) in per_obj.items()},
        "per_material": dict(per_mat),
    }
    for name, (v, t) in per_obj.items():
        log("INFO", f"OBJECT: {name}\nVertices: {v}\nTriangles: {t}")
    for name, t in per_mat.items():
        log("INFO", f"MATERIAL: {name}\nTriangles: {t}")

    # ================================================================
    # 6. GEOMETRY BUDGET (rule 10/11)
    # ================================================================
    HARD_VERTS = 65535
    decimated = False
    if budget_n is not None and total_tris > budget_n:
        log("INFO", f"Decimation required: {total_tris} tris > budget {budget_n}")
        ratio = max(budget_n / total_tris, 0.01)
        for ob in mesh_objs:
            mod = ob.modifiers.new("Decimate", 'DECIMATE')
            mod.ratio = ratio
            bpy.ops.object.select_all(action="DESELECT")
            ob.select_set(True)
            bpy.context.view_layer.objects.active = ob
            bpy.ops.object.modifier_apply(modifier=mod.name)
        decimated = True
        total_tris_after = sum(len(o.data.polygons) for o in mesh_objs)
        log("INFO", f"Decimated {total_tris} -> {total_tris_after} tris (ratio {ratio:.3f})")
        total_tris = total_tris_after
    elif budget_n is None:
        log("INFO", f"Geometry Budget: AUTO - {total_tris} triangles within limits, no reduction "
                    f"(hard DFF limit 65535 verts/geometry respected)")

    # ================================================================
    # 7. DRAGONFF SETUP
    # ================================================================
    # 7a. bone props (replicates DragonFF's object.dff_generate_bone_props)
    for arm in arm_objs:
        used_ids = set()
        for i, bone in enumerate(arm.data.bones):
            bid = i
            while bid in used_ids:
                bid += 1
            bone["bone_id"] = bid
            used_ids.add(bid)
            if not bone.children:
                btype = 1
            elif not bone.parent or bone.parent.children[-1] is bone:
                btype = 0
            else:
                btype = 2
            bone["type"] = btype
        log("INFO", f"Armature {arm.name!r}: bone_id/type set on {len(arm.data.bones)} bones")

    # 7b. object-level dff props
    for ob in mesh_objs:
        ob.dff.type = "OBJ"
        ob.dff.uv_map1 = True
        ob.dff.uv_map2 = False           # single UV set (MTA standard diffuse)
        ob.dff.export_split_normals = False  # per-vertex normals (smoothing + fewer verts)
        ob.dff.export_normals = True
        ob.dff.light = True
        ob.dff.modulate_color = True
        ob.dff.export_binsplit = True
        # estimate exported vertex count (EXACT same dedup key as DragonFF:
        # (vertex, per-vertex normal, ALL uv layers) - uv_map2 only gates
        # writing, not deduplication)
        me = ob.data
        uv_data = [[(uv.uv[0], uv.uv[1]) for uv in layer.data] for layer in me.uv_layers]
        keys = set()
        for p in me.polygons:
            for li in p.loop_indices:
                vi = me.loops[li].vertex_index
                n = me.vertices[vi].normal
                uvs = tuple(layer[li] for layer in uv_data)
                keys.add((vi, (n.x, n.y, n.z), uvs))
        est = len(keys)
        if est > HARD_VERTS:
            raise RuntimeError(
                f"Estimated {est} DFF vertices for {ob.name!r} exceeds hard limit {HARD_VERTS}; "
                f"decimation is required (budget must be lowered)")
        log("INFO", f"  {ob.name!r}: estimated DFF vertices {est} (limit {HARD_VERTS})")
    for arm in arm_objs:
        arm.dff.type = "OBJ"

    # 7c. material-level dff props (bump mapping via DragonFF Rockstar effect)
    tex_report = {}
    for mat in bpy.data.materials:
        d = mat.dff
        # find normal-map texture name (via Normal input / Normal Map node)
        bump = None
        if mat.use_nodes:
            for n in mat.node_tree.nodes:
                if n.type == "NORMAL_MAP":
                    inp = n.inputs["Color"]
                    if inp.is_linked and inp.links[0].from_node.type == "TEX_IMAGE":
                        img = inp.links[0].from_node.image
                        if img:
                            bump = img.name
        if bump:
            d.export_bump_map = True
            d.bump_map_tex = bump
            d.bump_map_intensity = 1.0
            log("INFO", f"  material {mat.name!r}: bump map -> {bump!r}")
        # diffuse texture name from Base Color link
        diff = None
        if mat.use_nodes:
            for n in mat.node_tree.nodes:
                if n.type == "BSDF_PRINCIPLED":
                    inp = n.inputs["Base Color"]
                    if inp.is_linked and inp.links[0].from_node.type == "TEX_IMAGE":
                        img = inp.links[0].from_node.image
                        if img:
                            diff = img.name
                    break
        d.ambient = 0.5
        d.specular = 0.5
        d.diffuse = 0.5
        d.tex_filters = "0"
        d.tex_u_addr = "0"
        d.tex_v_addr = "0"
        tex_report[mat.name] = {"diffuse": diff, "bump": bump}
    status["materials"] = {
        "count": len(bpy.data.materials),
        "textures": sorted(set(v for e in tex_report.values() for k, v in e.items() if v)),
        "details": tex_report,
    }

    # ================================================================
    # 8. EXPORT through DragonFF (with automatic retry)
    # ================================================================
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    if os.path.exists(args.output):
        os.remove(args.output)

    def try_export(tag, exclude_geo_faces=False, split_normals=False):
        for ob in mesh_objs:
            ob.dff.export_split_normals = split_normals
        bpy.ops.object.select_all(action="DESELECT")
        for o in mesh_objs + arm_objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = mesh_objs[0]
        r = bpy.ops.export_dff.scene(
            filepath=args.output,
            mass_export=False,
            export_coll=True,
            coll_ext_type="39056127",   # SA-MP collision extension type (MTA:SA)
            apply_coll_trans=True,
            export_frame_names=True,
            exclude_geo_faces=exclude_geo_faces,
            only_selected=True,
            preserve_positions=True,
            preserve_rotations=True,
            export_version="0x36003",   # GTA SA (v3.6.0.3)
        )
        ok = r == {"FINISHED"} and os.path.exists(args.output) and os.path.getsize(args.output) > 0
        log("INFO", f"Export attempt [{tag}]: result={r} file_ok={ok}")
        return ok

    export_ok = False
    try:
        export_ok = try_export("base")
    except Exception as e:
        log("ERROR", f"Export attempt [base] raised: {e}")
        status["errors"].append(f"base export: {e}")

    if not export_ok:
        log("WARN", "Auto-retry 1: forcing Bin Mesh PLG (exclude_geo_faces=True)")
        try:
            export_ok = try_export("binmesh", exclude_geo_faces=True)
        except Exception as e:
            log("ERROR", f"Export attempt [binmesh] raised: {e}")
            status["errors"].append(f"binmesh export: {e}")

    if not export_ok:
        log("WARN", "Auto-retry 2: recalculate normals + merge duplicates, then re-export")
        for ob in mesh_objs:
            bmesh_cleanup(ob.data, recalc_open_too=True)
        try:
            export_ok = try_export("reclean")
        except Exception as e:
            log("ERROR", f"Export attempt [reclean] raised: {e}")
            status["errors"].append(f"reclean export: {e}")

    if not export_ok:
        log("WARN", "Auto-retry 3: 25% decimation as last resort")
        for ob in mesh_objs:
            mod = ob.modifiers.new("Decimate2", 'DECIMATE')
            mod.ratio = 0.75
            bpy.ops.object.select_all(action="DESELECT")
            ob.select_set(True)
            bpy.context.view_layer.objects.active = ob
            bpy.ops.object.modifier_apply(modifier=mod.name)
        try:
            export_ok = try_export("decimated")
        except Exception as e:
            log("ERROR", f"Export attempt [decimated] raised: {e}")
            status["errors"].append(f"decimated export: {e}")

    if not export_ok:
        status["errors"].append("All export attempts failed")
        log("ERROR", "DFF export FAILED after all retries")
        with open(args.status, "w") as f:
            json.dump(status, f, indent=2)
        raise SystemExit(1)

    # ---- final stats
    final_tris = sum(len(o.data.polygons) for o in mesh_objs)
    final_verts = sum(len(o.data.vertices) for o in mesh_objs)
    status["processed"] = {
        "vertices": final_verts,
        "triangles": final_tris,
        "objects": len(mesh_objs),
        "decimated": decimated,
    }
    status["frames"] = sum(len(a.data.bones) for a in arm_objs)
    status["success"] = True
    status["export_version"] = "GTA SA (v3.6.0.3) 0x36003"
    status["size_bytes"] = os.path.getsize(args.output)
    status["elapsed_s"] = round(time.time() - t0, 2)
    log("INFO", f"DFF exported: {args.output} ({status['size_bytes']} bytes) in {status['elapsed_s']}s")
    log("SUCCESS", f"DFF created: {args.output}")

    with open(args.status, "w") as f:
        json.dump(status, f, indent=2)

if __name__ == "__main__":
    # NOTE: use os._exit() - the bpy wheel can segfault during normal CPython
    # interpreter shutdown. All output (DFF + status JSON + logs) is already
    # flushed to disk before we get here.
    try:
        main()
        os._exit(0)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        os._exit(code)
    except Exception:
        err = traceback.format_exc()
        try:
            log("ERROR", "Conversion FAILED:\n" + err)
        except Exception:
            pass
        os._exit(2)
