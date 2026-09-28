#!/usr/bin/env python3
"""FBX2MTA shared job/results logic (used by the tkinter GUI and the web GUI).

Headless-testable: no GUI imports here.
"""
import glob
import json
import os
import subprocess
import threading
import time
from collections import deque

ROOT = os.path.dirname(os.path.abspath(__file__))  # FBX2MTA/ (joblib.py sits at project root)
INPUT_DIR = os.path.join(ROOT, "input")
OUTPUT_DIR = os.path.join(ROOT, "output")
TEMP_DIR = os.path.join(ROOT, "temp")


class JobState:
    """Thread-safe state for background jobs (log stream + busy flag)."""

    def __init__(self, log_lines=800):
        self.lock = threading.Lock()
        self.lines = deque(maxlen=log_lines)
        self.busy = False
        self.job = None
        self.last_job_ok = None
        self.last_run_at = None

    def log(self, line):
        with self.lock:
            self.lines.append(f"[{time.strftime('%H:%M:%S')}] {line}")

    def snapshot_log(self, n=300):
        with self.lock:
            return list(self.lines)[-n:]

    def run(self, cmd, desc):
        """Run a subprocess job in a background thread. Returns True if started."""
        with self.lock:
            if self.busy:
                return False
            self.busy = True
            self.job = desc
        self.log(f"--- job: {desc} ---")

        def worker():
            ok = None
            try:
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True)
                for line in p.stdout:
                    line = line.rstrip("\n")
                    if line:
                        self.log(line)
                p.wait()
                ok = (p.returncode == 0)
                self.log(f"--- job finished ({desc}): {'OK' if ok else 'FAILED'} ---")
            except Exception as e:
                ok = False
                self.log(f"job error: {e}")
            with self.lock:
                self.busy = False
                self.job = None
                self.last_job_ok = ok
                self.last_run_at = time.time()

        threading.Thread(target=worker, daemon=True).start()
        return True


def list_input_files():
    out = []
    for p in sorted(glob.glob(os.path.join(INPUT_DIR, "*.fbx"))):
        out.append({"name": os.path.basename(p),
                    "size": os.path.getsize(p),
                    "mtime": int(os.path.getmtime(p))})
    return out


def latest_results():
    """Per-file results from the last pipeline run.

    Prefers temp/<name>.status.json + temp/<name>.col.validation.json; falls
    back to output/ artifacts (which only exist after validation passed).
    """
    results = []
    for f in list_input_files():
        name = os.path.splitext(f["name"])[0]
        res = {"name": name, "dff": "MISSING", "col": "MISSING",
               "triangles": None, "col_triangles": None,
               "col_reason": None, "outputs": []}
        status_f = os.path.join(TEMP_DIR, name + ".status.json")
        out_dff = os.path.join(OUTPUT_DIR, name + ".dff")
        out_col = os.path.join(OUTPUT_DIR, name + ".col")
        if os.path.exists(status_f):
            try:
                s = json.load(open(status_f))
                if s.get("success"):
                    res["dff"] = "PASS"
                    res["triangles"] = s.get("processed", {}).get("triangles")
                    col = s.get("col", {})
                    if col.get("enabled"):
                        res["col"] = "PASS" if col.get("success") else "FAILED"
                        res["col_reason"] = col.get("reason")
                    else:
                        res["col"] = "SKIPPED"
            except Exception:
                pass
        # fallback (e.g. temp/ was reset): output files only exist after the
        # pipeline validated them, so presence implies a passing conversion
        if res["dff"] == "MISSING" and os.path.exists(out_dff):
            res["dff"] = "PASS"
        if res["col"] in ("MISSING", "FAILED") and not os.path.exists(
                os.path.join(TEMP_DIR, name + ".col.validation.json")) \
                and os.path.exists(out_col):
            res["col"] = "PASS"
        if res.get("triangles") is None and os.path.exists(out_dff + ".validation.json"):
            try:
                res["triangles"] = json.load(
                    open(out_dff + ".validation.json")).get("info", {}).get("triangles")
            except Exception:
                pass
        colval_f = os.path.join(TEMP_DIR, name + ".col.validation.json")
        if not os.path.exists(colval_f) and os.path.exists(out_col + ".validation.json"):
            colval_f = out_col + ".validation.json"
        if os.path.exists(colval_f):
            try:
                cv = json.load(open(colval_f))
                if cv.get("valid"):
                    res["col"] = "PASS"
                    res["col_triangles"] = cv.get("info", {}).get("mesh_triangles")
                    res["col_verts"] = cv.get("info", {}).get("mesh_vertices")
                else:
                    res["col"] = "FAILED"
                    res["col_reason"] = "; ".join(cv.get("errors", []))
            except Exception:
                pass
        for ext in (".dff", ".col"):
            p = os.path.join(OUTPUT_DIR, name + ext)
            if os.path.exists(p):
                res["outputs"].append({"file": f"output/{name}{ext}",
                                       "bytes": os.path.getsize(p)})
        results.append(res)
    return results


def upload_fbx(src_path):
    """Copy a user-chosen FBX into input/ (sanitized name). Returns (ok, msg)."""
    import re
    fname = os.path.basename(src_path)
    fname = re.sub(r"[^A-Za-z0-9._-]", "_", fname)[:120] or "model.fbx"
    if not fname.lower().endswith(".fbx"):
        return False, "only .fbx files are accepted (got " + fname + ")"
    if not os.path.isfile(src_path) or os.path.getsize(src_path) == 0:
        return False, "file not found or empty: " + src_path
    os.makedirs(INPUT_DIR, exist_ok=True)
    shutil_copy = __import__("shutil").copyfile
    shutil_copy(src_path, os.path.join(INPUT_DIR, fname))
    return True, fname


def open_in_file_manager(path):
    """Open a folder/file in the OS file manager (best effort)."""
    import subprocess as sp
    try:
        if os.name == "nt":
            sp.Popen(["explorer", path])
        elif os.uname().sysname == "Darwin":
            sp.Popen(["open", path])
        else:
            sp.Popen(["xdg-open", path])
        return True
    except Exception:
        return False
