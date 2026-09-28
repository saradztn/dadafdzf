#!/usr/bin/env python3
"""
FBX2MTA - Pipeline driver (batch)
=================================
Scans input/*.fbx, converts every file:
    FBX -> Blender(bpy) + DragonFF -> DFF (GTA SA v3.6.0.3)
    -> standalone DFF validation (DragonFF gtaLib)
    -> round-trip test (DragonFF import -> Blender)
    -> output/<name>.dff
Writes reports/model_report.txt and appends to logs/converter.log.
Never touches input files.

Usage: python3 run_pipeline.py [--budget AUTO|N]
"""
import os, sys, json, glob, subprocess, time, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # FBX2MTA/
BLENDER = os.path.join(ROOT, "blender", "run_blender.sh")
PY = sys.executable

LOG = open(os.path.join(ROOT, "logs", "converter.log"), "a")
def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.write(line + "\n"); LOG.flush()

def run(cmd, timeout=3600):
    log("CMD: " + " ".join(cmd))
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

def make_mta_test(name, dff):
    d = os.path.join(ROOT, "mta_test")
    os.makedirs(d, exist_ok=True)
    meta = f"""<meta>
    <min_mta_version auth_id="" auth_version="1.5.9"></min_mta_version>
    <info author="FBX2MTA" name="{name} DFF test" type="map" version="1.0" description="Automatic DFF load test"/>
    <script src="client.lua" type="client" cache="false" />
    <file src="model.dff"/>
    <file src="model.txd" />
</meta>
"""
    open(os.path.join(d, "meta.xml"), "w").write(meta)
    open(os.path.join(d, "client.lua"), "w").write(
        "-- Automatic MTA:SA DFF load test (FBX2MTA)\n"
        "local ok, err = engineLoadDFF(0, getRealTime() and 'model.dff' or 'model.dff')\n"
        "if not ok then\n"
        "    print('DFF LOAD FAILED: ' .. tostring(err))\n"
        "else\n"
        "    engineReplaceModel(206, 'model.dff', 'model.txd') -- 206 = adder (test slot)\n"
        "    print('DFF LOADED + MODEL REPLACED OK')\n"
        "end\n"
    )
    dst = os.path.join(d, "model.dff")
    shutil.copyfile(dff, dst)
    log(f"MTA test resource written to {d} (drop into MTASA/resources/{name}_test)")
    return d

