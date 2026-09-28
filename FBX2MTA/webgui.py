#!/usr/bin/env python3
"""FBX2MTA Web GUI (zero external dependencies - stdlib only).

Serves a single-page web UI on http://<host>:<port> that wraps the same
pipeline the CLI uses (scripts/run_pipeline.py + Blender/bpy + DragonFF):

  * file list of input/*.fbx  +  "Generate Test FBX" (FBX generation feature)
  * COLLISION section:  [x] Generate COL,  quality AUTO/LOW/MEDIUM/HIGH/CUSTOM,
    maximum collision triangles,  quick presets 500/1000/2000/3000/5000/10000
  * Convert  ->  FBX -> DFF (DragonFF) + COL (DragonFF COL export) -> validation
  * "Generate MTA Test Resource"  ->  test_resource/ (meta.xml, client.lua,
    model.dff, model.col)
  * result panel:  Conversion Complete / DFF: PASS / COL: PASS /
    Triangles / Collision Triangles / outputs
  * live log tail

Usage:
    python webgui.py [--port 8321] [--host 0.0.0.0]
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))          # FBX2MTA/
PY = sys.executable
PIPELINE = os.path.join(ROOT, "scripts", "run_pipeline.py")
BLENDER = os.path.join(ROOT, "blender", "run_blender.sh")
GEN_FBX = os.path.join(ROOT, "scripts", "generate_test_fbx.py")
RESOURCE = os.path.join(ROOT, "scripts", "mta_resource.py")

INPUT_DIR = os.path.join(ROOT, "input")
OUTPUT_DIR = os.path.join(ROOT, "output")
TEMP_DIR = os.path.join(ROOT, "temp")

STATE = {
    "busy": False,
    "job": None,                 # description of the running job
    "log": deque(maxlen=800),
    "last_job_ok": None,
    "last_run_at": None,
}
LOCK = threading.Lock()


def state_log(line):
    with LOCK:
        STATE["log"].append(f"[{time.strftime('%H:%M:%S')}] {line}")


def list_input_files():
    out = []
    for p in sorted(glob.glob(os.path.join(INPUT_DIR, "*.fbx"))):
        out.append({"name": os.path.basename(p),
                    "size": os.path.getsize(p),
                    "mtime": int(os.path.getmtime(p))})
    return out


def latest_results():
    """Per-file results from the last pipeline run (temp status + validation)."""
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


def run_job(cmd, desc):
    """Run a subprocess job in a background thread, streaming its output."""
    with LOCK:
        if STATE["busy"]:
            return False
        STATE["busy"] = True
        STATE["job"] = desc
    state_log(f"--- job: {desc} ---")

    def worker():
        ok = None
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
            for line in p.stdout:
                line = line.rstrip("\n")
                if line:
                    state_log(line)
            p.wait()
            ok = (p.returncode == 0)
            state_log(f"--- job finished ({desc}): "
                      f"{'OK' if ok else 'FAILED'} ---")
        except Exception as e:
            ok = False
            state_log(f"job error: {e}")
        with LOCK:
            STATE["busy"] = False
            STATE["job"] = None
            STATE["last_job_ok"] = ok
            STATE["last_run_at"] = time.time()
    threading.Thread(target=worker, daemon=True).start()
    return True


def start_convert(files, col, quality, col_tris):
    cmd = [PY, PIPELINE, "--budget", "AUTO"]
    if files:
        cmd += ["--files"] + files
    if col:
        cmd += ["--col-quality", quality]
        if quality == "CUSTOM" and col_tris:
            cmd += ["--col-triangles", str(int(col_tris))]
    else:
        cmd += ["--no-col"]
    return run_job(cmd, f"convert ({len(files) or 'all'} files, "
                        f"COL {'AUTO' if quality == 'AUTO' else quality})")


def start_generate_fbx():
    out = os.path.join(INPUT_DIR, "generated_test.fbx")
    cmd = [BLENDER, GEN_FBX, "--output", out,
           "--log", os.path.join(ROOT, "logs", "test_fbx.log")]
    return run_job(cmd, "generate test FBX")


def start_resource():
    """Build the test resource for the last successful model."""
    results = latest_results()
    cands = [r for r in results if r["dff"] == "PASS"]
    if not cands:
        state_log("resource: no successful DFF found - run a conversion first")
        return False
    r = cands[-1]
    name = r["name"]
    cmd = [PY, RESOURCE, "--name", name,
           "--dff", os.path.join(OUTPUT_DIR, name + ".dff")]
    if r["col"] == "PASS":
        cmd += ["--col", os.path.join(OUTPUT_DIR, name + ".col")]
    else:
        cmd += ["--col-failed-reason", (r.get("col_reason") or r["col"])]
    return run_job(cmd, f"MTA test resource for {name}")


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FBX2MTA - DFF + COL converter</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
         background:#101418; color:#dfe7ee; }
  header { padding:18px 24px; background:#161d24; border-bottom:1px solid #26313c;
           display:flex; align-items:baseline; gap:14px; }
  header h1 { margin:0; font-size:19px; letter-spacing:.4px; }
  header .sub { color:#7f8ea0; font-size:12.5px; }
  main { padding:20px 24px; max-width:1050px; margin:0 auto; }
  section { background:#161d24; border:1px solid #26313c; border-radius:10px;
            padding:16px 18px; margin-bottom:16px; }
  h2 { margin:0 0 12px; font-size:14px; text-transform:uppercase;
       letter-spacing:1.2px; color:#8fd0ff; }
  label { display:inline-flex; gap:7px; align-items:center; cursor:pointer; }
  .row { display:flex; flex-wrap:wrap; gap:18px; align-items:center; margin:8px 0; }
  .col-section .row { margin:10px 0; }
  input[type=number] { width:110px; background:#0d1116; color:#dfe7ee;
     border:1px solid #33414f; border-radius:6px; padding:6px 8px; }
  select { background:#0d1116; color:#dfe7ee; border:1px solid #33414f;
     border-radius:6px; padding:6px 8px; }
  .preset { background:#0d1116; color:#9fc2e0; border:1px solid #33414f;
     border-radius:6px; padding:5px 11px; cursor:pointer; font-size:12.5px; }
  .preset.active { background:#1d4ed8; border-color:#1d4ed8; color:#fff; }
  .files { display:flex; flex-direction:column; gap:7px; margin:10px 0; }
  .file { display:flex; align-items:center; gap:10px; background:#0d1116;
     border:1px solid #26313c; border-radius:8px; padding:8px 12px; }
  .file .size { color:#7f8ea0; font-size:12px; margin-left:auto; }
  button.action { background:#1d4ed8; color:#fff; border:0; border-radius:8px;
     padding:10px 18px; font-size:14px; cursor:pointer; font-weight:600; }
  button.action:hover { background:#2563eb; }
  button.action:disabled { background:#33414f; color:#8a99ab; cursor:not-allowed; }
  button.ghost { background:transparent; color:#9fc2e0; border:1px solid #33414f;
     border-radius:8px; padding:9px 15px; cursor:pointer; }
  button.ghost:disabled { color:#5b6b7d; cursor:not-allowed; }
  pre.log { background:#0a0e12; border:1px solid #26313c; border-radius:8px;
     padding:12px; height:260px; overflow:auto; font:12px/1.5 ui-monospace,monospace;
     color:#a8b6c5; white-space:pre-wrap; }
  table.res { width:100%; border-collapse:collapse; margin-top:10px; }
  table.res th, table.res td { text-align:left; padding:7px 9px;
     border-bottom:1px solid #26313c; font-size:13px; }
  table.res th { color:#7f8ea0; font-weight:600; font-size:11.5px;
     text-transform:uppercase; letter-spacing:.8px; }
  .pass { color:#4ade80; font-weight:700; }
  .fail { color:#f87171; font-weight:700; }
  .skip { color:#facc15; font-weight:700; }
  .missing { color:#64748b; }
  .banner { border-radius:8px; padding:12px 14px; margin-bottom:12px; font-weight:600; }
  .banner.ok { background:#12351f; color:#4ade80; border:1px solid #1f5c33; }
  .banner.bad { background:#3a1518; color:#f87171; border:1px solid #6b2630; }
  .banner.idle { background:#1a2430; color:#9fc2e0; border:1px solid #2c3e52; }
  .busy { color:#facc15; font-weight:600; }
  .hint { color:#7f8ea0; font-size:12px; }
</style>
</head>
<body>
<header>
  <h1>FBX2MTA</h1>
  <span class="sub">FBX &rarr; Blender + DragonFF &rarr; MTA:SA DFF + COL &nbsp;|&nbsp; <span id="busyState"></span></span>
</header>
<main>

  <section>
    <h2>1 &middot; Input files (input/)</h2>
    <div class="files" id="files"></div>
    <div class="row">
      <input type="file" id="fbxPick" accept=".fbx,.FBX" style="display:none" onchange="uploadFbx(this)">
      <button class="action" id="uploadBtn" onclick="document.getElementById('fbxPick').click()">&#8682; Choose FBX file &hellip;</button>
      <button class="ghost" id="genFbx" onclick="api('generate-fbx')">+ Generate Test FBX</button>
      <span class="hint">Choose FBX: pick any .fbx from your computer &rarr; uploaded to input/ &rarr; then Convert it</span>
    </div>
  </section>

  <section class="col-section">
    <h2>2 &middot; Collision (COL)</h2>
    <div class="row">
      <label><input type="checkbox" id="genCol" checked> Generate COL (real collision geometry)</label>
    </div>
    <div class="row">
      <label>Quality
        <select id="quality" onchange="onQuality()">
          <option value="AUTO" selected>AUTO (by model size)</option>
          <option value="LOW">LOW (~1,000 tris)</option>
          <option value="MEDIUM">MEDIUM (~3,000 tris)</option>
          <option value="HIGH">HIGH (~10,000 tris)</option>
          <option value="CUSTOM">CUSTOM (max triangles below)</option>
        </select>
      </label>
      <label id="colTrisWrap" style="display:none">Maximum Collision Triangles
        <input type="number" id="colTris" value="3000" min="100" max="100000" step="100">
      </label>
    </div>
    <div class="row" id="presets">
      <span class="hint">Quick presets:</span>
      <button class="preset" data-n="500">500</button>
      <button class="preset" data-n="1000">1000</button>
      <button class="preset" data-n="2000">2000</button>
      <button class="preset" data-n="3000">3000</button>
      <button class="preset" data-n="5000">5000</button>
      <button class="preset" data-n="10000">10000</button>
    </div>
    <div class="hint">AUTO: small model &rarr; minimal reduction &middot; medium &rarr; ~3,000 &middot; large &rarr; ~5,000
      collision triangles (keeps outer silhouette / floors / walls, removes interior detail).</div>
  </section>

  <section>
    <h2>3 &middot; Convert</h2>
    <div class="row">
      <button class="action" id="convertBtn" onclick="doConvert()">Convert selected &rarr; DFF + COL</button>
      <button class="ghost" id="resBtn" onclick="api('resource')">Generate MTA Test Resource</button>
    </div>
    <div class="hint">Pipeline: FBX &rarr; Blender processing &rarr; DragonFF DFF export (GTA SA v3.6.0.3)
      &rarr; DFF validation + round-trip &rarr; DragonFF COL export (COL3) &rarr; COL validation
      &rarr; output/&lt;model&gt;.dff + output/&lt;model&gt;.col. COL failure never fails the DFF.</div>
  </section>

  <section>
    <h2>4 &middot; Results</h2>
    <div id="banner" class="banner idle">No conversion yet</div>
    <table class="res" id="resTable">
      <thead><tr><th>Model</th><th>DFF</th><th>COL</th><th>Triangles</th>
        <th>Collision Triangles</th><th>Outputs</th></tr></thead>
      <tbody></tbody>
    </table>
  </section>

  <section>
    <h2>5 &middot; Log</h2>
    <pre class="log" id="log"></pre>
  </section>

</main>
<script>
const $ = s => document.querySelector(s);
let selected = new Set();

function onQuality(){
  $("#colTrisWrap").style.display = ($("#quality").value === "CUSTOM") ? "" : "none";
  document.querySelectorAll(".preset").forEach(b => b.classList.remove("active"));
}
document.querySelectorAll(".preset").forEach(b => b.onclick = () => {
  $("#quality").value = "CUSTOM";
  $("#colTris").value = b.dataset.n;
  onQuality();
  document.querySelectorAll(".preset").forEach(x => x.classList.toggle("active", x === b));
});

function esc(s){ return String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

function renderFiles(files){
  const box = $("#files"); box.innerHTML = "";
  if (!files.length) { box.innerHTML = "<span class='hint'>no .fbx files in input/ yet</span>"; return; }
  for (const f of files){
    const name = f.name.replace(/\.fbx$/i, "");
    if (!selected.has(name)) selected.add(name);
    const div = document.createElement("div");
    div.className = "file";
    div.innerHTML = `<label><input type="checkbox" checked data-name="${esc(name)}">
      <b>${esc(f.name)}</b></label><span class="size">${(f.size/1024).toFixed(1)} KB</span>`;
    box.appendChild(div);
  }
}

function statusClass(v){
  return v === "PASS" ? "pass" : v === "FAILED" ? "fail" : v === "SKIPPED" ? "skip" : "missing";
}

function renderResults(data){
  const rows = data.results || [];
  const tb = $("#resTable tbody"); tb.innerHTML = "";
  let allOk = true, any = false;
  for (const r of rows){
    any = true;
    const dffOk = r.dff === "PASS";
    const colOk = r.col === "PASS";
    if (dffOk && (r.col === "PASS" || r.col === "SKIPPED")) {} else allOk = false;
    const outs = (r.outputs || []).map(o =>
      `<a href="/api/file?path=${encodeURIComponent(o.file)}" style="color:#8fd0ff">${esc(o.file)}</a>
       <span class="hint">(${(o.bytes/1024).toFixed(1)} KB)</span>`).join("<br>");
    const reason = r.col === "FAILED" ? ` <span class="hint">${esc(r.col_reason||"")}</span>` : "";
    tb.innerHTML += `<tr>
      <td>${esc(r.name)}</td>
      <td class="${statusClass(r.dff)}">${r.dff}</td>
      <td class="${statusClass(r.col)}">${r.col}${reason}</td>
      <td>${r.triangles != null ? r.triangles.toLocaleString() : "-"}</td>
      <td>${r.col_triangles != null ? r.col_triangles.toLocaleString() : "-"}</td>
      <td class="hint">${outs || "-"}</td></tr>`;
  }
  const b = $("#banner");
  if (data.busy){ b.className = "banner idle"; b.innerHTML = "Working... " + esc(data.job||""); }
  else if (any){
    if (allOk){
      const last = rows[rows.length-1];
      b.className = "banner ok";
      b.innerHTML = "Conversion Complete<br><span style='font-weight:400;font-size:12.5px'>
        DFF: PASS &nbsp;|&nbsp; COL: " + (last.col === "SKIPPED" ? "SKIPPED" : "PASS") +
        " &nbsp;|&nbsp; Triangles: " + (last.triangles||0).toLocaleString() +
        " &nbsp;|&nbsp; Collision Triangles: " + (last.col_triangles||0).toLocaleString() +
        "<br>Outputs: " + (last.outputs||[]).map(o=>esc(o.file)).join(", ") + "</span>";
    } else {
      b.className = "banner bad";
      const bad = rows.find(r => r.dff !== "PASS" || r.col === "FAILED");
      b.innerHTML = "Conversion finished with errors &mdash; " + esc(bad.name) +
        (bad.col === "FAILED" ? ": COL FAILED (" + esc(bad.col_reason||"see log") + ")" : " DFF failed - see log");
    }
  } else { b.className = "banner idle"; b.innerHTML = data.busy ? "Working..." : "No conversion yet"; }
  $("#busyState").innerHTML = data.busy ? '<span class="busy">RUNNING: ' + esc(data.job||"") + '</span>'
                                        : (data.last_run_at ? "idle" : "");
  $("#convertBtn").disabled = data.busy;
  $("#genFbx").disabled = data.busy;
  $("#resBtn").disabled = data.busy;
  const lg = $("#log");
  const tail = (data.log || []).slice(-300).join("\n");
  if (lg.textContent !== tail){ lg.textContent = tail; lg.scrollTop = lg.scrollHeight; }
}

async function api(name, body){
  await fetch("/api/" + name, {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(body || {})});
  refresh();
}

let uploadMsg = {ok: null, text: ""};
async function uploadFbx(inp){
  const f = inp.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  try {
    const r = await fetch("/api/upload", {method: "POST", body: fd});
    const d = await r.json();
    if (d.ok){
      uploadMsg = {ok: true, text: "Uploaded " + d.saved + " (" + (d.bytes/1024).toFixed(1) + " KB) to input/ - it is selected below"};
      stateLogLocal("Upload OK: " + d.saved);
    } else {
      uploadMsg = {ok: false, text: "Upload failed: " + (d.error || "unknown error")};
      stateLogLocal("Upload FAILED: " + (d.error || "unknown error"));
    }
  } catch(e){
    uploadMsg = {ok: false, text: "Upload failed: cannot reach backend - restart python start.py"};
  }
  inp.value = "";
  refresh();
}
function stateLogLocal(msg){
  const lg = $("#log");
  lg.textContent += "\n[" + new Date().toTimeString().slice(0,8) + "] " + msg;
  lg.scrollTop = lg.scrollHeight;
}

function doConvert(){
  const files = [...document.querySelectorAll("#files input:checked")].map(i => i.dataset.name);
  api("convert", {files, col: $("#genCol").checked, quality: $("#quality").value,
                  col_tris: parseInt($("#colTris").value || "0", 10)});
}

async function refresh(){
  try {
    const r = await fetch("/api/state");
    const d = await r.json();
    const b = $("#banner");
    if (b) delete b.dataset.offline;
    renderFiles(d.files);
    renderResults(d);
  } catch(e){
    const b = $("#banner");
    if (b && !b.dataset.offline){
      b.dataset.offline = "1";
      b.className = "banner bad";
      b.innerHTML = "Cannot reach the converter backend &mdash; restart the server with "
        + "<code>python start.py</code>. This page updates automatically.";
    }
  }
}

refresh();
setInterval(refresh, 1500);
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep the console clean

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            data = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/api/state":
            with LOCK:
                log_tail = list(STATE["log"])[-300:]
                busy, job = STATE["busy"], STATE["job"]
            self._json({
                "busy": busy, "job": job, "log": log_tail,
                "last_run_at": STATE.get("last_run_at"),
                "files": list_input_files(),
                "results": latest_results(),
            })
        elif self.path.startswith("/api/file"):
            self._serve_file()
        else:
            self._json({"error": "not found"}, 404)

    MAX_UPLOAD = 1024 * 1024 * 1024  # 1 GB

    def _handle_upload(self, raw):
        import re
        ct = self.headers.get("Content-Type", "")
        m = re.search(r"boundary=([^;]+)", ct)
        if not m:
            self._json({"ok": False, "error": "multipart/form-data expected"}, 400)
            return
        boundary = m.group(1).strip('"').encode()
        # split multipart body on the boundary
        parts = raw.split(b"--" + boundary)
        saved = None
        for part in parts:
            if b"filename=" not in part:
                continue
            head, sep, data = part.partition(b"\r\n\r\n")
            if not sep:
                continue
            if data.endswith(b"\r\n"):
                data = data[:-2]  # strip the CRLF separator after the payload
            fm = re.search(rb'filename="([^"]+)"', head)
            fname = os.path.basename(fm.group(1).decode("utf-8", "replace")) if fm else ""
            fname = re.sub(r"[^A-Za-z0-9._-]", "_", fname)[:120] or "model.fbx"
            if not fname.lower().endswith(".fbx"):
                self._json({"ok": False,
                            "error": "only .fbx files are accepted (got " + fname + ")"}, 400)
                return
            if len(data) == 0:
                self._json({"ok": False, "error": "empty file"}, 400)
                return
            if len(data) > self.MAX_UPLOAD:
                self._json({"ok": False, "error": "file too large (max 1 GB)"}, 400)
                return
            dest = os.path.join(INPUT_DIR, fname)
            with open(dest, "wb") as f:
                f.write(data)
            saved = {"saved": fname, "bytes": len(data)}
            state_log(f"Uploaded FBX: {fname} ({len(data):,} bytes) -> input/{fname}")
            break
        if saved is None:
            self._json({"ok": False, "error": "no file field found in upload"}, 400)
        else:
            self._json({"ok": True, **saved})

    def _serve_file(self):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        rel = (qs.get("path") or [""])[0].replace("\\", "/")
        p = os.path.normpath(os.path.join(ROOT, rel))
        if not (p.startswith(ROOT + os.sep) and os.path.isfile(p)):
            self._json({"error": "forbidden"}, 403)
            return
        data = open(p, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition",
                         f'attachment; filename="{os.path.basename(p)}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n)
            if self.path == "/api/upload":
                self._handle_upload(raw)
                return
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        if self.path == "/api/convert":
            started = start_convert(body.get("files") or [],
                                    bool(body.get("col", True)),
                                    str(body.get("quality") or "AUTO").upper(),
                                    body.get("col_tris") or 0)
            self._json({"started": started})
        elif self.path == "/api/generate-fbx":
            self._json({"started": start_generate_fbx()})
        elif self.path == "/api/resource":
            self._json({"started": start_resource()})
        else:
            self._json({"error": "not found"}, 404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8321)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"FBX2MTA GUI: http://localhost:{args.port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
