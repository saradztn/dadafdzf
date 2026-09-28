#!/usr/bin/env python3
"""FBX2MTA - entry point.

    python start.py                  # Web GUI  (http://localhost:8321)
    python start.py --cli            # headless: run the full pipeline on input/*.fbx
    python start.py --cli --file Dragon_2.5.fbx
    python start.py --cli --no-col
    python start.py --cli --col-quality HIGH
    python start.py --cli --col-quality CUSTOM --col-triangles 2500
    python start.py --port 9000      # Web GUI on another port

The pipeline:  FBX -> Blender(bpy) processing -> DragonFF DFF export (GTA SA
v3.6.0.3) -> DFF validation + round-trip -> real COL collision generation
(DragonFF COL export, COL3) -> COL validation -> output/<model>.dff + .col
+ test_resource/ (MTA:SA drop-in resource).
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser(description="FBX2MTA - FBX to MTA:SA DFF + COL")
    ap.add_argument("--cli", action="store_true",
                    help="headless mode: run the pipeline directly (no web GUI)")
    ap.add_argument("--file", default="",
                    help="only convert this input file (name without .fbx or with)")
    ap.add_argument("--no-col", action="store_true",
                    help="skip collision (COL) generation")
    ap.add_argument("--col-quality", default="AUTO",
                    choices=["AUTO", "LOW", "MEDIUM", "HIGH", "CUSTOM"])
    ap.add_argument("--col-triangles", type=int, default=0,
                    help="maximum collision triangles (with CUSTOM)")
    ap.add_argument("--budget", default="AUTO",
                    help="DFF geometry budget: AUTO or max triangles")
    ap.add_argument("--port", type=int, default=8321)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()

    if not args.cli:
        # ---- Web GUI mode (default)
        sys.path.insert(0, ROOT)
        import webgui
        sys.argv = [sys.argv[0], "--port", str(args.port), "--host", args.host]
        webgui.main()
        return

    # ---- headless CLI mode: same pipeline the GUI runs
    cmd = [sys.executable, os.path.join(ROOT, "scripts", "run_pipeline.py"),
           "--budget", args.budget]
    if args.file:
        cmd += ["--files", os.path.splitext(os.path.basename(args.file))[0]]
    if args.no_col:
        cmd += ["--no-col"]
    else:
        cmd += ["--col-quality", args.col_quality]
        if args.col_quality == "CUSTOM" and args.col_triangles:
            cmd += ["--col-triangles", str(args.col_triangles)]
    print("FBX2MTA (CLI mode) - running pipeline...")
    p = subprocess.run(cmd)
    sys.exit(p.returncode)


if __name__ == "__main__":
    main()
