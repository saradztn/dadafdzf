#!/usr/bin/env python3
"""
FBX2MTA - Pipeline driver (batch)
=================================
Scans input/*.fbx, converts every file:
    FBX -> Blender(bpy) + DragonFF -> DFF (GTA SA v3.6.0.3)
    -> standalone DFF validation (DragonFF gtaLib)
    -> round-trip test (DragonFF import -> Blender)
    -> REAL COLLISION (COL) generation (DragonFF COL export, COL3 for GTA SA/MTA)
    -> standalone COL validation (DragonFF gtaLib col parser)
    -> output/<name>.dff + output/<name>.col
Writes reports/model_report.txt and appends to logs/converter.log.
Never touches input files.

COL is fully isolated: a COL failure records COL=FAILED (with reason)
while the DFF still succeeds (DFF=PASS).

Usage:
    python3 run_pipeline.py [--budget AUTO|N]
                            [--no-col] [--col-quality AUTO|LOW|MEDIUM|HIGH|CUSTOM]
                            [--col-triangles N]
"""
import os, sys, json, glob, subprocess, time, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # FBX2MTA/
sys.path.insert(0, ROOT)  # make joblib importable
PY = sys.executable
# cross-platform headless Blender runner (Windows: no bash needed)
RUN_BLENDER = os.path.join(ROOT, "blender", "run_blender.py")

LOG = open(os.path.join(ROOT, "logs", "converter.log"), "a")
def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.write(line + "\n"); LOG.flush()

def run(cmd, timeout=3600):
    flat = []
    for part in cmd:
        flat.extend(part if isinstance(part, (list, tuple)) else [part])
    log("CMD: " + " ".join(flat))
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    tail = (p.stdout or "").strip().splitlines()[-25:]
    for l in tail:
        LOG.write("    " + l + "\n")
    if p.returncode != 0 and p.stderr:
        err = p.stderr.strip().splitlines()[-15:]
        for l in err:
            LOG.write("    [stderr] " + l + "\n")
    LOG.flush()
    return p

def find_mta():
    cands = [
        "/home/user/GTA San Andreas", "/mnt/g/SteamLibrary",
        os.path.expanduser("~/Games/MTA San Andreas"),
        "/opt/mta", "/home/user/MTA San Andreas",
        r"C:\Program Files (x86)\Rockstar Games\GTA San Andreas",
    ]
    for c in cands:
        if os.path.exists(c) and any(f.lower().endswith(".exe") or f.lower() in ("mta.exe","gta_sa.exe") for f in os.listdir(c) if os.path.isfile(os.path.join(c,f))):
            return c
    return None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mta_resource import make_test_resource  # noqa: E402
from fbx_compat import fbx_version, FBX_MIN_OK  # noqa: E402

VENV45_PY = os.path.join(ROOT, "blender", "venv45",
                         "Scripts" if os.name == "nt" else "bin",
                         "python.exe" if os.name == "nt" else "python")


def bridge_old_fbx(inp, name):
    """Re-export an old FBX (< 7.1) through Blender 5.0.s new importer.

    Returns the bridged file path, or None on failure. The 5.0 engine is a
    second, optional venv (venv45) created on demand - only old FBX files
    ever trigger it.
    """
    out = os.path.join(ROOT, "temp", name + ".bridge.fbx")
    if not os.path.exists(VENV45_PY):
        log("Blender 5.0 bridge engine missing - creating it (one-time "
            "~350MB download, only needed for old FBX files)...")
        p = subprocess.run([sys.executable,
                            os.path.join(ROOT, "blender", "setup_env.py"),
                            "--venv", "venv45", "--bpy", "bpy==5.0.1"],
                           capture_output=True, text=True, timeout=7200)
        tail = (p.stdout or p.stderr or "").strip().splitlines()[-8:]
        for l in tail:
            log("  [setup45] " + l)
        if p.returncode != 0 or not os.path.exists(VENV45_PY):
            log("ERROR: could not create the Blender 5.0 bridge engine "
                "(needed to import FBX older than 7.1)")
            return None
        log("Blender 5.0 bridge engine ready")
    if os.path.exists(out):
        os.remove(out)
    p = run([PY, RUN_BLENDER, "--venv", "venv45",
             os.path.join(ROOT, "scripts", "bridge_old_fbx.py"),
             "--input", inp, "--output", out], timeout=1800)
    if p.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    log("ERROR: FBX bridge failed (see [bridge] lines above)")
    return None


