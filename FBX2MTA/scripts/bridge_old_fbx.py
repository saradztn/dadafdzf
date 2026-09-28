#!/usr/bin/env python3
"""Old-FBX bridge (runs inside the bpy 5.0 venv).

Blender 4.2's FBX importer refuses files < 7.1 (e.g. FBX 6.100 from
3ds Max 2008-era tools). Blender 5.0.s new C++ importer
(bpy.ops.wm.fbx_import) supports them. This script imports the old file with
the 5.0 engine and re-exports it as a standard FBX (7.4) that the main
4.2.23 + DragonFF pipeline can process normally.

Usage (via the driver):
    run_blender.py --venv venv45 scripts/bridge_old_fbx.py --input X --output Y
"""
import os
import sys
import traceback

import bpy

BPY_MIN = (5, 0)


def log(m):
    print(f"[bridge] {m}", flush=True)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    # headless bpy resolves operator filepaths against an unpredictable CWD
    a.input = os.path.abspath(a.input)
    a.output = os.path.abspath(a.output)

    if tuple(bpy.app.version)[:2] < BPY_MIN:
        log(f"ERROR: bridge needs Blender 5.0+ (got {bpy.app.version_string})")
        return 1
    if not hasattr(bpy.ops.wm, "fbx_import"):
        log("ERROR: bpy.ops.wm.fbx_import (new FBX importer) is not available "
            "in this Blender build")
        return 1

    # ---- import with the new importer (supports old FBX versions)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    log(f"Blender {bpy.app.version_string} new-importer: {a.input}")
    try:
        bpy.ops.wm.fbx_import(filepath=a.input)
    except Exception:
        log("ERROR: wm.fbx_import failed:\n" + traceback.format_exc())
        return 1

    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    if not meshes:
        log("ERROR: imported scene contains no mesh objects - nothing to bridge")
        return 1
    verts = sum(len(o.data.vertices) for o in meshes)
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    log(f"import OK: {len(meshes)} mesh object(s), {verts:,} vertices"
        + (f", {len(arms)} armature(s)" if arms else ""))

    # ---- re-export as standard FBX (7.4) for the main 4.2.23 pipeline
    # keep the scene faithful: meshes + armatures + empties (frame structure),
    # no animations (the DFF stage is mesh-only), no texture file copying
    os.makedirs(os.path.dirname(a.output) or ".", exist_ok=True)
    try:
        bpy.ops.export_scene.fbx(
            filepath=a.output,
            use_selection=False,
            object_types={"EMPTY", "MESH", "ARMATURE"},
            mesh_smooth_type="FACE",
            use_armature_deform_only=True,
            add_leaf_bones=False,
            bake_anim=False,
            path_mode="STRIP",
        )
    except Exception:
        log("ERROR: FBX re-export failed:\n" + traceback.format_exc())
        return 1
    if not (os.path.exists(a.output) and os.path.getsize(a.output) > 0):
        log("ERROR: re-export produced no file")
        return 1
    log(f"bridge complete: {a.output} ({os.path.getsize(a.output):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
