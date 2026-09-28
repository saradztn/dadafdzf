"""Test-FBX generator (runs inside headless Blender/bpy).

Creates a real, non-trivial low-poly test model (a small winged creature:
body + head + snout + tail + two wings + legs, UV mapped, one material) and
exports it as an FBX so the pipeline can be tested end-to-end without an
external file.

Usage (via run_blender.sh):
    run_blender.sh scripts/generate_test_fbx.py --output <path.fbx> [--log <f>]
"""
import argparse
import math
import os
import sys
import time
import traceback

LOG_PATH = ""


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if LOG_PATH:
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")


def main_animated(output):
    """Small rigged test character (3 bones, skinned mesh, 30-frame arm
    swing) - used to test the IFP (animation) pipeline end-to-end."""
    import bpy

    log(f"Blender {bpy.app.version_string} - generating ANIMATED test FBX")
    bpy.ops.wm.read_factory_settings(use_empty=True)

    # ---- rig: root (Z) <- spine (Z) <- arm (+X)
    arm_data = bpy.data.armatures.new("TestRig")
    arm_obj = bpy.data.objects.new("TestRig", arm_data)
    bpy.context.collection.objects.link(arm_obj)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm_data.edit_bones
    root = eb.new("root"); root.head = (0, 0, 0); root.tail = (0, 0, 1.0)
    spine = eb.new("spine"); spine.head = (0, 0, 1.0)
    spine.tail = (0, 0, 2.0); spine.parent = root
    arm = eb.new("arm"); arm.head = (0, 0, 1.8)
    arm.tail = (0.9, 0, 1.8); arm.parent = spine
    bpy.ops.object.mode_set(mode="OBJECT")

    # ---- meshes: torso cube (skinned to spine) + forearm cube (skinned to arm)
    bpy.ops.mesh.primitive_cube_add(size=0.6, location=(0, 0, 1.5))
    torso = bpy.context.active_object
    torso.name = "Torso"
    bpy.ops.mesh.primitive_cube_add(size=0.5, location=(0.45, 0, 1.8))
    fore = bpy.context.active_object
    fore.name = "ForeArmMesh"
    bpy.ops.object.select_all(action="DESELECT")
    torso.select_set(True)
    fore.select_set(True)
    bpy.context.view_layer.objects.active = torso
    bpy.ops.object.join()
    model = bpy.context.active_object
    model.name = "TestCharacter"

    # parent to armature + weights by position (x > 0.15 -> arm, else spine)
    model.parent = arm_obj
    vg_arm = model.vertex_groups.new(name="arm")
    vg_spine = model.vertex_groups.new(name="spine")
    arm_idx, spine_idx = [], []
    for v in model.data.vertices:
        (arm_idx if v.co.x > 0.15 else spine_idx).append(v.index)
    vg_arm.add(arm_idx, 1.0, "REPLACE")
    vg_spine.add(spine_idx, 1.0, "REPLACE")

    # UV + simple material (no external files)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")
    mat = bpy.data.materials.new("TestMaterial")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.2, 0.5, 0.8, 1.0)
    model.data.materials.append(mat)

    # ---- animation: 30-frame horizontal arm swing @ 30 fps
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 30
    scene.render.fps = 30
    scene.render.fps_base = 1.0
    pb = arm_obj.pose.bones["arm"]
    pb.rotation_mode = "XYZ"
    pb.keyframe_insert("rotation_euler", frame=1)
    pb.rotation_euler[2] = math.radians(-45)
    pb.keyframe_insert("rotation_euler", frame=15)
    pb.rotation_euler[2] = 0.0
    pb.keyframe_insert("rotation_euler", frame=30)

    tris = sum(len(o.data.polygons) for o in (model,))
    log(f"Animated test model: {len(model.data.vertices)} vertices, "
        f"{tris} faces, 3 bones, 30 frames @ 30fps")

    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    model.select_set(True)
    arm_obj.select_set(True)
    bpy.context.view_layer.objects.active = arm_obj
    r = bpy.ops.export_scene.fbx(
        filepath=output,
        use_selection=True,
        object_types={"MESH", "ARMATURE"},
        use_mesh_modifiers=True,
        use_armature_deform_only=True,
        add_leaf_bones=False,
        bake_anim=True,
        path_mode="STRIP",
        apply_scale_options="FBX_SCALE_ALL",
    )
    assert r == {"FINISHED"}, f"FBX export failed: {r}"
    size = os.path.getsize(output)
    assert size > 0
    log(f"[SUCCESS] Animated test FBX created: {output} ({size} bytes)")


