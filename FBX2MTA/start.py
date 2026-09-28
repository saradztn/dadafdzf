#!/usr/bin/env python3
"""FBX2MTA - entry point.

    python start.py                  # tkinter desktop GUI (default)
    python start.py --web            # browser GUI (http://localhost:8321)
    python start.py --cli            # headless: run the full pipeline on input/*.fbx
    python start.py --cli --file Dragon_2.5.fbx
    python start.py --cli --no-col
    python start.py --cli --col-quality HIGH
    python start.py --cli --col-quality CUSTOM --col-triangles 2500
    python start.py --web --port 9000

The pipeline:  FBX -> Blender(bpy) processing -> DragonFF DFF export (GTA SA
v3.6.0.3) -> DFF validation + round-trip -> real COL collision generation
(DragonFF COL export, COL3) -> COL validation -> output/<model>.dff + .col
+ test_resource/ (MTA:SA drop-in resource).

If the headless Blender venv is missing (environment resets), the pipeline
rebuilds it automatically before running.
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser(description="FBX2MTA - FBX to MTA:SA DFF + COL")
    ap.add_argument("--cli", action="store_true",
                    help="headless mode: run the pipeline directly (no GUI)")
    ap.add_argument("--web", action="store_true",
                    help="use the browser (web) GUI instead of tkinter")
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

    if args.cli:
        # ---- headless CLI mode: same pipeline the GUIs run
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

    if args.web:
        # ---- browser GUI
        sys.path.insert(0, ROOT)
        import webgui
        sys.argv = [sys.argv[0], "--port", str(args.port), "--host", args.host]
        webgui.main()
        return

    # ---- tkinter desktop GUI (default)
    try:
        import tkinter  # noqa: F401
    except Exception:
        print("tkinter is not available on this Python installation.")
        print("Install it (e.g. 'sudo apt install python3-tk' on Linux, or use a "
              "standard python.org build on Windows/macOS which includes it),")
        print("or run with:  python start.py --web")
        sys.exit(1)
    sys.path.insert(0, ROOT)
    import tkgui
    tkgui.main()


if __name__ == "__main__":
    main()
