#!/usr/bin/env python3
"""Cross-platform headless Blender (bpy) runner.

Usage (via the system/venv python):
    python blender/run_blender.py <script.py> [args...]

Windows/macOS: just runs the venv python.
Linux: also preloads the X11/GL stub libraries the bpy wheel expects
(see blender/stublibs/ + blender/x11_missing.so).
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IS_WIN = os.name == "nt"
IS_LINUX = (not IS_WIN) and hasattr(os, "uname") and os.uname().sysname == "Linux"
VENV_PY = os.path.join(ROOT, "blender", "venv",
                       "Scripts" if not IS_LINUX else "bin",
                       "python.exe" if IS_WIN else "python")


def main():
    if not os.path.exists(VENV_PY):
        print("[run_blender] bpy venv missing - run blender/setup_env.py "
              "(or press Convert once more: the pipeline rebuilds it "
              "automatically)")
        return 1
    env = dict(os.environ)
    if IS_LINUX:
        env["LD_PRELOAD"] = os.path.join(ROOT, "blender", "x11_missing.so")
        stubs = os.path.join(ROOT, "blender", "stublibs")
        env["LD_LIBRARY_PATH"] = stubs + (
            os.pathsep + env["LD_LIBRARY_PATH"]
            if env.get("LD_LIBRARY_PATH") else "")
    r = subprocess.run([VENV_PY] + sys.argv[1:], env=env)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
