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


def main():
    budget = "AUTO"
    mode = "both"
    col_enabled = True
    col_quality = "AUTO"
    col_tris = 0
    only_files = []
    if "--budget" in sys.argv:
        budget = sys.argv[sys.argv.index("--budget") + 1]
    if "--mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--mode") + 1].lower()
    if "--no-ifp" in sys.argv and mode == "both":
        mode = "dff"
    if mode not in ("both", "dff", "ifp"):
        log(f"ERROR: unknown --mode {mode!r} (use both|dff|ifp)")
        sys.exit(2)
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

    for _d in ("temp", "output", "logs", "reports"):
        os.makedirs(os.path.join(ROOT, _d), exist_ok=True)
    inputs = sorted(glob.glob(os.path.join(ROOT, "input", "*.fbx")))
    if only_files:
        inputs = [p for p in inputs
                  if os.path.splitext(os.path.basename(p))[0] in only_files]
    log("Starting converter (driver)")
    log(f"Blender backend: headless bpy via {PY} {RUN_BLENDER}")
    if mode == "both":
        log("Mode: BOTH - DFF + COL + IFP (auto - only files with animation)")
    elif mode == "dff":
        log("Mode: DFF only - DFF + COL, no IFP")
    else:
        log("Mode: IFP only - animation export (ANP3), no DFF/COL")
    log("DragonFF: official Parik27/DragonFF addon (Blender 4.2+)")
    if col_enabled and mode in ("both", "dff"):
        if col_quality == "CUSTOM":
            log(f"Collision: enabled (CUSTOM, max {col_tris} tris)")
        elif col_quality == "AUTO":
            log("Collision: enabled (AUTO - budget chosen by model size)")
        else:
            log(f"Collision: enabled (preset {col_quality})")
    else:
        log("Collision: DISABLED " + ("(IFP-only mode)" if mode == "ifp"
                                      else "(--no-col)"))
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
        tmp_ifp = os.path.join(ROOT, "temp", name + ".ifp")
        status_f = os.path.join(ROOT, "temp", name + ".status.json")
        rt_status_f = os.path.join(ROOT, "temp", name + ".rt.json")
        log_f = os.path.join(ROOT, "logs", name + ".log")
        rt_log_f = os.path.join(ROOT, "logs", name + ".rt.log")
        for f in (tmp_dff, tmp_col, tmp_ifp, status_f, rt_status_f):
            if os.path.exists(f): os.remove(f)

        # ---- 1) convert (DFF + IFP + COL in the same Blender session)
        conv_cmd = [PY, RUN_BLENDER, os.path.join(ROOT, "scripts", "convert.py"),
                    "--input", inp, "--output", tmp_dff,
                    "--budget", budget, "--log", log_f, "--status", status_f,
                    "--mode", mode]
        if mode in ("both", "dff") and col_enabled:
            conv_cmd += ["--col", "--col-quality", col_quality,
                         "--col-triangles", str(col_tris),
                         "--col-output", tmp_col]
        if mode == "both":
            conv_cmd += ["--ifp"]
        if mode in ("both", "ifp"):
            conv_cmd += ["--ifp-output", tmp_ifp]
        p = run(conv_cmd)
        need_dff = mode in ("both", "dff")
        if p.returncode != 0 or (need_dff and not os.path.exists(tmp_dff)) \
                or (mode == "ifp" and not os.path.exists(tmp_ifp)):
            log(f"FAILED: {mode.upper()} conversion of {name} (see {log_f})")
            err = "conversion failed"
            if os.path.exists(status_f):
                try:
                    s = json.load(open(status_f))
                    err = "; ".join(s.get("errors", ["conversion failed"])) or err
                    if mode == "ifp":
                        ip = s.get("ifp", {})
                        if not ip.get("success"):
                            err = ip.get("reason") or err
                except Exception: pass
            summary["failed"].append((name, err))
            reports.append((name, inp, None, None, None, None, None, None))
            continue

        status = json.load(open(status_f))
        validation = rt = val = colres = None
        if mode == "ifp":
            log("IFP-only mode: DFF validation / round-trip / COL skipped "
                "(no DFF produced)")
        if need_dff:
            # ---- 2) validate DFF (standalone, DragonFF dff module)
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
                reports.append((name, inp, status, validation, None, val, None, None))
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
                reports.append((name, inp, status, validation, None, val, None, None))
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

        # ---- 4.5) IFP stage (isolated - an IFP problem never fails DFF/COL)
        ifpres = None
        ifp_status = status.get("ifp", {})
        if mode == "dff":
            ifpres = {"status": "SKIPPED",
                      "reason": "mode=dff (no IFP requested)",
                      "anim": None, "frames": 0, "fps": 0.0, "bones": 0,
                      "size_bytes": 0}
        elif mode in ("both", "ifp"):
            if not ifp_status.get("present"):
                if mode == "ifp":
                    ifpres = {"status": "FAILED",
                              "reason": ifp_status.get("reason")
                              or "no animation in FBX",
                              "anim": None, "frames": 0, "fps": 0.0,
                              "bones": 0, "size_bytes": 0}
                    log(f"IFP FAILED for {name}: {ifpres['reason']} "
                        f"(IFP-only mode requires an animated FBX)")
                else:
                    ifpres = {"status": "NONE",
                              "reason": ifp_status.get("reason") or "no animation"}
                    log(f"IFP for {name}: no animation ({ifpres['reason']})")
            elif ifp_status.get("success") and os.path.exists(tmp_ifp):
                # independent validation: re-read the ANP3 structure
                p = run([PY, os.path.join(ROOT, "scripts", "gta_ifp.py"),
                         tmp_ifp])
                ok = p.returncode == 0
                ifpres = {
                    "status": "PASS" if ok else "FAILED",
                    "reason": None if ok else "IFP read-back validation failed",
                    "anim": ifp_status.get("anim"),
                    "frames": ifp_status.get("frames", 0),
                    "fps": ifp_status.get("fps", 0.0),
                    "bones": ifp_status.get("bones", 0),
                    "size_bytes": os.path.getsize(tmp_ifp) if ok else 0,
                    "id_source": ifp_status.get("id_source", "DFF"),
                }
                if ok:
                    ids = (" (bone ids from DFF)"
                           if ifpres["id_source"] == "DFF" else
                           " (bone ids in rig order - match a DFF exported "
                           "from this same rig)")
                    log(f"IFP validated for {name}: {ifpres['bones']} bones, "
                        f"{ifpres['frames']} frames @ {ifpres['fps']:.0f}fps, "
                        f"anim '{ifpres['anim']}'{ids}")
                else:
                    log(f"IFP FAILED for {name}: ANP3 read-back validation "
                        f"failed")
            else:
                ifpres = {"status": "FAILED",
                          "reason": ifp_status.get("reason") or "IFP not generated",
                          "anim": ifp_status.get("anim"), "frames": 0,
                          "fps": 0.0, "bones": 0, "size_bytes": 0}
                log(f"IFP FAILED for {name}: {ifpres['reason']} "
                    f"({'DFF unaffected' if mode == 'both' else 'IFP-only mode'})")

        # ---- 5) finalize
        if mode == "ifp":
            if ifpres and ifpres["status"] == "PASS":
                shutil.copyfile(tmp_ifp, os.path.join(ROOT, "output",
                                                      name + ".ifp"))
                log(f"SUCCESS (IFP only): {name} -> "
                    f"output/{name}.ifp ({ifpres['bones']} bones, "
                    f"{ifpres['frames']} frames @ {ifpres['fps']:.0f}fps, "
                    f"anim '{ifpres.get('anim')}')")
                summary["success"].append(name)
            else:
                reason = (ifpres or {}).get("reason") or "IFP not generated"
                log(f"FAILED: IFP for {name}: {reason}")
                summary["failed"].append((name, f"IFP: {reason}"))
            reports.append((name, inp, status, None, None, None, None,
                            ifpres))
            continue
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
            ifp_txt = ""
            if ifpres and ifpres["status"] == "PASS":
                shutil.copyfile(tmp_ifp, os.path.join(ROOT, "output",
                                                      name + ".ifp"))
                ifp_txt = f" + {name}.ifp"
            log(f"SUCCESS: {name} -> {out_dff} + {out_col}{ifp_txt}")
        else:
            ifp_txt = ""
            if ifpres and ifpres["status"] == "PASS":
                shutil.copyfile(tmp_ifp, os.path.join(ROOT, "output",
                                                      name + ".ifp"))
                ifp_txt = f" + {name}.ifp"
            log(f"SUCCESS: {name} -> {out_dff} (COL: {colres['status'] if colres else 'SKIPPED'}"
                f"{ifp_txt})")
        summary["success"].append(name)
        reports.append((name, inp, status, validation, rt, val, colres, ifpres))

    # ---- MTA test resource (DFF + COL)
    mta = find_mta()
    if mta:
        log(f"MTA:SA found at {mta} - runtime test would run in-game (headless not possible here)")
    else:
        log("MTA runtime test unavailable (MTA:SA not installed on this machine) - "
            "writing drop-in test resource instead")
    # the test resource is generated for the LAST successful model of the
    # batch that has a DFF (a resource needs model.dff; IFP-only runs do not
    # produce one)
    last_ok = None
    for name, inp, status, validation, rt, val, colres, ifpres in reports:
        if status and os.path.exists(os.path.join(ROOT, "output",
                                                  name + ".dff")):
            last_ok = (name, colres, ifpres)
    if last_ok:
        name, colres, ifpres = last_ok
        make_test_resource(
            name,
            os.path.join(ROOT, "output", name + ".dff"),
            col=os.path.join(ROOT, "output", name + ".col")
                if (colres and colres["status"] == "PASS") else None,
            col_failed_reason=colres.get("reason") if colres else None,
            ifp=os.path.join(ROOT, "output", name + ".ifp")
                if (ifpres and ifpres["status"] == "PASS") else None,
            ifp_anim=ifpres.get("anim") if ifpres else None)
        log(f"Test resource model: {name}")
    else:
        log("No DFF in this batch (IFP-only mode?) - MTA test resource not "
            "generated (run with mode=both to get a complete test resource)")

    # ---- report
    rpt = ["=" * 40, "FBX2MTA CONVERSION REPORT", "=" * 40, ""]
    for name, inp, status, validation, rt, val, colres, ifpres in reports:
        rpt.append(f"Input: {os.path.basename(inp)}")
        if status:
            o = status.get("original", {})
            pr = status.get("processed", {})
            dff = status.get("dff_actual", {})
            if status.get("mode") == "ifp":
                # IFP-only run: no DFF/COL sections to report
                ifp_ok = ifpres and ifpres["status"] == "PASS"
                rpt += [
                    "", "Mode: IFP only (no DFF/COL)",
                    "", "Original:",
                    f"Vertices: {o.get('vertices', 0):,}",
                    f"Triangles: {o.get('triangles', 0):,}",
                    f"Objects: {o.get('objects', 0)}",
                    f"Kind: {status.get('kind', '?')}",
                    "", "Animation (IFP):",
                ]
                if ifpres and ifp_ok:
                    rpt += [
                        "Status: PASS (MTA:SA IFP / ANP3)",
                        f"Anim name: {ifpres.get('anim', '?')}",
                        f"Bones: {ifpres.get('bones', 0)}  "
                        f"Frames: {ifpres.get('frames', 0)}  "
                        f"FPS: {ifpres.get('fps', 0):.0f}",
                        f"Bone ids: {ifpres.get('id_source', '?')}",
                        f"File: {name}.ifp ({ifpres.get('size_bytes', 0):,} bytes)",
                        "Load in MTA: engineLoadIFP('model.ifp') + "
                        "setPedAnimation(ped, '" + str(ifpres.get('anim', 'anim')) + "', ...)",
                    ]
                else:
                    rpt += [f"Status: {ifpres['status'] if ifpres else 'FAILED'}"
                            f" ({(ifpres or {}).get('reason', '?')})"]
                rpt += ["",
                        "RESULT:",
                        "IFP Export Complete" if ifp_ok else "IFP EXPORT FAILED",
                        f"IFP: {ifpres['status'] if ifpres else 'FAILED'}",
                        f"Output: output/{name}.ifp" if ifp_ok else "Output: none"]
                rpt += ["", "=" * 40, ""]
                continue
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
            rpt += ["", "Animation (IFP):"]
            if ifpres:
                if ifpres["status"] == "PASS":
                    rpt += [
                        "Status: PASS (MTA:SA IFP / ANP3)",
                        f"Anim name: {ifpres.get('anim', '?')}",
                        f"Bones: {ifpres.get('bones', 0)}  "
                        f"Frames: {ifpres.get('frames', 0)}  "
                        f"FPS: {ifpres.get('fps', 0):.0f}",
                        f"File: {name}.ifp ({ifpres.get('size_bytes', 0):,} bytes)",
                        "Load in MTA: engineLoadIFP('model.ifp') + "
                        "setPedAnimation(ped, '" + str(ifpres.get('anim', 'anim')) + "', ...)",
                    ]
                elif ifpres["status"] == "NONE":
                    rpt += [f"Status: NONE ({ifpres.get('reason', '?')})"]
                elif ifpres["status"] == "SKIPPED":
                    rpt += [f"Status: SKIPPED ({ifpres.get('reason', '?')})"]
                else:
                    rpt += [f"Status: FAILED ({ifpres.get('reason', '?')})",
                            "DFF/COL are NOT affected"]
            else:
                rpt.append("Status: SKIPPED (--no-ifp)")
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
                f"IFP: {ifpres['status'] if ifpres else 'NONE'}",
                f"Triangles: {pr.get('triangles', 0):,}",
                f"Collision Triangles: {(colres or {}).get('col_tris_in_file') or 0:,}",
                "Output: " + ", ".join(
                    [f"output/{name}.dff"] +
                    ([f"output/{name}.col"] if (colres and colres["status"] == "PASS") else []) +
                    ([f"output/{name}.ifp"]
                     if (ifpres and ifpres["status"] == "PASS") else [])),
            ]
            if dff.get("skin_bones", 0) > 32:
                rpt.append(f"NOTE: model has {dff['skin_bones']} bones - above the standard 32-bone SA "
                           "ped skeleton; valid as a standalone skinned model, but if you engineReplaceModel() "
                           "a SA ped, the game will remap to its own skeleton.")
        else:
            rpt += ["", "CONVERSION FAILED"]
        rpt += ["", "=" * 40, ""]

    # batch summary
    ok_n = sum(1 for r in reports if r[2] and r[2].get("mode") != "ifp"
               and r[6] and r[6]["status"] == "PASS")
    dff_n = sum(1 for r in reports if r[2] and r[2].get("mode") != "ifp")
    ifp_n = sum(1 for r in reports if r[2] and r[2].get("mode") == "ifp"
                and r[7] and r[7]["status"] == "PASS")
    rpt += ["BATCH SUMMARY",
            f"success (DFF+COL): {ok_n}",
            f"success (DFF only): {dff_n - ok_n}",
            f"success (IFP only): {ifp_n}",
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