def main():
    budget = "AUTO"
    if "--budget" in sys.argv:
        budget = sys.argv[sys.argv.index("--budget") + 1]

    inputs = sorted(glob.glob(os.path.join(ROOT, "input", "*.fbx")))
    log("Starting converter (driver)")
    log(f"Blender backend: headless bpy via {BLENDER}")
    log("DragonFF: official Parik27/DragonFF addon (Blender 4.2+)")
    if not inputs:
        log("ERROR: no .fbx files found in input/")
        return

    summary = {"success": [], "failed": [], "skipped": []}
    reports = []

    for inp in inputs:
        name = os.path.splitext(os.path.basename(inp))[0].replace(" ", "_")
        log("=" * 60)
        log(f"Processing: {os.path.basename(inp)}")

        out_dff = os.path.join(ROOT, "output", name + ".dff")
        tmp_dff = os.path.join(ROOT, "temp", name + ".dff")
        status_f = os.path.join(ROOT, "temp", name + ".status.json")
        rt_status_f = os.path.join(ROOT, "temp", name + ".rt.json")
        log_f = os.path.join(ROOT, "logs", name + ".log")
        rt_log_f = os.path.join(ROOT, "logs", name + ".rt.log")
        for f in (tmp_dff, status_f, rt_status_f):
            if os.path.exists(f): os.remove(f)

        # ---- 1) convert
        p = run([BLENDER, os.path.join(ROOT, "scripts", "convert.py"),
                 "--input", inp, "--output", tmp_dff,
                 "--budget", budget, "--log", log_f, "--status", status_f])
        if p.returncode != 0 or not os.path.exists(tmp_dff):
            log(f"FAILED: conversion of {name} (see {log_f})")
            err = "conversion failed"
            if os.path.exists(status_f):
                try:
                    s = json.load(open(status_f))
                    err = "; ".join(s.get("errors", ["conversion failed"])) or err
                except Exception: pass
            summary["failed"].append((name, err))
            reports.append((name, inp, None, None, None, None))
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
            reports.append((name, inp, status, None, None, val))
            continue

        # ---- 3) round-trip test
        p = run([BLENDER, os.path.join(ROOT, "scripts", "roundtrip.py"),
                 "--dff", tmp_dff, "--status", status_f,
                 "--rt-status", rt_status_f, "--log", rt_log_f])
        rt = json.load(open(rt_status_f)) if os.path.exists(rt_status_f) else {}
        rt_ok = rt.get("roundtrip") == "PASS"
        if not rt_ok:
            log(f"FAILED: round-trip test for {name}: {rt.get('reason')}")
            summary["failed"].append((name, f"round-trip: {rt.get('reason')}"))
            reports.append((name, inp, status, validation, None, val))
            continue

        # ---- 4) finalize
        shutil.copyfile(tmp_dff, out_dff)
        summary["success"].append(name)
        log(f"SUCCESS: {name} -> {out_dff}")
        reports.append((name, inp, status, validation, rt, val))

    # ---- MTA test
    mta = find_mta()
    if mta:
        log(f"MTA:SA found at {mta} - runtime test would run in-game (headless not possible here)")
    else:
        log("MTA runtime test unavailable (MTA:SA not installed on this machine) - "
            "writing drop-in test resource instead")
    for name, inp, status, validation, rt, val in reports:
        if status:
            make_mta_test(name, os.path.join(ROOT, "output", name + ".dff"))

    # ---- report
    rpt = ["=" * 40, "FBX2MTA CONVERSION REPORT", "=" * 40, ""]
    for name, inp, status, validation, rt, val in reports:
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
                "DragonFF Export:",
                "SUCCESS" if validation else "FAILED",
                f"Export Version: {status.get('export_version', '?')}",
                f"File: {name}.dff ({status.get('size_bytes', 0):,} bytes)",
                "",
                "DFF Validation:", validation or "FAILED",
                "Round Trip:", rt.get("roundtrip", "FAILED") if rt else "FAILED",
            ]
            if dff.get("skin_bones", 0) > 32:
                rpt.append(f"NOTE: model has {dff['skin_bones']} bones - above the standard 32-bone SA "
                           "ped skeleton; valid as a standalone skinned model, but if you engineReplaceModel() "
                           "a SA ped, the game will remap to its own skeleton.")
        else:
            rpt += ["", "CONVERSION FAILED"]
        rpt += ["", "Output:", f"output/{name}.dff" if status else "(none)", "=" * 40, ""]

    # batch summary
    rpt += ["BATCH SUMMARY",
            f"success: {len(summary['success'])}",
            f"failed: {len(summary['failed'])}",
            f"skipped: {len(summary['skipped'])}"]
    for n, e in summary["failed"]:
        rpt.append(f"  FAILED {n}: {e}")
    if not mta:
        rpt.append("MTA runtime test: unavailable (test resource provided in mta_test/)")
    rpt.append("=" * 40)

    rep_path = os.path.join(ROOT, "reports", "model_report.txt")
    open(rep_path, "w").write("\n".join(rpt) + "\n")
    log(f"Report written: {rep_path}")
    log(f"BATCH: success={len(summary['success'])} failed={len(summary['failed'])} skipped={len(summary['skipped'])}")
    for n in summary["success"]:
        log(f"  OK: {n}")
    for n, e in summary["failed"]:
        log(f"  FAILED: {n} ({e})")

if __name__ == "__main__":
    main()
