#!/usr/bin/env python3
"""
FBX2MTA - Standalone DFF validator
==================================
Re-reads the exported DFF with DragonFF's own gtaLib/dff.py module and verifies
the RenderWare structure: chunks, geometry, materials, indices, vertices, UVs,
skin data, frames. Runs in plain Python (no Blender required).

Usage: validate_dff.py <file.dff> [--expect-tris N] [--expect-verts N]
Exit code 0 = PASS, 1 = FAIL
"""
import sys, os, json, math

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # FBX2MTA/
# gtaLib is standalone pure-python (no bpy); point at the addon dir directly
sys.path.insert(0, os.path.join(ROOT, "dragonff", "DragonFF"))

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        print("usage: validate_dff.py <file.dff>")
        sys.exit(2)

    expect = {}
    if "--expect-tris" in sys.argv:
        expect["tris"] = int(sys.argv[sys.argv.index("--expect-tris") + 1])
    if "--expect-verts" in sys.argv:
        expect["verts"] = int(sys.argv[sys.argv.index("--expect-verts") + 1])

    results = {"file": path, "checks": {}, "failures": [], "info": {}}

    def check(name, ok, detail=""):
        results["checks"][name] = "PASS" if ok else "FAIL"
        if detail:
            results["info"][name] = detail
        if not ok:
            results["failures"].append(name + (f": {detail}" if detail else ""))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))

    # --- file level
    check("exists", os.path.exists(path), path)
    if not os.path.exists(path):
        finish(results); return
    size = os.path.getsize(path)
    check("size>0", size > 0, f"{size} bytes")

    raw = open(path, "rb").read()
    # DFF layout: [4B library id][chunk: size|type=RWClump(0x1803FFFF)|...]
    rwclump = (0x1803FFFF).to_bytes(4, "little")
    check("signature", rwclump in raw[:16],
          f"RWClump fourcc at offset {raw[:16].find(rwclump)}")

    # --- parse with DragonFF's dff module
    from gtaLib import dff as dffmod
    d = dffmod.dff()
    try:
        d.load_file(path)
        parsed = True
    except Exception as e:
        parsed = False
        print("  parse error:", e)
    check("parsed_by_dragonff", parsed)
    if not parsed:
        finish(results); return

    def fmt_rw(v):
        try:
            v = int(v)
            # RW version word: (major<<16) | (minor<<12) | (build<<4) | patch
            return f"v{(v >> 16) & 0xFF}.{(v >> 12) & 0xF}.{(v >> 4) & 0xF}.{v & 0xF} (0x{v:05X})"
        except Exception:
            return str(v)
    check("rw_version", bool(d.rw_version), fmt_rw(d.rw_version))
    results["info"]["rw_version"] = fmt_rw(d.rw_version)
    check("has_clump", len(d.clumps) > 0, f"{len(d.clumps)} clump(s)")

    n_frames = n_geos = n_atomics = n_tris = n_verts = 0
    n_mats = 0
    tex_names, mat_fx = set(), set()
    skin_bones = 0
    bad_indices = bad_finite = bad_uv = 0
    mat_count = 0

    for ci, clump in enumerate(d.clumps):
        n_frames += len(clump.frame_list)
        n_geos += len(clump.geometry_list)
        n_atomics += len(clump.atomic_list)

        # frames
        for fi, fr in enumerate(clump.frame_list):
            p = fr.position
            if not all(math.isfinite(v) for v in (p.x, p.y, p.z)):
                bad_finite += 1
            rm = fr.rotation_matrix
            if not all(math.isfinite(v) for row in rm for v in row):
                bad_finite += 1
            # -1 = root frame (no parent) - valid in RenderWare
            if fr.parent is not None and fr.parent != -1 and not (0 <= fr.parent < n_frames):
                check(f"frame[{fi}].parent", False, str(fr.parent))

        # geometry
        for gi, geo in enumerate(clump.geometry_list):
            nv = len(geo.vertices)
            n_verts += nv
            nt = len(geo.triangles)
            n_tris += nt
            mat_count += len(geo.materials)

            # finite vertices/normals
            for v in geo.vertices:
                if not (math.isfinite(v.x) and math.isfinite(v.y) and math.isfinite(v.z)):
                    bad_finite += 1
            for nrm in geo.normals:
                if not (math.isfinite(nrm.x) and math.isfinite(nrm.y) and math.isfinite(nrm.z)):
                    bad_finite += 1
            # UV layers
            for layer in geo.uv_layers:
                if len(layer) != nv:
                    bad_uv += 1
                for uv in layer:
                    if not (math.isfinite(uv.u) and math.isfinite(uv.v)):
                        bad_uv += 1
            # triangles + indices
            for t in geo.triangles:
                for idx in (t.a, t.b, t.c):
                    if idx >= nv:
                        bad_indices += 1
                if t.material >= len(geo.materials):
                    bad_indices += 1
            # materials
            for m in geo.materials:
                n_mats += 1
                for t in m.textures:
                    tex_names.add(t.name)
                if "bump_map" in m.plugins:
                    mat_fx.add("bump_map")
                    for bm in m.plugins["bump_map"]:
                        for t in (bm.bump_map, bm.height_map):
                            if t is not None and t.name:
                                tex_names.add(t.name)
                if "env_map" in m.plugins:
                    mat_fx.add("env_map")
                if "spec" in m.plugins:
                    mat_fx.add("spec")
            # skin
            skin = geo.extensions.get("skin")
            if skin:
                skin_bones = max(skin_bones, skin.num_bones)
                if len(skin.bone_matrices) != skin.num_bones:
                    check("skin.bone_matrices", False,
                          f"{len(skin.bone_matrices)} != {skin.num_bones}")
                for bm in skin.bone_matrices:
                    if not all(math.isfinite(v) for row in bm for v in row):
                        bad_finite += 1
                vi_b = len(skin.vertex_bone_indices)
                if vi_b != nv:
                    check("skin.vertex_index_count", False, f"{vi_b} != {nv}")
                else:
                    for i, (inds, wts) in enumerate(zip(skin.vertex_bone_indices, skin.vertex_bone_weights)):
                        for b in inds:
                            if b >= skin.num_bones:
                                bad_indices += 1
                        for w in wts:
                            if not math.isfinite(w):
                                bad_finite += 1

        # atomics
        for ai, at in enumerate(clump.atomic_list):
            if at.frame is None or at.frame >= n_frames:
                check(f"atomic[{ai}].frame", False, str(at.frame))
            if at.geometry >= n_geos:
                check(f"atomic[{ai}].geometry", False, str(at.geometry))

    results["info"].update({
        "clumps": len(d.clumps), "frames": n_frames, "geometries": n_geos,
        "atomics": n_atomics, "triangles": n_tris, "vertices": n_verts,
        "materials": mat_count, "textures": sorted(tex_names), "effects": sorted(mat_fx),
        "skin_bones": skin_bones,
    })
    print(f"  info: frames={n_frames} geos={n_geos} atomics={n_atomics} "
          f"tris={n_tris} verts={n_verts} mats={mat_count} skin_bones={skin_bones}")
    print(f"  textures: {sorted(tex_names)}")
    print(f"  material effects: {sorted(mat_fx) or 'none'}")

    check("finite_data", bad_finite == 0, f"bad values: {bad_finite}")
    check("valid_indices", bad_indices == 0, f"bad indices: {bad_indices}")
    check("valid_uvs", bad_uv == 0, f"uv problems: {bad_uv}")
    check("has_geometry", n_tris > 0 and n_verts > 0, f"{n_tris} tris / {n_verts} verts")
    if expect.get("tris"):
        check("tri_count_matches", n_tris == expect["tris"], f"expected {expect['tris']}, got {n_tris}")
    if expect.get("verts"):
        check("vert_count_matches", n_verts == expect["verts"], f"expected {expect['verts']}, got {n_verts}")

    finish(results)

def finish(results):
    ok = not results["failures"]
    print(f"VALIDATION {'PASS' if ok else 'FAIL'}")
    if results["failures"]:
        for f in results["failures"]:
            print("  FAILED CHECK:", f)
    out = os.path.join(os.path.dirname(results["file"]), os.path.basename(results["file"]) + ".validation.json")
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
