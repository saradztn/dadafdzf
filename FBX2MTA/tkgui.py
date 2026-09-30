#!/usr/bin/env python3
"""FBX2MTA - tkinter desktop GUI (the default UI).

No browser, no server:  python start.py  opens this window directly.

  1. input files (input/*.fbx)  +  [Choose FBX file...] (pick any .fbx from
     your computer - it is copied to input/)  +  [Generate Test FBX]
  2. COLLISION: [x] Generate COL, quality AUTO/LOW/MEDIUM/HIGH/CUSTOM,
     maximum collision triangles, quick presets 500/1000/2000/3000/5000/10000
  3. Convert selected -> DFF + COL   |   Generate MTA Test Resource
  4. results: Conversion Complete / DFF: PASS / COL: PASS / Triangles /
     Collision Triangles / outputs
  5. live log

The actual work runs in a background thread (Blender/bpy + DragonFF pipeline,
exactly the same one the CLI uses); this window only shows state.
"""
import os
import subprocess
import sys
import threading
import time

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

ROOT = os.path.dirname(os.path.abspath(__file__))          # FBX2MTA/
sys.path.insert(0, ROOT)
import joblib  # noqa: E402

PY = sys.executable
PIPELINE = os.path.join(ROOT, "scripts", "run_pipeline.py")
RUN_BLENDER = os.path.join(ROOT, "blender", "run_blender.py")  # cross-platform
GEN_FBX = os.path.join(ROOT, "scripts", "generate_test_fbx.py")
RESOURCE = os.path.join(ROOT, "scripts", "mta_resource.py")

STATE = joblib.JobState()
PRESETS = (500, 1000, 2000, 3000, 5000, 10000)

PASS_FG, FAIL_FG, SKIP_FG, MISS_FG = "#1d7a35", "#c0392b", "#9a7d0a", "#7f8c9b"


def _dpi_aware():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