def main():
    for d in ("temp", "output", "logs", "reports"):
        os.makedirs(os.path.join(ROOT, d), exist_ok=True)
    budget = "AUTO"
    col_enabled = True
    col_quality = "AUTO"
    col_tris = 0
    only_files = []
    if "--budget" in sys.argv:
        budget = sys.argv[sys.argv.index("--budget") + 1]
    if "--no-col" in sys.argv:
        col_enabled = False
    if "--col-quality" in sys.argv:
        col_quality = sys.argv[sys.argv.index("--col-quality") + 1]
    if "--col-triangles" in sys.argv:
        col_tris = int(sys.argv[sys.argv.index("--col-triangles") + 1])
    if "--files" in sys.argv:
        i = sys.argv.index("--files")
        while i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--"):
            only_files.append(sys.argv[i + 1])
            i += 1

    # ---- 0) self-heal: the headless Blender venv (~1GB) may not survive
    #         environment resets/snapshots - rebuild it automatically
    import joblib
    venv_py = joblib.venv_python_path()
    if not os.path.exists(venv_py):
        log("Blender bpy venv missing - rebuilding via blender/setup_env.py "
            "(first time downloads ~350MB, can take a few minutes)...")
        p = subprocess.run([sys.executable,
                            os.path.join(ROOT, "blender", "setup_env.py")],
                           capture_output=True, text=True, timeout=3600)
        tail = (p.stdout or p.stderr or "").strip().splitlines()[-8:]
        for l in tail:
            log("  [setup] " + l)
        if p.returncode != 0 or not os.path.exists(venv_py):
            log("ERROR: could not rebuild the Blender engine - aborting batch "
                "(see [setup] lines above; on Windows install Python 3.11 "
                "from python.org and press Convert again)")
            sys.exit(2)
        log("Blender bpy venv rebuilt OK")

    inputs = sorted({p for p in glob.glob(os.path.join(ROOT, "input", "*"))
                     if p.lower().endswith(".fbx") and os.path.isfile(p)})
    if only_files:
        inputs = [p for p in inputs
                  if os.path.splitext(os.path.basename(p))[0] in only_files]
    log("Starting converter (driver)")
    log(f"Blender backend: headless bpy via {PY} {RUN_BLENDER}")
    log("DragonFF: official Parik27/DragonFF addon (Blender 4.2+)")
    if col_enabled:
        if col_quality == "CUSTOM":
            log(f"Collision: enabled (CUSTOM, max {col_tris} tris)")
        elif col_quality == "AUTO":
            log("Collision: enabled (AUTO - budget chosen by model size)")
        else:
            log(f"Collision: enabled (preset {col_quality})")
    else:
        log("Collision: DISABLED (--no-col)")
    if not inputs:
        log("ERROR: no .fbx files found in input/")
        sys.exit(2)

    summary = {"success": [], "failed": [], "skipped": []}
    reports = []  # (name, inp, status, validation, rt, val, colres)

    for inp in inputs:
        name = os.path.splitext(os.path.basename(inp))[0].replace(" ", "_")
        log("=" * 60)
        log(f"Processing: {os.path.basename(inp)}")

        out_dff = os.path.join(ROOT, "output", name + ".dff")
        out_col = os.path.join(ROOT, "output", name + ".col")
        tmp_dff = os.path.join(ROOT, "temp", name + ".dff")
        tmp_col = os.path.join(ROOT, "temp", name + ".col")
        status_f = os.path.join(ROOT, "temp", name + ".status.json")
        rt_status_f = os.path.join(ROOT, "temp", name + ".rt.json")
        log_f = os.path.join(ROOT, "logs", name + ".log")
        rt_log_f = os.path.join(ROOT, "logs", name + ".rt.log")
        for f in (tmp_dff, tmp_col, status_f, rt_status_f):
            if os.path.exists(f): os.remove(f)

        # ---- 0) old-FBX bridge: Blender 4.2 refuses FBX < 7.1 (e.g. 6.100)
        src = inp
        ver = fbx_version(inp)
        if ver is not None and ver < FBX_MIN_OK:
            log(f"[INFO] FBX version {ver} (< 7100 - older format, e.g. "
                "3ds Max 2008 era): auto-bridging via Blender 5.0 new "
                "importer ...")
            bridged = bridge_old_fbx(inp, name)
            if bridged:
                log(f"[INFO] Bridge OK: {os.path.basename(bridged)} - "
                    "continuing with the regular 4.2.23 pipeline")
                src = bridged
            else:
                reason = (f"FBX version {ver} is older than 7.100 - the "
                          "Blender 4.2 engine cannot import it and the "
                          "automatic bridge (Blender 5.0 new importer) "
                          "failed. Re-export the model from your 3D tool as "
                          "FBX 7.1+ (3ds Max: File > Export > FBX, version "
                          "7.1 or newer) and drop it in input/.")
                summary["failed"].append((name, reason))
                log(f"FAILED: {name} - {reason}")
                continue

        # ---- 1) convert (DFF + COL in the same Blender session)
        conv_cmd = [PY, RUN_BLENDER, os.path.join(ROOT, "scripts", "convert.py"),
                    "--input", src, "--output", tmp_dff,
                    "--budget", budget, "--log", log_f, "--status", status_f]
        if col_enabled:
            conv_cmd += ["--col", "--col-quality", col_quality,
                         "--col-triangles", str(col_tris),
                         "--col-output", tmp_col]
        p = run(conv_cmd)
        if p.returncode != 0 or not os.path.exists(tmp_dff):
            log(f"FAILED: conversion of {name} (see {log_f})")
            err = "conversion failed"
            if os.path.exists(status_f):
                try:
                    s = json.load(open(status_f))
                    err = "; ".join(s.get("errors", ["conversion failed"])) or err
                except Exception: pass
            summary["failed"].append((name, err))
            reports.append((name, inp, None, None, None, None, None))
            continue

        # ---- 2) validate DFF (standalone, DragonFF dff module)
        status = json.load(open(status_f))
        cmd = [PY, os.path.join(ROOT, "scripts", "validate_dff.py"), tmp_dff,
               "--expect-tris", str(status["processed"]["triangles"])]
        p = run(cmd)
        validation = "PASS" if p.returncode == 0 else "FAIL"
        val_json_f = tmp_dff + ".validation.json"
        val = None
        if os.path.exists(val_json_f):
            try:
                val = json.load(open(val_json_f))
            except Exception:
                pass
        if val:
            status["dff_actual"] = {
                "vertices": val["info"].get("vertices"),
                "triangles": val["info"].get("triangles"),
                "frames": val["info"].get("frames"),
                "materials": val["info"].get("materials"),
                "textures": val["info"].get("textures"),
                "effects": val["info"].get("effects"),
                "skin_bones": val["info"].get("skin_bones"),
                "rw_version": val["info"].get("rw_version"),
            }
        if validation == "FAIL":
            log(f"FAILED: DFF validation for {name}")
            summary["failed"].append((name, "DFF validation failed"))
            reports.append((name, inp, status, validation, None, val, None))
            continue

        # ---- 3) round-trip test
        p = run([PY, RUN_BLENDER, os.path.join(ROOT, "scripts", "roundtrip.py"),
                 "--dff", tmp_dff, "--status", status_f,
                 "--rt-status", rt_status_f, "--log", rt_log_f])
        rt = json.load(open(rt_status_f)) if os.path.exists(rt_status_f) else {}
        rt_ok = rt.get("roundtrip") == "PASS"
        if not rt_ok:
            log(f"FAILED: round-trip test for {name}: {rt.get('reason')}")
            summary["failed"].append((name, f"round-trip: {rt.get('reason')}"))
            reports.append((name, inp, status, validation, None, val, None))
            continue

        # ---- 4) COL stage (isolated - DFF is already verified at this point)
        colres = None
        col_status = status.get("col", {})
        if col_enabled:
            if not col_status.get("success"):
                colres = {"status": "FAILED",
                          "reason": col_status.get("reason") or "COL not generated",
                          "final_tris": col_status.get("final_tris", 0),
                          "verts": col_status.get("verts", 0),
                          "original_tris": col_status.get("original_tris", 0),
                          "validation": None}
                log(f"COL FAILED for {name}: {colres['reason']} (DFF still PASS)")
            else:
                # standalone COL validation with DragonFF's own col parser
                p = run([PY, os.path.join(ROOT, "scripts", "validate_col.py"),
                         tmp_col, "--report", tmp_col + ".validation.json"])
                col_val = "PASS" if p.returncode == 0 else "FAIL"
                cv = None
                if os.path.exists(tmp_col + ".validation.json"):
                    try:
                        cv = json.load(open(tmp_col + ".validation.json"))
                    except Exception:
                        pass
                colres = {
                    "status": "PASS" if col_val == "PASS" else "FAILED",
                    "reason": None if col_val == "PASS" else
                              ("; ".join(cv.get("errors", ["COL validation failed"])) if cv
                               else "COL validation failed"),
                    "final_tris": col_status.get("final_tris", 0),
                    "col_tris_in_file": (cv or {}).get("info", {}).get("mesh_triangles"),
                    "verts": (cv or {}).get("info", {}).get("mesh_vertices",
                              col_status.get("verts", 0)),
                    "original_tris": col_status.get("original_tris", 0),
                    "size_bytes": (cv or {}).get("info", {}).get("size_bytes", 0),
                    "quality": col_status.get("quality"),
                    "validation": col_val,
                }
                if col_val == "PASS":
                    log(f"COL validation passed for {name} "
                        f"({colres['col_tris_in_file']} triangles in file)")
                else:
                    log(f"COL validation FAILED for {name}: {colres['reason']} (DFF still PASS)")
        else:
            colres = {"status": "SKIPPED", "reason": "--no-col",
                      "final_tris": 0, "verts": 0, "validation": None}

        # ---- 5) finalize
        shutil.copyfile(tmp_dff, out_dff)
        if os.path.exists(tmp_dff + ".validation.json"):
            shutil.copyfile(tmp_dff + ".validation.json",
                            out_dff + ".validation.json")
        col_out_final = None
        if colres and colres["status"] == "PASS" and os.path.exists(tmp_col):
            shutil.copyfile(tmp_col, out_col)
            col_out_final = out_col
            shutil.copyfile(tmp_col + ".validation.json",
                            out_col + ".validation.json")
            log(f"SUCCESS: {name} -> {out_dff} + {out_col}")
        else:
            log(f"SUCCESS: {name} -> {out_dff} (COL: {colres['status'] if colres else 'SKIPPED'})")
        summary["success"].append(name)
        reports.append((name, inp, status, validation, rt, val, colres))

    # ---- MTA test resource (DFF + COL)
    mta = find_mta()
    if mta:
        log(f"MTA:SA found at {mta} - runtime test would run in-game (headless not possible here)")
    else:
        log("MTA runtime test unavailable (MTA:SA not installed on this machine) - "
            "writing drop-in test resource instead")
    # the test resource is generated for the LAST successful model of the batch
    last_ok = None
    for name, inp, status, validation, rt, val, colres in reports:
        if status:
            last_ok = (name, colres)
    if last_ok:
        name, colres = last_ok
        make_test_resource(
            name,
            os.path.join(ROOT, "output", name + ".dff"),
            col=os.path.join(ROOT, "output", name + ".col")
                if (colres and colres["status"] == "PASS") else None,
            col_failed_reason=colres.get("reason") if colres else None)
        log(f"Test resource model: {name}")

    # ---- report
    rpt = ["=" * 40, "FBX2MTA CONVERSION REPORT", "=" * 40, ""]
    for name, inp, status, validation, rt, val, colres in reports:
        rpt.append(f"Input: {os.path.basename(inp)}")
        if status:
            o = status.get("original", {})
            pr = status.get("processed", {})
            dff = status.get("dff_actual", {})
            rpt += [
                "", "Original:",
                f"Vertices: {o.get('vertices', 0):,}",
                f"Triangles: {o.get('triangles', 0):,}",
                f"Objects: {o.get('objects', 0)}",
                f"Kind: {status.get('kind', '?')}",
                "", "Processed (mesh):",
                f"Vertices: {pr.get('vertices', 0):,}",
                f"Triangles: {pr.get('triangles', 0):,}",
                f"Objects: {pr.get('objects', 0)}",
                f"Decimated: {pr.get('decimated', False)}",
                "", "DFF content (measured from the DFF file):",
                f"Vertices: {dff.get('vertices', 0):,}",
                f"Triangles: {dff.get('triangles', 0):,}",
                f"Frames: {dff.get('frames', 0)}  (bones in skin: {dff.get('skin_bones', 0)})",
                f"Materials: {dff.get('materials', 0)}",
                f"Textures: {', '.join(dff.get('textures', [])) or 'none'}",
                f"Material effects: {', '.join(dff.get('effects', [])) or 'none'}",
                f"RW version: {dff.get('rw_version', '?')}",
                "",
                f"Orientation: {status.get('orientation', {}).get('label', '?')} "
                f"(upright score {status.get('orientation', {}).get('upright_score', '?')})",
                "",
                "Collision (COL):",
            ]
            if colres:
                if colres["status"] == "PASS":
                    rpt += [
                        f"Status: PASS",
                        f"Quality: {colres.get('quality', '?')} (budget by model size)",
                        f"Original triangles: {colres.get('original_tris', 0):,}",
                        f"Collision triangles: {colres.get('col_tris_in_file') or colres.get('final_tris') or 0:,}",
                        f"Collision vertices: {colres.get('verts', 0):,}",
                        f"File: {name}.col ({colres.get('size_bytes', 0):,} bytes)",
                        "Transform: identical to DFF (same processed mesh, identity world space)",
                    ]
                else:
                    rpt += [f"Status: FAILED ({colres.get('reason', '?')})",
                            "DFF is NOT affected - it remains valid and usable"]
            else:
                rpt.append("Status: SKIPPED")
            rpt += [
                "",
                "DragonFF Export:",
                "SUCCESS" if validation else "FAILED",
                f"Export Version: {status.get('export_version', '?')}",
                f"File: {name}.dff ({status.get('size_bytes', 0):,} bytes)",
                "",
                "DFF Validation:", validation or "FAILED",
                "Round Trip:", rt.get("roundtrip", "FAILED") if rt else "FAILED",
                "",
                "RESULT:",
                "Conversion Complete",
                f"DFF: {validation or 'FAILED'}",
                f"COL: {colres['status'] if colres else 'SKIPPED'}",
                f"Triangles: {pr.get('triangles', 0):,}",
                f"Collision Triangles: {(colres or {}).get('col_tris_in_file') or 0:,}",
                "Output: " + ", ".join(
                    [f"output/{name}.dff"] +
                    ([f"output/{name}.col"] if (colres and colres["status"] == "PASS") else [])),
            ]
            if dff.get("skin_bones", 0) > 32:
                rpt.append(f"NOTE: model has {dff['skin_bones']} bones - above the standard 32-bone SA "
                           "ped skeleton; valid as a standalone skinned model, but if you engineReplaceModel() "
                           "a SA ped, the game will remap to its own skeleton.")
        else:
            rpt += ["", "CONVERSION FAILED"]
        rpt += ["", "=" * 40, ""]

    # batch summary
    ok_n = sum(1 for r in reports if r[2] and r[6] and r[6]["status"] == "PASS")
    rpt += ["BATCH SUMMARY",
            f"success (DFF+COL): {ok_n}",
            f"success (DFF only): {len([r for r in reports if r[2]]) - ok_n}",
            f"failed: {len(summary['failed'])}",
            f"skipped: {len(summary['skipped'])}"]
    for n, e in summary["failed"]:
        rpt.append(f"  FAILED {n}: {e}")
    if not mta:
        rpt.append("MTA runtime test: unavailable (test resource provided in test_resource/)")
    rpt.append("=" * 40)

    rep_path = os.path.join(ROOT, "reports", "model_report.txt")
    open(rep_path, "w").write("\n".join(rpt) + "\n")
    log(f"Report written: {rep_path}")
    log(f"BATCH: success={len(summary['success'])} failed={len(summary['failed'])} skipped={len(summary['skipped'])}")
    for n in summary["success"]:
        log(f"  OK: {n}")
    for n, e in summary["failed"]:
        log(f"  FAILED: {n} ({e})")
    if summary["failed"]:
        sys.exit(1)

if __name__ == "__main__":
    main()
