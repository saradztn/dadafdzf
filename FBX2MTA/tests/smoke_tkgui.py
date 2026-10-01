#!/usr/bin/env python3
"""Headless smoke test for the tkinter GUI (this sandbox has no display/tkinter).

Builds a fake `tkinter` module that records widget creation, then instantiates
the real Fbx2MtaApp and exercises its methods - catching any wiring error
(unknown widget method, bad attribute, bad call) before the user runs it.
"""
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CREATED = []
CALLS = []


class FakeVar:
    def __init__(self, value=None, **kw):
        self._v = value
        CALLS.append(("var", self.__class__.__name__, value))

    def get(self):
        return self._v

    def set(self, v):
        self._v = v


class FakeWidget:
    def __init__(self, master=None, **kw):
        CREATED.append((type(self).__name__, kw))
        self.master = master
        self.kw = kw
        self._children = []
        self._tags = ()

    def pack(self, **kw):
        CALLS.append(("pack", type(self).__name__))
        if self.master is not None and hasattr(self.master, "_children"):
            self.master._children.append(self)

    def pack_forget(self):
        CALLS.append(("pack_forget", type(self).__name__))

    def configure(self, **kw):
        self.kw.update(kw)

    def bind(self, *a, **kw):
        pass

    def destroy(self):
        pass

    def winfo_children(self):
        return list(getattr(self, "_children", []))


class FakeRoot(FakeWidget):
    def __init__(self):
        super().__init__()
        self._afters = []

    def title(self, *a):
        pass

    def geometry(self, *a):
        pass

    def minsize(self, *a):
        pass

    def after(self, ms, fn=None, *a):
        self._afters.append((ms, fn))

    def mainloop(self):
        pass


class FakeTreeview(FakeWidget):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._items = {}
        self._n = 0

    def heading(self, *a, **kw):
        pass

    def column(self, *a, **kw):
        pass

    def tag_configure(self, *a, **kw):
        pass

    def delete(self, *iids):
        for i in iids:
            self._items.pop(i, None)

    def get_children(self):
        return list(self._items)

    def insert(self, *a, **kw):
        self._n += 1
        iid = f"i{self._n}"
        self._items[iid] = kw.get("values", ())
        self._items[iid + ":tags"] = kw.get("tags", ())
        return iid

    def item(self, iid, opt=None, **kw):
        if opt == "values":
            return self._items.get(iid, ())
        if opt == "tags":
            return self._items.get(iid + ":tags", ())
        if opt is None:
            self._items[iid + ":tags"] = tuple(kw.get("tags", ()))
        return {}


class FakeCombobox(FakeWidget):
    def current(self, i):
        pass


class FakeText(FakeWidget):
    def configure(self, **kw):
        super().configure(**kw)

    def delete(self, *a):
        pass

    def insert(self, *a, **kw):
        pass

    def see(self, *a):
        pass


def build_fake_tkinter():
    tk = types.ModuleType("tkinter")
    tk.Tk = FakeRoot
    tk.Frame = FakeWidget
    tk.Label = FakeWidget
    tk.Button = FakeWidget
    tk.Checkbutton = FakeWidget
    tk.Text = FakeText
    tk.BooleanVar = FakeVar
    tk.StringVar = FakeVar
    tk.END = "end"
    tk.W = "w"

    ttk = types.ModuleType("tkinter.ttk")
    ttk.Frame = FakeWidget
    ttk.LabelFrame = FakeWidget
    ttk.Label = FakeWidget
    ttk.Button = FakeWidget
    ttk.Checkbutton = FakeWidget
    ttk.Combobox = FakeCombobox
    ttk.Spinbox = FakeWidget
    ttk.Treeview = FakeTreeview

    class FakeStyle:
        def theme_use(self, name):
            pass
    ttk.Style = FakeStyle
    tk.ttk = ttk

    fd = types.ModuleType("tkinter.filedialog")
    fd.askopenfilename = lambda **kw: ""
    tk.filedialog = fd

    mb = types.ModuleType("tkinter.messagebox")
    mb.showerror = mb.showwarning = mb.showinfo = lambda *a, **kw: None
    tk.messagebox = mb

    sys.modules["tkinter"] = tk
    sys.modules["tkinter.ttk"] = ttk
    sys.modules["tkinter.filedialog"] = fd
    sys.modules["tkinter.messagebox"] = mb
    return tk


def main():
    tk = build_fake_tkinter()
    import tkgui  # imports the fake tkinter

    root = tk.Tk()
    app = tkgui.Fbx2MtaApp(root)

    # --- exercise every action/method
    app.refresh_files()
    app.refresh_results()
    app._preset(1000)
    assert app.col_tris.get() == "1000", app.col_tris.get()
    assert app._quality_key() == "CUSTOM"
    app._on_quality()
    app.quality_var_box.current(0)
    app.quality.set("AUTO (by model size)")
    app._on_quality()
    assert app._quality_key() == "AUTO"

    # pick_fbx with no selection (askopenfilename returns "") -> no-op
    app.pick_fbx()

    # do_convert with no selection -> warning (messagebox mocked)
    for v in app.selected.values():
        v.set(False)
    app.do_convert()

    # do_convert with selection -> would start a job; mock STATE.run
    started = []
    tkgui.STATE.run = lambda cmd, desc: (started.append((cmd, desc)) or True)
    for v in app.selected.values():
        v.set(True)
    app.do_convert()
    assert started and started[0][1].startswith("convert"), started
    cmd = started[0][0]
    assert "--files" in cmd, cmd
    print("convert cmd:", " ".join(os.path.basename(c) if c.endswith(".py") else c
                                   for c in cmd[:4]), "...", cmd[cmd.index("--files"):])

    # COL disabled path
    app.gen_col.set(False)
    started.clear()
    app.do_convert()
    assert "--no-col" in started[0][0]
    app.gen_col.set(True)

    # generate fbx
    started.clear()
    app.generate_fbx()
    assert "generate test FBX" in started[0][1]

    # resource (needs a PASS result; dragon has outputs in this env)
    started.clear()
    app.make_resource()
    if started:
        assert "MTA test resource" in started[0][1], started[0]

    # one poll cycle (log drain + results refresh)
    tkgui.STATE.log("test line from smoke test")
    app._last_log_len = 0
    app._poll()
    assert app._last_log_len > 0

    # open_output (no file manager in sandbox -> messagebox mocked)
    app.open_output()

    # quality preset buttons all work
    for n in (500, 2000, 10000):
        app._preset(n)
        assert app.col_tris.get() == str(n)

    n_widgets = len(CREATED)
    print(f"SMOKE OK: {n_widgets} widgets created, "
          f"{len([c for c in CALLS if c[0] == 'pack'])} packs, "
          f"all action paths exercised")
    os._exit(0)


if __name__ == "__main__":
    main()
