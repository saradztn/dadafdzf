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
import sys, os, json, math, time, argparse, traceback, shutil
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
    # ---- collision (COL) options (new stage, isolated from the DFF)
    ap.add_argument("--col", action="store_true",
                    help="generate a real COL collision mesh via DragonFF COL export")
    ap.add_argument("--col-quality", default="AUTO",
                    choices=["AUTO", "LOW", "MEDIUM", "HIGH", "CUSTOM"])
    ap.add_argument("--col-triangles", type=int, default=0,
                    help="maximum collision triangles (used with CUSTOM / as cap)")
    ap.add_argument("--col-output", default="",
                    help="COL output path (default: DFF path with .col extension)")
    args = ap.parse_args()

    col_out = args.col_output or os.path.splitext(args.output)[0] + ".col"
    txd_out = os.path.splitext(args.output)[0] + ".txd"

    LOG_PATH = args.log
    for _p in (args.log, args.status, args.output, txd_out, col_out):
        _d = os.path.dirname(os.path.abspath(_p))
        if _d:
            os.makedirs(_d, exist_ok=True)
    open(args.log, "w").close()
    status = {
        "input": args.input, "output": args.output,
        "success": False, "attempts": [], "errors": [], "warnings": [],
        "col": {"enabled": bool(args.col), "output": col_out,
                "success": False, "reason": None,
                "quality": args.col_quality, "original_tris": 0,
                "budget_tris": 0, "final_tris": 0, "verts": 0},
        "txd": {"output": txd_out, "success": False, "reason": None,
                "textures": 0, "names": [], "size_bytes": 0},
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
            use_anim=True,  # animations are imported so the rest pose can
                            # be parked on the first keyframe (stage 4.5)
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

    # the FBX importer extracts embedded textures to a sidecar folder
    # <input>.fbm next to the file - remember if we are the ones creating
    # it so it can be cleaned up at the end (user-provided sidecars are
    # never touched)
    fbm_dir = os.path.splitext(args.input)[0] + ".fbm"
    fbm_existed = os.path.isdir(fbm_dir)

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
                             "upright_score": round(score, 4), "extents": [round(v, 2) for v in EXTENTS],
                             "model_space": "GTA SA Y-up (Blender Z-up rotated -90deg about X)"}
    log("INFO", "Orientation plan: import in Blender Z-up (axis auto-detected above), then "
                "stage 4.5 converts the model to GTA SA Y-up model space (-90 deg about X, "
                "no mirroring). Both DFF and COL are exported from the same Y-up mesh.")

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
    # 4.5 CONVERT BLENDER Z-UP -> GTA SA Y-UP MODEL SPACE
    #      GTA:SA DFF model space is Y-up (clump root frames are
    #      identity, so the geometry itself must be Y-up). Blender
    #      works in Z-up, so rotate the whole model -90 deg about X:
    #      (x, y, z) -> (x, z, -y). Right-handed (no mirroring), so
    #      winding/normals stay valid. At this point every object has
    #      an identity matrix, so rotating the DATA (mesh vertices +
    #      armature bones + empties) keeps skinning consistent.
    # ================================================================
    import mathutils
    YUP = mathutils.Matrix.Rotation(math.radians(-90), 4, "X")
    for ob in mesh_objs:
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        bm.transform(YUP)
        bm.to_mesh(ob.data)
        bm.free()
        ob.data.update()
    for arm in arm_objs:
        bpy.ops.object.select_all(action="DESELECT")
        arm.select_set(True)
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode="EDIT")
        for b in arm.data.edit_bones:
            b.head = YUP @ b.head
            b.tail = YUP @ b.tail
        bpy.ops.object.mode_set(mode="OBJECT")
    for o in bpy.data.objects:
        if o.type == "EMPTY":
            o.matrix_world = YUP @ o.matrix_world
    log("INFO", "Converted model to GTA SA Y-up model space (-90 deg about X, no mirroring)")

    # ---- ensure Armature modifiers (DFF skinning requirement) ----------
    #      FBX imports do not always create the Armature modifier, but
    #      DragonFF's exporter only writes skin data (SkinPLG) for objects
    #      that have one. Bind each skinned mesh to the armature whose
    #      bones match its vertex groups (or its parent), and park the
    #      scene on the first animation keyframe so the exported DFF/COL
    #      rest pose equals keyframe 1 of the FBX animation.
    if arm_objs:
        bone_names_by_arm = {a.name: {b.name for b in a.data.bones}
                             for a in arm_objs}
        fixed = 0
        for ob in mesh_objs:
            if any(m.type == "ARMATURE" for m in ob.modifiers):
                continue
            vg_names = {g.name for g in ob.vertex_groups}
            arm = None
            best_hits = 0
            for a in arm_objs:
                hits = len(vg_names & bone_names_by_arm[a.name])
                if hits > best_hits:
                    best_hits, arm = hits, a
            if arm is None and ob.parent is not None \
                    and ob.parent.type == "ARMATURE":
                arm = ob.parent
            if arm is not None:
                mod = ob.modifiers.new("Armature", "ARMATURE")
                mod.object = arm
                fixed += 1
                log("INFO", f"Added missing Armature modifier to {ob.name!r} "
                            f"(armature {arm.name!r}) - required for DFF skinning")
        if not fixed:
            log("INFO", "All skinned meshes already have Armature modifiers")
        anim_start = None
        for a in arm_objs:
            ad = a.animation_data
            if ad and ad.action:
                f0 = int(ad.action.frame_range[0])
                anim_start = f0 if anim_start is None else min(anim_start, f0)
        for ob in mesh_objs:
            ad = ob.animation_data
            if ad and ad.action:
                f0 = int(ad.action.frame_range[0])
                anim_start = f0 if anim_start is None else min(anim_start, f0)
        if anim_start is not None:
            bpy.context.scene.frame_set(anim_start)
            log("INFO", f"Scene parked on animation start frame {anim_start} "
                        f"(DFF/COL rest pose = first keyframe)")
        # DragonFF's exporter processes each armature IMMEDIATELY during
        # the object pass, while empties are only processed when their own
        # turn comes (FBX import collection order is arbitrary). If the
        # armature is still parented to an empty - typical of RDR/STK
        # style rigs ("Sam", "..._CTRL" roots) - and its turn comes first,
        # the exporter raises "Failed to set parent for <arm> to <empty>".
        # All object matrices are identity at this point (stage 4), so
        # unparenting changes nothing geometrically: the armature frame
        # simply becomes the clump root frame (its true role in a GTA rig).
        for arm in arm_objs:
            if arm.parent is not None:
                log("INFO", f"Unparented armature {arm.name!r} from "
                            f"{arm.parent.name!r} (DragonFF export-order "
                            f"safety; matrices already identity)")
                arm.parent = None

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

    # ================================================================
    # 7.5 TEXTURES (TXD) - ISOLATED LIKE COL: a TXD problem NEVER fails
    #     the DFF/COL. Uses the official DragonFF TXD writer
    #     (DragonFF/gtaLib/txd.py - the same module family as the DFF and
    #     COL exports): a proper RenderWare 3.6.0.3 (0x1803FFFF) "PC" TXD
    #     v5 dictionary with BGRA8888 (D3DFMT_A8R8G8B8) textures.
    #     The DFF and TXD share EXACTLY the same texture names - both are
    #     derived from the material's base-color image with DragonFF's own
    #     naming helpers (extract_texture_info_from_name + clear_extension)
    #     - so SA/MTA resolves each DFF material to its texture
    #     automatically (engineReplaceModel(dff, txd)).
    # ================================================================
    log("INFO", "=== Texture (TXD) stage ===")
    txd = status["txd"]
    try:
        from DragonFF.gtaLib.txd import txd as TxDCls, TextureNative
        from DragonFF.gtaLib.txd import (ImageEncoder, DeviceType,
                                         D3DFormat, RasterFormat)
        from DragonFF.gtaLib.dff import NativePlatformType
        from DragonFF.ops.exporter_common import (clear_extension,
                                                  extract_texture_info_from_name)
        from bpy_extras.node_shader_utils import PrincipledBSDFWrapper
        import numpy as np

        MAX_TEX = 1024  # safe max for the SA renderer

        def _ensure_loaded(img):
            if img is not None and not img.has_data:
                # .fbm sidecar / embedded file may need an explicit load
                try:
                    img.load()
                except Exception:
                    pass
            return img

        def _usable(img):
            return (img is not None and bool(img.has_data)
                    and img.size[0] > 1 and img.size[1] > 1)

        def _candidates(mat):
            """(image, node_label) pairs, most authoritative first.
            #1 is exactly what the DFF exporter uses (base-color image) so
            the TXD name matches the DFF texture name; the rest widen the
            net for real-world FBX node setups: a Mapping/Mix node between
            image and Base Color, an image linked to another input
            (emission/opacity), an unlinked image node, or legacy slots."""
            out = []
            try:
                wrapper = PrincipledBSDFWrapper(mat, is_readonly=False)
                bct = wrapper.base_color_texture
                if bct is not None and bct.image is not None:
                    out.append((bct.image, bct.node_image.label
                                if bct.node_image else ""))
                    return out
            except Exception:
                pass
            tree = mat.node_tree
            if tree is not None:
                bsdf = next((n for n in tree.nodes
                             if n.type == "BSDF_PRINCIPLED"), None)
                if bsdf is not None:
                    bc = bsdf.inputs.get("Base Color")
                    if bc is not None and bc.is_linked:
                        node = bc.links[0].from_node
                        if node.type == "TEX_IMAGE" and node.image:
                            out.append((node.image, node.label))
                        else:
                            # one level deeper (Mix / RGB / HueSat / ...)
                            for inp in node.inputs:
                                if inp.is_linked:
                                    src = inp.links[0].from_node
                                    if (src.type == "TEX_IMAGE"
                                            and src.image):
                                        out.append((src.image, src.label))
                seen = {id(i) for i, _l in out}
                for n in tree.nodes:
                    if (n.type == "TEX_IMAGE" and n.image is not None
                            and id(n.image) not in seen):
                        out.append((n.image, n.label))
                        seen.add(id(n.image))
            else:
                try:  # legacy pre-2.8 materials
                    for slot in mat.texture_slots or []:
                        if (slot.texture is not None
                                and getattr(slot.texture, "image", None)):
                            out.append((slot.texture.image, ""))
                except Exception:
                    pass
            return out

        entries = {}    # texture name -> (w, h, bgra8888 bytes)
        names_used = set()
        mat_notes = []  # per-material diagnostics (log + reason)
        mats_done = set()
        for ob in mesh_objs:
            for mat in (ob.data.materials or []):
                if mat is None or mat.name in mats_done:
                    continue
                mats_done.add(mat.name)
                cands = _candidates(mat)
                if not cands:
                    mat_notes.append(f"{mat.name}: no image node")
                    continue
                chosen = None
                for img_c, label_c in cands:
                    img_c = _ensure_loaded(img_c)
                    if _usable(img_c):
                        chosen = (img_c, label_c)
                        break
                if chosen is None:
                    bimg = _ensure_loaded(cands[0][0])
                    mat_notes.append(
                        f"{mat.name}: image '{bimg.name}' not available "
                        f"(file missing? {bimg.filepath or 'no path'})")
                    txd.setdefault("skipped", []).append(mat.name)
                    continue
                img, node_label = chosen
                image_name = img.name
                if node_label and node_label != "Image" \
                        and node_label in image_name:
                    image_name = node_label
                tname, _ = extract_texture_info_from_name(image_name)
                tname = clear_extension(tname) or img.name
                if not tname:
                    mat_notes.append(f"{mat.name}: empty texture name")
                    continue
                if tname in names_used:
                    mat_notes.append(
                        f"{mat.name}: '{tname}' already used by another "
                        f"material (shared texture)")
                    continue
                # --- size cap (SA renderer max 1024) ----------------------
                w, h = img.size
                if max(w, h) > MAX_TEX:
                    s = MAX_TEX / float(max(w, h))
                    nw, nh = max(1, int(w * s)), max(1, int(h * s))
                    img.scale(nw, nh)
                    w, h = nw, nh
                # --- pixels: RGBA -> BGRA8888, rows top-down -------------
                # (Blender pixels are bottom-up, RenderWare/D3D rows are
                #  top-down - flip vertically)
                buf = np.empty(w * h * 4, dtype=np.float32)
                img.pixels.foreach_get(buf)
                buf = np.clip(buf.reshape(h, w, 4), 0.0, 1.0)[::-1]
                rgba = np.round(buf * 255.0).astype(np.uint8).tobytes()
                names_used.add(tname)
                entries[tname] = (w, h, ImageEncoder.rgba_to_bgra8888(rgba))
                mat_notes.append(f"{mat.name}: '{tname}' ({w}x{h})")

        for note in mat_notes:
            log("INFO", "TXD material: " + note)
        if entries:
            log("INFO", f"TXD: {len(entries)} texture(s) extracted from "
                        f"{len(mats_done)} material(s)")
            if txd.get("skipped"):
                log("WARN", "TXD: materials skipped (no usable image): "
                            + ", ".join(txd["skipped"]))

        if not entries:
            detail = "; ".join(mat_notes[:6])
            more = (f" (+{len(mat_notes) - 6} more)"
                    if len(mat_notes) > 6 else "")
            if not detail:
                detail = "no materials found on the mesh"
            txd["reason"] = (
                f"no usable textures in {len(mats_done)} material(s) "
                f"[{detail}{more}]. The FBX carries no embedded texture "
                f"data, so a TXD cannot be created. To get a TXD, re-export "
                f"the FBX with textures embedded (or bake the textures "
                f"into image textures linked to the materials).")
            log("WARN", "TXD: " + txd["reason"])
        else:
            tobj = TxDCls()
            tobj.device_id = DeviceType.DEVICE_D3D9
            for tname, (w, h, bgra) in entries.items():
                tex = TextureNative()
                tex.platform_id = NativePlatformType.D3D9   # 9 = PC/D3D9
                tex.filter_mode = 0x02                      # bilinear
                tex.uv_addressing = 0x11                    # u=WRAP, v=WRAP
                tex.name = tname
                tex.mask = ""
                tex.raster_format_flags = RasterFormat.RASTER_8888
                tex.d3d_format = D3DFormat.D3D_8888         # D3DFMT_A8R8G8B8
                tex.width = w
                tex.height = h
                tex.depth = 1
                tex.num_levels = 1
                tex.raster_type = 0
                from collections import namedtuple
                PP = namedtuple("PlatformProperties",
                                ["alpha", "cube_texture", "auto_mipmaps",
                                 "compressed"])
                tex.platform_properties = PP(True, False, False, False)
                tex.palette = b""
                tex.pixels = [bgra]
                tobj.native_textures.append(tex)
            tobj.write_file(txd_out, 0x36003)  # RW 3.6.0.3 -> 0x1803FFFF
            txd["success"] = True
            txd["textures"] = len(entries)
            txd["names"] = list(entries)
            txd["size_bytes"] = os.path.getsize(txd_out)
            # read-back validation with DragonFF's own TXD parser
            check = TxDCls()
            check.load_file(txd_out)
            if len(check.native_textures) != len(entries):
                raise RuntimeError(
                    f"TXD read-back mismatch: wrote {len(entries)}, "
                    f"read {len(check.native_textures)}")
            log("SUCCESS", f"model.txd created: {txd_out} "
                f"({len(entries)} texture(s), {txd['size_bytes']} bytes) - "
                f"validated with DragonFF TXD reader")
            log("INFO", "TXD textures: " + ", ".join(
                f"{n} ({w}x{h})" for n, (w, h, _b) in entries.items()))
    except Exception as e:
        txd["success"] = False
        txd["reason"] = f"TXD stage error: {e}"
        log("ERROR", f"TXD stage error (DFF unaffected): {e}")
        if os.path.exists(txd_out) and os.path.getsize(txd_out) == 0:
            os.remove(txd_out)

    with open(args.status, "w") as f:
        json.dump(status, f, indent=2)

    # ================================================================
    # 8. COLLISION (COL) - NEW STAGE, FULLY ISOLATED FROM THE DFF
    #    A COL failure is recorded in status["col"] and NEVER fails the
    #    DFF (the DFF is already exported + verified at this point).
    #    The collision mesh is built from the SAME processed mesh that
    #    went into the DFF (identity world space) => DFF and COL
    #    transforms/scale/rotation/position match exactly.
    # ================================================================
    if args.col:
        log("INFO", "=== Collision (COL) stage ===")
        col = status["col"]
        col_obj = None
        last_err = None
        budget = None

        def clog(msg):
            # collision_generator logs single-arg messages with [LEVEL] prefixes
            for lvl in ("ERROR", "WARN", "SUCCESS"):
                if f"[{lvl}]" in msg:
                    log(lvl, msg)
                    return
            log("INFO", msg)

        try:
            import collision_generator as cgen

            orig_tris = sum(len(o.data.polygons) for o in mesh_objs)
            col["original_tris"] = orig_tris
            budget = cgen.pick_collision_budget(
                orig_tris, args.col_quality, args.col_triangles or None, clog)

            # auto-retry ladder: rebuild collision mesh -> re-clean ->
            # remove invalid faces -> reduce triangle count -> re-export
            target = budget
            for attempt in (1, 2):
                col["budget_tris"] = target
                if attempt > 1:
                    log("WARN", f"COL auto-retry {attempt - 1}: rebuilding collision mesh "
                        f"(re-clean + reduced target {target} tris)")
                try:
                    col_obj, stats = cgen.build_collision_mesh(mesh_objs, target, clog)
                    col["final_tris"] = stats["final_tris"]
                    col["verts"] = stats["verts"]
                    cgen.export_col(col_obj, col_out, clog)
                    cgen.remove_collision_obj(col_obj, clog)
                    col_obj = None
                    col["success"] = True
                    col["reason"] = None
                    col["size_bytes"] = os.path.getsize(col_out)
                    log("SUCCESS", f"model.col created: {col_out} "
                        f"({col['size_bytes']} bytes, {col['final_tris']} triangles)")
                    break
                except Exception as e:
                    last_err = str(e)
                    log("ERROR", f"COL attempt {attempt} failed: {e}")
                    cgen.remove_collision_obj(col_obj, clog)
                    col_obj = None
                    if os.path.exists(col_out) and os.path.getsize(col_out) == 0:
                        os.remove(col_out)
                    target = max(1, int(target * 0.75))

            if not col["success"]:
                col["reason"] = f"COL generation failed after 2 attempts: {last_err}"
                log("ERROR", f"COL FAILED (DFF unaffected): {last_err}")
        except Exception as e:
            col["success"] = False
            col["reason"] = f"COL stage error: {e}"
            log("ERROR", f"COL stage error (DFF unaffected): {e}")

        with open(args.status, "w") as f:
            json.dump(status, f, indent=2)

    # ---- clean up the .fbm sidecar the FBX import created (if we created it)
    if not fbm_existed and os.path.isdir(fbm_dir):
        shutil.rmtree(fbm_dir, ignore_errors=True)

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