class Fbx2MtaApp:
    def __init__(self, root):
        self.root = root
        root.title("FBX2MTA - FBX to MTA:SA DFF + COL")
        root.geometry("980x760")
        root.minsize(860, 640)

        self.selected = {}          # name -> BooleanVar
        self.quality = tk.StringVar(value="AUTO")
        self.gen_col = tk.BooleanVar(value=True)
        self.col_tris = tk.StringVar(value="3000")
        self.gen_ifp = tk.BooleanVar(value=True)
        self._last_log_len = 0
        self._last_log_text = ""

        self._build()
        self.refresh_files()
        self.refresh_results()
        root.after(1500, self._poll)

    # ------------------------------------------------------------------ UI
    def _build(self):
        pad = {"padx": 10, "pady": 6}
        main = ttk.Frame(self.root)
        main.pack(fill="both", expand=True, padx=8, pady=8)

        # ---- 1. input files -------------------------------------------
        f1 = ttk.LabelFrame(main, text=" 1 - INPUT FILES (input/) ")
        f1.pack(fill="x", **pad)
        self.files_box = ttk.Frame(f1)
        self.files_box.pack(fill="x", padx=8, pady=4)
        row = ttk.Frame(f1)
        row.pack(fill="x", padx=8, pady=(2, 8))
        self.pick_btn = ttk.Button(row, text="Choose FBX file ...",
                                   command=self.pick_fbx)
        self.pick_btn.pack(side="left")
        ttk.Button(row, text="+ Generate Test FBX",
                   command=self.generate_fbx).pack(side="left", padx=8)
        ttk.Button(row, text="+ Animated Test FBX (IFP test)",
                   command=lambda: self.generate_fbx(animated=True)
                   ).pack(side="left", padx=2)
        ttk.Label(row, foreground="#7f8c9b",
                  text="Choose FBX: pick any .fbx from your computer "
                       "(copied to input/)").pack(side="left", padx=10)

        # ---- 2. collision ----------------------------------------------
        f2 = ttk.LabelFrame(main, text=" 2 - COLLISION (COL) ")
        f2.pack(fill="x", **pad)
        r1 = ttk.Frame(f2)
        r1.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Checkbutton(r1, text="Generate COL (real collision geometry)",
                        variable=self.gen_col).pack(side="left")
        r2 = ttk.Frame(f2)
        r2.pack(fill="x", padx=8, pady=2)
        ttk.Label(r2, text="Quality:").pack(side="left")
        self.quality_var_box = ttk.Combobox(
            r2, textvariable=self.quality, state="readonly", width=26,
            values=["AUTO (by model size)", "LOW (~1,000 tris)",
                    "MEDIUM (~3,000 tris)", "HIGH (~10,000 tris)",
                    "CUSTOM (max triangles below)"])
        self.quality_var_box.current(0)
        self.quality_var_box.pack(side="left", padx=6)
        self.quality_var_box.bind("<<ComboboxSelected>>", self._on_quality)
        self.tris_wrap = ttk.Label(r2, text="Maximum Collision Triangles:")
        self.tris_wrap.pack(side="left", padx=(18, 4))
        self.tris_var_box = ttk.Spinbox(r2, from_=100, to=100000, increment=100,
                                        textvariable=self.col_tris, width=8)
        self.tris_var_box.pack(side="left")
        self.tris_wrap.pack_forget()
        self.tris_var_box.pack_forget()
        r3 = ttk.Frame(f2)
        r3.pack(fill="x", padx=8, pady=(2, 8))
        ttk.Label(r3, text="Quick presets:").pack(side="left")
        for n in PRESETS:
            ttk.Button(r3, text=str(n), width=7,
                       command=lambda n=n: self._preset(n)).pack(side="left", padx=3)
        ttk.Label(f2, foreground="#7f8c9b", justify="left",
                  text="AUTO: small model -> minimal reduction | medium -> ~3,000 | "
                       "large -> ~5,000 collision triangles (keeps outer silhouette / "
                       "floors / walls, removes interior detail).").pack(
                      anchor="w", padx=10, pady=(0, 8))

        # ---- 2b. animation (IFP) -----------------------------------------
        f2b = ttk.LabelFrame(main, text=" 2b - ANIMATION (IFP) ")
        f2b.pack(fill="x", **pad)
        ra = ttk.Frame(f2b)
        ra.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Checkbutton(ra, text="Export animation to IFP (MTA:SA / GTA SA ANP3)",
                        variable=self.gen_ifp).pack(side="left")
        ttk.Label(f2b, foreground="#7f8c9b", justify="left",
                  text="Automatic detection: if the FBX has an armature animation it is "
                       "baked to output/<name>.ifp (bone ids match the DFF frames - "
                       "load with engineLoadIFP + setPedAnimation). No animation -> "
                       "skipped, nothing exported.").pack(
                      anchor="w", padx=10, pady=(0, 8))

        # ---- 3. convert --------------------------------------------------
        f3 = ttk.LabelFrame(main, text=" 3 - CONVERT ")
        f3.pack(fill="x", **pad)
        r3m = ttk.Frame(f3)
        r3m.pack(fill="x", padx=8, pady=(8, 0))
        ttk.Label(r3m, text="Output:").pack(side="left")
        self.mode = tk.StringVar(value="both")
        mode_box = ttk.Combobox(r3m, textvariable=self.mode, state="readonly",
                                width=42,
                                values=["both", "dff", "ifp"])
        mode_box.pack(side="left", padx=6)
        ttk.Label(r3m, foreground="#7f8c9b",
                  text="both = DFF+COL+IFP (auto)  |  dff = no IFP  |  "
                       "ifp = IFP only (no DFF/COL)").pack(side="left", padx=6)
        r4 = ttk.Frame(f3)
        r4.pack(fill="x", padx=8, pady=8)
        self.convert_btn = ttk.Button(r4, text="Convert selected  ->  DFF + COL",
                                      command=self.do_convert)
        self.convert_btn.pack(side="left")
        self.res_btn = ttk.Button(r4, text="Generate MTA Test Resource",
                                  command=self.make_resource)
        self.res_btn.pack(side="left", padx=8)
        ttk.Button(r4, text="Open output folder",
                   command=self.open_output).pack(side="left", padx=8)
        ttk.Label(f3, foreground="#7f8c9b", justify="left",
                  text="Pipeline: FBX -> Blender processing -> DragonFF DFF export "
                       "(GTA SA v3.6.0.3) -> DFF validation + round-trip -> DragonFF "
                       "COL export (COL3) -> COL validation -> output/<model>.dff + .col. "
                       "COL failure never fails the DFF.").pack(anchor="w", padx=10, pady=(0, 8))

        # ---- 4. results ---------------------------------------------------
        f4 = ttk.LabelFrame(main, text=" 4 - RESULTS ")
        f4.pack(fill="both", expand=True, **pad)
        self.banner = tk.Label(f4, text="No conversion yet", anchor="w",
                               bg="#1a2430", fg="#9fc2e0", relief="solid",
                               bd=1, padx=10, pady=8, justify="left")
        self.banner.pack(fill="x", padx=8, pady=(8, 4))
        cols = ("model", "dff", "col", "ifp", "tris", "coltris", "outputs")
        self.tree = ttk.Treeview(f4, columns=cols, show="headings", height=6)
        for c, t, w in (("model", "Model", 120), ("dff", "DFF", 65),
                        ("col", "COL", 80), ("ifp", "IFP (anim)", 80),
                        ("tris", "Triangles", 90),
                        ("coltris", "Collision Triangles", 130),
                        ("outputs", "Outputs", 300)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        self.tree.tag_configure("pass", foreground=PASS_FG)
        self.tree.tag_configure("fail", foreground=FAIL_FG)
        self.tree.tag_configure("skip", foreground=SKIP_FG)
        self.tree.tag_configure("miss", foreground=MISS_FG)

        # ---- 5. log ---------------------------------------------------------
        f5 = ttk.LabelFrame(main, text=" 5 - LOG ")
        f5.pack(fill="x", **pad)
        self.log_txt = tk.Text(f5, height=12, state="disabled", bg="#0a0e12",
                               fg="#a8b6c5", insertbackground="#a8b6c5",
                               relief="flat", font=("Courier", 9))
        self.log_txt.pack(fill="x", padx=8, pady=8)

        self.busy_lbl = ttk.Label(main, text="")
        self.busy_lbl.pack(anchor="w", padx=12, pady=(0, 6))

    # ------------------------------------------------------------- actions
    def _quality_key(self):
        return self.quality.get().split(" ")[0]

    def _on_quality(self, _evt=None):
        if self._quality_key() == "CUSTOM":
            self.tris_wrap.pack(side="left", padx=(18, 4), before=self.tris_var_box)
            self.tris_var_box.pack(side="left")
        else:
            self.tris_wrap.pack_forget()
            self.tris_var_box.pack_forget()

    def _preset(self, n):
        self.quality_var_box.current(4)     # CUSTOM
        self.quality.set("CUSTOM (max triangles below)")
        self.col_tris.set(str(n))
        self._on_quality()

    def pick_fbx(self):
        path = filedialog.askopenfilename(
            title="Choose an FBX file", initialdir=joblib.INPUT_DIR,
            filetypes=[("FBX files", "*.fbx *.FBX"), ("All files", "*.*")])
        if not path:
            return
        ok, msg = joblib.upload_fbx(path)
        if not ok:
            messagebox.showerror("FBX2MTA", msg)
        self.refresh_files()
        self.refresh_results()

    def generate_fbx(self, animated=False):
        if animated:
            out = os.path.join(joblib.INPUT_DIR, "generated_anim.fbx")
            desc = "generate animated test FBX (IFP test)"
        else:
            out = os.path.join(joblib.INPUT_DIR, "generated_test.fbx")
            desc = "generate test FBX"
        cmd = [PY, RUN_BLENDER, GEN_FBX, "--output", out,
               "--log", os.path.join(ROOT, "logs", "test_fbx.log")]
        if animated:
            cmd.append("--animated")
        if not STATE.run(cmd, desc):
            self._busy_warning()

    def do_convert(self):
        files = [n for n, v in self.selected.items() if v.get()]
        if not files:
            messagebox.showwarning("FBX2MTA", "Select at least one FBX file above.")
            return
        q = self._quality_key()
        cmd = [PY, PIPELINE, "--budget", "AUTO", "--files"] + files
        if self.gen_col.get():
            cmd += ["--col-quality", q]
            if q == "CUSTOM":
                try:
                    cmd += ["--col-triangles", str(int(self.col_tris.get()))]
                except ValueError:
                    pass
        else:
            cmd += ["--no-col"]
        # the IFP checkbox refines mode=both (uncheck = DFF+COL only);
        # mode=dff/ifp are explicit and ignore the checkbox
        m = self.mode.get()
        if m == "both" and not self.gen_ifp.get():
            m = "dff"
        cmd += ["--mode", m]
        if not STATE.run(cmd, f"convert ({len(files)} file(s), mode={m}"
                              f"{', COL ' + q if self.gen_col.get() and m != 'ifp' else ''})"):
            self._busy_warning()

    def make_resource(self):
        results = joblib.latest_results()
        cands = [r for r in results if r["dff"] == "PASS"]
        if not cands:
            messagebox.showwarning("FBX2MTA",
                                   "No successful DFF found - run a conversion first.")
            return
        r = cands[-1]
        name = r["name"]
        cmd = [PY, RESOURCE, "--name", name,
               "--dff", os.path.join(joblib.OUTPUT_DIR, name + ".dff")]
        if r["col"] == "PASS":
            cmd += ["--col", os.path.join(joblib.OUTPUT_DIR, name + ".col")]
        else:
            cmd += ["--col-failed-reason", (r.get("col_reason") or r["col"])]
        if not STATE.run(cmd, f"MTA test resource for {name}"):
            self._busy_warning()

    def open_output(self):
        if joblib.open_in_file_manager(joblib.OUTPUT_DIR):
            pass
        else:
            messagebox.showinfo("FBX2MTA",
                                "Output folder: " + joblib.OUTPUT_DIR)

    def _busy_warning(self):
        STATE.log("another job is still running - please wait for it to finish")

    # ------------------------------------------------------------- refresh
    def refresh_files(self):
        for w in self.files_box.winfo_children():
            w.destroy()
        self.selected.clear()
        files = joblib.list_input_files()
        if not files:
            ttk.Label(self.files_box, text="no .fbx files in input/ yet "
                       "- use 'Choose FBX file ...'").pack(anchor="w", padx=4)
            return
        for f in files:
            name = f["name"][:-4]
            var = tk.BooleanVar(value=True)
            self.selected[name] = var
            cb = ttk.Checkbutton(self.files_box, variable=var,
                                 text=f"{f['name']}   ({f['size']/1024:.1f} KB)")
            cb.pack(anchor="w", padx=4, pady=1)

    def refresh_results(self):
        results = joblib.latest_results()
        self.tree.delete(*self.tree.get_children())
        any_row, all_ok = False, True
        last = None
        for r in results:
            any_row = True
            last = r
            dff_ok = r["dff"] in ("PASS", "SKIPPED")
            col_ok = r["col"] in ("PASS", "SKIPPED")
            ifp = r.get("ifp", "NONE")
            ifp_ok = ifp in ("PASS", "NONE", "SKIPPED")
            if not (dff_ok and col_ok and ifp_ok):
                all_ok = False
            outs = ", ".join(f"{o['file']} ({o['bytes']/1024:.1f} KB)"
                             for o in r["outputs"])
            col_txt = r["col"]
            if r["col"] == "FAILED":
                col_txt += " - " + (r.get("col_reason") or "see log")[:80]
            ifp_txt = ifp
            if ifp == "FAILED":
                ifp_txt += " - " + (r.get("ifp_reason") or "see log")[:80]
            tag = "fail" if not (dff_ok and col_ok and ifp_ok) else "pass"
            self.tree.insert("", "end", values=(
                r["name"], r["dff"], col_txt, ifp_txt,
                f"{r['triangles']:,}" if r.get("triangles") is not None else "-",
                f"{r['col_triangles']:,}" if r.get("col_triangles") is not None else "-",
                outs), tags=(tag,))
        b = self.banner
        busy = STATE.busy
        if busy:
            b.configure(bg="#1a2430", fg="#f0c93c",
                        text="Working... " + (STATE.job or ""))
        elif STATE.last_job_ok is False:
            b.configure(bg="#3a1518", fg="#e05252",
                        text="Conversion FAILED\n"
                             + (STATE.last_job_error or "see log above")[:220])
        elif any_row and all_ok and last:
            b.configure(bg="#12351f", fg="#37b45f",
                        text="Conversion Complete\n"
                             f"DFF: {last['dff']}   |   COL: {last['col']}   |   "
                             f"IFP: {last.get('ifp', 'NONE')}   |   "
                             f"Triangles: {last.get('triangles') or 0:,}   |   "
                             f"Collision Triangles: {last.get('col_triangles') or 0:,}\n"
                             "Outputs: " + ", ".join(
                                 o["file"] for o in last["outputs"]))
        elif any_row:
            bad = next((r for r in results
                        if r["dff"] != "PASS" or r["col"] == "FAILED"
                        or r.get("ifp") == "FAILED"), None)
            b.configure(bg="#3a1518", fg="#e05252",
                        text="Conversion finished with errors - "
                             + (bad["name"] if bad else "") +
                             (f"  (COL: {bad.get('col_reason')})"
                              if bad and bad.get("col_reason") else ""))
        else:
            b.configure(bg="#1a2430", fg="#9fc2e0", text="No conversion yet")
        self.busy_lbl.configure(
            text=("RUNNING: " + (STATE.job or "")) if busy else "")
        state = "disabled" if busy else "normal"
        self.convert_btn.configure(state=state)
        self.res_btn.configure(state=state)
        self.pick_btn.configure(state=state)

    def _poll(self):
        try:
            lines = STATE.snapshot_log()
            if len(lines) != self._last_log_len:
                self._last_log_len = len(lines)
                self._last_log_text = "\n".join(lines[-300:])
                self.log_txt.configure(state="normal")
                self.log_txt.delete("1.0", "end")
                self.log_txt.insert("end", self._last_log_text)
                self.log_txt.see("end")
                self.log_txt.configure(state="disabled")
            self.refresh_results()
        except Exception as e:  # never kill the UI loop
            STATE.log(f"UI error: {e}")
        self.root.after(1500, self._poll)


def main():
    _dpi_aware()
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except Exception:
        pass
    Fbx2MtaApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
