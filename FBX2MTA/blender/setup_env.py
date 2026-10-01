#!/usr/bin/env python3
"""(Re)create the headless Blender (bpy) environment - CROSS-PLATFORM.

Windows / Linux / macOS.  Run manually any time, or let the pipeline run it
automatically when the venv is missing (self-heal).

IMPORTANT: the bpy wheel requires **Python 3.11** (cp311). If the Python
running this script is not 3.11, we search the machine for python3.11.
If none is found, we print exact install instructions.

Note: only the ENGINE (venv) needs Python 3.11 - the GUI/CLI of FBX2MTA run
on any Python 3.9+.
"""
import glob
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # FBX2MTA/
IS_WIN = os.name == "nt"
VENV = os.path.join(ROOT, "blender", "venv")
VENV_PY = os.path.join(VENV, "Scripts" if IS_WIN else "bin",
                       "python.exe" if IS_WIN else "python")
BPY_SPEC = "bpy==4.2.23"


def venv_python_path():
    return VENV_PY


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _verify(venv_py):
    """import bpy inside the venv (Linux needs the X11/GL stub libs)."""
    env = dict(os.environ)
    if not IS_WIN:
        env["LD_PRELOAD"] = os.path.join(ROOT, "blender", "x11_missing.so")
        stubs = os.path.join(ROOT, "blender", "stublibs")
        env["LD_LIBRARY_PATH"] = stubs + (
            os.pathsep + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    r = _run([venv_py, "-c", "import bpy; print(bpy.app.version_string)"],
             env=env, timeout=300)
    return r.returncode == 0, (r.stdout or r.stderr or "").strip()[-400:]


def _find_python311():
    if sys.version_info[:2] == (3, 11):
        return sys.executable
    for name in ("python3.11", "python311"):
        p = shutil.which(name)
        if p:
            return p
    if IS_WIN:
        cands = [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python311\python.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python311*\python.exe"),
            r"C:\Program Files\Python311\python.exe",
            r"C:\Python311\python.exe",
        ]
        for c in cands:
            if os.path.exists(c):
                return c
            for p in sorted(glob.glob(c), reverse=True):
                return p
    for p in ("/usr/bin/python3.11", "/usr/local/bin/python3.11",
              "/opt/homebrew/bin/python3.11",
              os.path.expanduser("~/.pyenv/shims/python3.11")):
        if os.path.exists(p):
            return p
    return None




def main():
    print(f"[setup] FBX2MTA root: {ROOT}")
    if os.path.exists(VENV_PY):
        ok, msg = _verify(VENV_PY)
        if ok:
            print(f"[setup] Blender {msg} ready")
            return 0
        print(f"[setup] existing venv is broken ({msg}) - recreating")
        shutil.rmtree(VENV, ignore_errors=True)

    py = _find_python311()
    if py is None:
        print("[setup] ERROR: the bpy engine wheel needs Python 3.11, "
              "but no Python 3.11 was found on this machine.")
        print("         (FBX2MTA's GUI itself runs fine on your current "
              f"Python {sys.version_info.major}.{sys.version_info.minor} - "
              "only the Blender engine needs 3.11.)")
        print()
        print("         FIX: install Python 3.11, then press Convert again "
              "(it rebuilds automatically):")
        if IS_WIN:
            print("           1) open https://www.python.org/downloads/release/"
                  "python-3119/")
            print("           2) download 'Windows installer (64-bit)' "
                  "(python-3.11.9-amd64.exe)")
            print("           3) run it (keep defaults - it adds python3.11 "
                  "to PATH)")
        else:
            print("           sudo apt install python3.11 python3.11-venv "
                  "(Debian/Ubuntu)")
        return 1

    ver = _run([py, "-c",
                "import sys; print('.'.join(map(str, sys.version_info[:3])))"])
    print(f"[setup] Creating bpy venv with Python "
          f"{ver.stdout.strip() or '?'} ({py}) ... (downloads ~350MB, "
          f"can take a few minutes)")
    r = _run([py, "-m", "venv", VENV])
    if r.returncode != 0:
        print("[setup] ERROR: venv creation failed:")
        print((r.stderr or "")[-2000:])
        return 1
    r = _run([VENV_PY, "-m", "pip", "install", "--quiet", "--no-input",
              "--disable-pip-version-check", BPY_SPEC])
    if r.returncode != 0:
        print("[setup] ERROR: installing bpy failed:")
        print((r.stderr or "")[-2000:])
        return 1
    ok, msg = _verify(VENV_PY)
    if ok:
        print(f"[setup] Blender {msg} ready")
        return 0
    print("[setup] ERROR: bpy installed but import failed:")
    print(msg)
    return 1


if __name__ == "__main__":
    sys.exit(main())