def main(output):
    import bpy

    log(f"Blender {bpy.app.version_string} - generating test FBX")
    bpy.ops.wm.read_factory_settings(use_empty=True)

    parts = []

    # body: elongated icosphere (length along X, height along Z)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=1.0, location=(0, 0, 1.2))
    body = bpy.context.active_object
    body.scale = (1.8, 0.75, 0.85)
    bpy.ops.object.transform_apply(scale=True)
    parts.append(body)

    # head
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=0.55, location=(2.0, 0, 1.6))
    head = bpy.context.active_object
    head.scale = (1.3, 0.8, 0.8)
    bpy.ops.object.transform_apply(scale=True)
    parts.append(head)

    # snout
    bpy.ops.mesh.primitive_cone_add(vertices=8, radius1=0.28, radius2=0.05,
                                    depth=0.7, location=(2.85, 0, 1.55),
                                    rotation=(0, math.radians(-90), 0))
    parts.append(bpy.context.active_object)

    # tail (tapered cone pointing back)
    bpy.ops.mesh.primitive_cone_add(vertices=8, radius1=0.45, radius2=0.04,
                                    depth=2.2, location=(-2.2, 0, 1.1),
                                    rotation=(0, math.radians(90), 0))
    parts.append(bpy.context.active_object)

    # wings: two flat scaled icospheres
    for side in (-1, 1):
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=1.0,
                                              location=(-0.3, side * 1.7, 2.0))
        w = bpy.context.active_object
        w.scale = (1.1, 1.4, 0.06)
        w.rotation_euler = (0, 0, math.radians(side * 15))
        bpy.ops.object.transform_apply(scale=True, rotation=True)
        parts.append(w)

    # legs
    for lx in (1.0, -1.1):
        for side in (-1, 1):
            bpy.ops.mesh.primitive_cylinder_add(vertices=8, radius=0.14, depth=0.9,
                                                location=(lx, side * 0.45, 0.45))
            parts.append(bpy.context.active_object)

    # join everything into one mesh
    bpy.ops.object.select_all(action="DESELECT")
    for p in parts:
        p.select_set(True)
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.join()
    model = bpy.context.active_object
    model.name = "TestCreature"

    # UV smart project (FBX/DFF need UVs)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")

    # single material with a solid-color texture (no external files)
    mat = bpy.data.materials.new("TestMaterial")
    mat.use_nodes = True
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    img = bpy.data.images.new("TestTex", 4, 4, alpha=False)
    img.generated_color = (0.75, 0.45, 0.2, 1.0)
    tex.image = img
    mat.node_tree.links.new(tex.outputs["Color"],
                            mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
    model.data.materials.append(mat)

    tris = len(model.data.polygons)
    log(f"Test model: {len(model.data.vertices)} vertices, {tris} faces")

    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    r = bpy.ops.export_scene.fbx(
        filepath=output,
        use_selection=True,
        object_types={"MESH"},
        use_mesh_modifiers=True,
        path_mode="AUTO",
        apply_scale_options="FBX_SCALE_ALL",
    )
    assert r == {"FINISHED"}, f"FBX export failed: {r}"
    size = os.path.getsize(output)
    assert size > 0
    log(f"[SUCCESS] Test FBX created: {output} ({size} bytes)")


if __name__ == "__main__":
    # NOTE: os._exit() - the bpy wheel can segfault during CPython shutdown
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    ap.add_argument("--log", default="")
    ap.add_argument("--animated", action="store_true",
                    help="rigged character with a 30-frame arm swing "
                         "(tests the IFP animation pipeline)")
    args = ap.parse_args()
    try:
        if args.log:
            LOG_PATH = args.log
            os.makedirs(os.path.dirname(LOG_PATH) or ".", exist_ok=True)
            open(LOG_PATH, "w").close()
        if args.animated:
            main_animated(args.output)
        else:
            main(args.output)
        os._exit(0)
    except Exception:
        err = traceback.format_exc()
        try:
            if LOG_PATH:
                with open(LOG_PATH, "a") as f:
                    f.write(err)
        except Exception:
            pass
        print(err)
        os._exit(1)
