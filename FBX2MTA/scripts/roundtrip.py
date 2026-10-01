#!/usr/bin/env python3
"""
FBX2MTA - Round-trip test
=========================
DFF -> DragonFF Import -> Blender, then compares the re-imported scene against
the export status (triangles, vertices, bones, materials, textures).

Runs inside a Blender/bpy process:
    run_blender.sh scripts/roundtrip.py --dff out.dff --status temp/x.json \
        --rt-status temp/x_rt.json --log logs/rt.log
"""
import sys, os, json, argparse, traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # FBX2MTA/
sys.path.insert(0, os.path.join(ROOT, "dragonff"))

LOG_PATH = None
def log(level, msg):
    line = f"[{level}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def main():
    global LOG_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--dff", required=True)
    ap.add_argument("--status", required=True)
    ap.add_argument("--rt-status", required=True)
    ap.add_argument("--log", required=True)
    args = ap.parse_args()
    LOG_PATH = args.log
    open(args.log, "w").close()

    with open(args.status) as f:
        orig = json.load(f)

    import bpy
    import DragonFF
    DragonFF.register()
    log("INFO", "Round-trip: importing DFF back through DragonFF")
    bpy.ops.wm.read_factory_settings(use_empty=True)

    try:
        r = bpy.ops.import_scene.dff(filepath=args.dff)
    except Exception:
        log("ERROR", "import_scene.dff raised:\n" + traceback.format_exc())
        write_rt(False, "import operator failed")
        return

    if r != {"FINISHED"}:
        log("ERROR", f"import_scene.dff result: {r}")
        write_rt(False, f"import result {r}")
        return

    # gather stats
    mesh_objs = [o for o in bpy.data.objects if o.type == "MESH"]
    arm_objs = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    tris = sum(len(o.data.polygons) for o in mesh_objs)
    verts = sum(len(o.data.vertices) for o in mesh_objs)
    bones = sum(len(a.data.bones) for a in arm_objs)
    mats = len(bpy.data.materials)
    vgroups = sum(len(o.vertex_groups) for o in mesh_objs)
    uvlayers = sum(len(o.data.uv_layers) for o in mesh_objs)

    orig_tris = orig.get("processed", {}).get("triangles")
    orig_verts = orig.get("processed", {}).get("vertices")
    orig_frames = orig.get("frames", 0)
    orig_mats = orig.get("materials", {}).get("count", 0)

    log("INFO", f"Re-imported: meshes={len(mesh_objs)} tris={tris} verts={verts} "
                f"armatures={len(arm_objs)} bones={bones} mats={mats} vgroups={vgroups} uv_layers={uvlayers}")
    log("INFO", f"Expected:      tris={orig_tris} verts={orig_verts} frames={orig_frames} mats={orig_mats}")

    failures = []
    if not mesh_objs:
        failures.append("no mesh re-imported")
    if orig_tris is not None and tris != orig_tris:
        failures.append(f"triangle mismatch: exported {orig_tris}, re-imported {tris}")
    if orig_frames and bones != orig_frames:
        failures.append(f"bone mismatch: exported {orig_frames}, re-imported {bones}")
    if orig_mats and mats != orig_mats:
        failures.append(f"material mismatch: exported {orig_mats}, re-imported {mats}")
    if orig.get("kind") == "skinned" and vgroups == 0:
        failures.append("skinning lost on round-trip (no vertex groups)")
    if orig.get("kind") == "skinned" and not arm_objs:
        failures.append("armature lost on round-trip")

    # save the round-tripped blend for inspection
    blend_path = args.dff.replace(".dff", "_roundtrip.blend")
    try:
        bpy.ops.wm.save_mainfile(filepath=blend_path)
        log("INFO", f"Round-trip scene saved: {blend_path}")
    except Exception as e:
        log("WARN", f"could not save round-trip blend: {e}")

    write_rt(not failures, "; ".join(failures), {
        "triangles": tris, "vertices": verts, "bones": bones,
        "materials": mats, "vertex_groups": vgroups, "uv_layers": uvlayers,
    })

def write_rt(ok, reason="", stats=None):
    global LOG_PATH
    result = {"roundtrip": "PASS" if ok else "FAIL", "reason": reason, "stats": stats or {}}
    # find args
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dff", required=True); ap.add_argument("--status", required=True)
    ap.add_argument("--rt-status", required=True); ap.add_argument("--log", required=True)
    a, _ = ap.parse_known_args(sys.argv)
    with open(a.rt_status, "w") as f:
        json.dump(result, f, indent=2)
    log("SUCCESS" if ok else "ERROR", f"Round-trip test: {'PASS' if ok else 'FAIL'} {reason}")
    if not ok:
        sys.exit(1)

if __name__ == "__main__":
    # os._exit(): the bpy wheel can segfault during normal interpreter shutdown;
    # all results are already written before we get here.
    try:
        main()
        os._exit(0)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        os._exit(code)
    except Exception:
        log("ERROR", "Round-trip crashed:\n" + traceback.format_exc())
        os._exit(1)
