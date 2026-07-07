#!/usr/bin/env python3
"""
batch_leapp_gui.py — a small Tkinter front-end for batch_leapp.

Pick an input directory of zips, an output directory, and a LEAPP tool (a .py
script OR a compiled binary / macOS .app), then run the whole batch with a live
log. Shares the exact engine the CLI uses (batch_leapp.run_batch).

    python batch_leapp_gui.py
"""

import json
import os
import queue
import shlex
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

import batch_leapp as core


def _config_dir() -> Path:
    """Per-OS folder for batch-leapp's own GUI settings (recent paths)."""
    home = Path.home()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or (home / "AppData" / "Roaming")
        return Path(base) / "BatchLEAPP"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "BatchLEAPP"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else home / ".config") / "BatchLEAPP"


class History:
    """Remembers recently used input dirs, output dirs, and LEAPP tools so the
    GUI can prefill and offer them again. Stored as JSON in the config dir."""
    LIMIT = 10
    KEYS = ("input_dirs", "output_dirs", "leapp_tools")

    def __init__(self):
        self.path = _config_dir() / "history.json"
        self.data = {k: [] for k in self.KEYS}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            for k in self.KEYS:
                if isinstance(loaded.get(k), list):
                    self.data[k] = [str(p) for p in loaded[k]]
        except (OSError, ValueError):
            pass

    def recent(self, key):
        return self.data.get(key, [])

    def most_recent(self, key):
        vals = self.data.get(key, [])
        return vals[0] if vals else ""

    def add(self, key, value):
        if not value:
            return
        vals = [p for p in self.data.get(key, []) if p != value]
        vals.insert(0, value)
        self.data[key] = vals[:self.LIMIT]
        self._save()

    def clear(self, key):
        self.data[key] = []
        self._save()

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass


DONE = "__DONE__"      # sentinel pushed on the queue when a run finishes

# leapps.org palette (mirrors css/global.css).
GOLD = "#F5C020"
GOLD_DK = "#D4A010"
OFF_BLACK = "#0E0E0E"
SURFACE = "#161616"
SURFACE2 = "#1C1C1C"
BORDER = "#2C2C2C"
TEXT = "#F0EDE6"
MUTED = "#888888"
OK_GREEN = "#A4C639"
FAIL_RED = "#E30613"
# batch-leapp brand accent (its color on leapps.org) — used for the GUI chrome.
ACCENT = "#E8762D"
ACCENT_DK = "#C25E1E"
# Native "this is a link" pointer (Safari-style finger on macOS).
LINK_CURSOR = "pointinghand" if sys.platform == "darwin" else "hand2"

# High-resolution logo rendered at header size (no runtime upscaling).
GUI_LOGO_DATA_URI = "data:image/png;base64,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"


def detect_leapp():
    """Best-effort guess at an installed LEAPP tool to prefill the field."""
    import shutil
    for name in ("ileapp", "aleapp", "rleapp", "vleapp"):
        found = shutil.which(name)
        if found:
            return found
    if sys.platform == "darwin":
        for app in sorted(Path("/Applications").glob("*[lL][eE][aA][pP][pP]*.app")):
            if "gui" not in app.stem.lower():
                return str(app)
    return ""


def open_path(path):
    """Open a file/folder with the OS default handler."""
    path = str(path)
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif sys.platform == "win32":
        os.startfile(path)            # noqa: S606 (intended)
    else:
        subprocess.Popen(["xdg-open", path])


class BatchLeappGUI:
    def __init__(self, root):
        self.root = root
        root.title("Batch LEAPP")
        root.minsize(760, 560)

        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.last_index = None
        self.last_output = None
        self.history = History()

        # Prefill from history (last used), falling back to auto-detection.
        self.input_dir = tk.StringVar(value=self.history.most_recent("input_dirs"))
        self.output_dir = tk.StringVar(value=self.history.most_recent("output_dirs"))
        self.leapp = tk.StringVar(
            value=self.history.most_recent("leapp_tools") or detect_leapp())
        self.ftype = tk.StringVar(value="auto")
        self.jobs = tk.IntVar(value=1)
        self.skip_existing = tk.BooleanVar(value=False)
        self.dry_run = tk.BooleanVar(value=False)
        self.hashes = tk.BooleanVar(value=True)
        self.extra = tk.StringVar()

        self._apply_theme()
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._drain)

    # ---- theming ---------------------------------------------------------
    def _apply_theme(self):
        self.root.configure(bg=OFF_BLACK)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=OFF_BLACK, foreground=TEXT,
                        fieldbackground=SURFACE2, bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER, troughcolor=SURFACE)
        style.configure("TFrame", background=OFF_BLACK)
        style.configure("TLabel", background=OFF_BLACK, foreground=TEXT)
        style.configure("Header.TLabel", background=OFF_BLACK, foreground=TEXT,
                        font=("Helvetica Neue", 22, "bold"))
        style.configure("Status.TLabel", background=OFF_BLACK, foreground=MUTED)
        style.configure("TButton", background=SURFACE2, foreground=TEXT,
                        bordercolor=BORDER, focuscolor=ACCENT, padding=6)
        style.map("TButton",
                  background=[("active", SURFACE), ("disabled", OFF_BLACK)],
                  foreground=[("disabled", MUTED)],
                  bordercolor=[("active", ACCENT)])
        style.configure("Accent.TButton", background=ACCENT, foreground=OFF_BLACK,
                        font=("Helvetica Neue", 12, "bold"), padding=6)
        style.map("Accent.TButton",
                  background=[("active", ACCENT_DK), ("disabled", BORDER)],
                  foreground=[("disabled", MUTED)])
        style.configure("TEntry", fieldbackground=SURFACE2, foreground=TEXT,
                        insertcolor=ACCENT, bordercolor=BORDER, padding=4)
        style.configure("TCheckbutton", background=OFF_BLACK, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", OFF_BLACK)],
                  indicatorcolor=[("selected", ACCENT)], foreground=[("active", ACCENT)])
        style.configure("TSpinbox", fieldbackground=SURFACE2, foreground=TEXT,
                        arrowcolor=ACCENT, bordercolor=BORDER, padding=3)

    def _load_logo(self):
        """Build a PhotoImage of the batch-leapp icon. Prefers GUI_LOGO_DATA_URI,
        pre-rendered at header size (drawn 1:1, no upscaling). Falls back to the
        index logo (subsampled) if needed. Kept on self for Tk GC."""
        try:
            b64 = GUI_LOGO_DATA_URI.split(",", 1)[1]
            self._logo_img = tk.PhotoImage(data=b64)
            return self._logo_img
        except Exception:
            pass
        try:
            b64 = core.LEAPP_LOGO_DATA_URI.split(",", 1)[1]
            img = tk.PhotoImage(data=b64)
            factor = max(1, img.height() // 56)
            if factor > 1:
                img = img.subsample(factor, factor)
            self._logo_img = img
            return img
        except Exception:
            return None

    # ---- layout ----------------------------------------------------------
    def _build(self):
        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(self.root, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        header = ttk.Frame(frm)
        header.grid(row=0, column=0, columnspan=3, sticky="we", pady=(0, 12))
        logo = self._load_logo()
        if logo is not None:
            logo_lbl = tk.Label(header, image=logo, bg=OFF_BLACK)
            logo_lbl.pack(side="left", padx=(0, 12))
            self._make_link(logo_lbl, "https://leapps.org", "Open leapps.org ↗")
        titles = ttk.Frame(header)
        titles.pack(side="left", anchor="center")
        ttk.Label(titles, text="Batch LEAPP", style="Header.TLabel").pack(anchor="w")
        ttk.Label(titles, text="Run iLEAPP / ALEAPP / RLEAPP / VLEAPP across a "
                              "folder of zips", style="Status.TLabel").pack(anchor="w")

        self._path_row(frm, 1, "Input dir (zips)", self.input_dir,
                       self._pick_indir, "input_dirs")
        self._path_row(frm, 2, "Output dir", self.output_dir,
                       self._pick_outdir, "output_dirs")
        self._path_row(frm, 3, "LEAPP tool", self.leapp,
                       self._pick_leapp, "leapp_tools")

        opts = ttk.Frame(frm)
        opts.grid(row=4, column=0, columnspan=3, sticky="we", **pad)
        ttk.Label(opts, text="Type").pack(side="left")
        ttk.Entry(opts, textvariable=self.ftype, width=7).pack(side="left", padx=(4, 16))
        ttk.Label(opts, text="Parallel jobs").pack(side="left")
        ttk.Spinbox(opts, from_=1, to=64, width=4, textvariable=self.jobs).pack(
            side="left", padx=(4, 16))
        ttk.Checkbutton(opts, text="Skip existing", variable=self.skip_existing).pack(
            side="left", padx=6)
        ttk.Checkbutton(opts, text="SHA-256", variable=self.hashes).pack(
            side="left", padx=6)
        ttk.Checkbutton(opts, text="Dry run", variable=self.dry_run).pack(
            side="left", padx=6)

        extra = ttk.Frame(frm)
        extra.grid(row=5, column=0, columnspan=3, sticky="we", **pad)
        ttk.Label(extra, text="Extra LEAPP args").pack(side="left")
        ttk.Entry(extra, textvariable=self.extra).pack(
            side="left", fill="x", expand=True, padx=(8, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=6, column=0, columnspan=3, sticky="we", **pad)
        self.run_btn = ttk.Button(btns, text="Run", command=self._start,
                                  style="Accent.TButton")
        self.run_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="Stop", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.open_index_btn = ttk.Button(btns, text="Open report index",
                                         command=self._open_index, state="disabled")
        self.open_index_btn.pack(side="left", padx=6)
        self.open_out_btn = ttk.Button(btns, text="Open output folder",
                                       command=self._open_output, state="disabled")
        self.open_out_btn.pack(side="left", padx=6)
        self.status = ttk.Label(btns, text="Idle", style="Status.TLabel")
        self.status.pack(side="right")

        self.log = scrolledtext.ScrolledText(
            frm, height=18, wrap="none", font=("Menlo", 11),
            bg=SURFACE, fg=TEXT, insertbackground=ACCENT, borderwidth=0,
            highlightthickness=1, highlightbackground=BORDER,
            selectbackground=ACCENT_DK, selectforeground=OFF_BLACK)
        self.log.grid(row=7, column=0, columnspan=3, sticky="nsew", **pad)
        frm.rowconfigure(7, weight=1)
        self.log.tag_configure("ok", foreground=OK_GREEN)
        self.log.tag_configure("fail", foreground=FAIL_RED)
        self.log.tag_configure("muted", foreground=MUTED)
        self.log.tag_configure("accent", foreground=ACCENT)
        self.log.configure(state="disabled")

    def _path_row(self, frm, row, label, var, cmd, hist_key):
        ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(frm, textvariable=var).grid(row=row, column=1, sticky="we", pady=4)
        box = ttk.Frame(frm)
        box.grid(row=row, column=2, padx=8, sticky="e")
        ttk.Button(box, text="Browse…", command=cmd).pack(side="left")
        rb = ttk.Button(box, text="Recent ▾", width=9)
        rb.configure(command=lambda b=rb: self._show_recent(b, var, hist_key))
        rb.pack(side="left", padx=(4, 0))

    def _show_recent(self, button, var, key):
        menu = tk.Menu(self.root, tearoff=0, bg=SURFACE2, fg=TEXT,
                       activebackground=ACCENT, activeforeground=OFF_BLACK,
                       bd=0, relief="flat")
        paths = self.history.recent(key)
        if not paths:
            menu.add_command(label="(no recent items)", state="disabled")
        else:
            for p in paths:
                menu.add_command(label=p, command=lambda v=p: var.set(v))
            menu.add_separator()
            menu.add_command(label="Clear recent",
                             command=lambda: self.history.clear(key))
        menu.post(button.winfo_rootx(),
                  button.winfo_rooty() + button.winfo_height())

    # ---- pickers ---------------------------------------------------------
    def _pick_indir(self):
        d = filedialog.askdirectory(title="Input directory of zips")
        if d:
            self.input_dir.set(d)

    def _pick_outdir(self):
        d = filedialog.askdirectory(title="Output directory for reports")
        if d:
            self.output_dir.set(d)

    def _pick_leapp(self):
        # On macOS a `filetypes` filter makes the open panel treat .app bundles
        # as folders you navigate INTO instead of selecting — so omit it there
        # and let packages be chosen as files.
        kw = {}
        if sys.platform != "darwin":
            kw["filetypes"] = [("LEAPP tool", "*.py *.exe *.app *"),
                               ("All files", "*")]
        f = filedialog.askopenfilename(
            title="LEAPP script, binary, or .app", **kw)
        if not f:
            return
        p = Path(f)
        # If they navigated inside a .app bundle, snap back to the bundle root.
        for cand in (p, *p.parents):
            if cand.suffix.lower() == ".app":
                p = cand
                break
        if core.is_gui_build(p):
            messagebox.showerror(
                "GUI build selected",
                f"'{p.name}' is the interactive GUI build and can't be used "
                f"for batch processing.\n\nChoose the command-line LEAPP tool "
                f"instead — the CLI binary, or the ileapp.py / aleapp.py script "
                f"from the tool's source folder.")
            return
        self.leapp.set(str(p))

    # ---- run / stop ------------------------------------------------------
    def _start(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.input_dir.get() or not self.output_dir.get():
            messagebox.showerror("Missing path", "Choose an input and output directory.")
            return
        if not self.dry_run.get() and not self.leapp.get():
            messagebox.showerror("Missing LEAPP tool",
                                 "Choose a LEAPP script, binary, or .app.")
            return
        if self.leapp.get() and core.is_gui_build(Path(self.leapp.get())):
            messagebox.showerror(
                "GUI build selected",
                f"'{Path(self.leapp.get()).name}' is the interactive GUI build and "
                f"can't be used for batch processing.\n\nChoose the command-line "
                f"LEAPP tool instead.")
            return

        # Remember the paths that were actually used, most-recent first.
        self.history.add("input_dirs", self.input_dir.get())
        self.history.add("output_dirs", self.output_dir.get())
        self.history.add("leapp_tools", self.leapp.get())

        self._clear_log()
        self.stop_event.clear()
        self.last_index = self.last_output = None
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.open_index_btn.configure(state="disabled")
        self.open_out_btn.configure(state="disabled")
        self.status.configure(text="Running…")

        try:
            extra_args = shlex.split(self.extra.get())
        except ValueError as ex:
            self.run_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
            self.status.configure(text="Idle")
            messagebox.showerror("Bad extra args",
                                 f"Could not parse the Extra LEAPP args:\n{ex}")
            return

        opts = dict(
            input_dir=self.input_dir.get(), output_dir=self.output_dir.get(),
            leapp=self.leapp.get() or "ileapp.py", type=self.ftype.get() or "auto",
            jobs=max(1, self.jobs.get()), skip_existing=self.skip_existing.get(),
            dry_run=self.dry_run.get(), hashes=self.hashes.get(),
            extra_args=extra_args,
        )
        self.worker = threading.Thread(target=self._run_worker, args=(opts,), daemon=True)
        self.worker.start()

    def _run_worker(self, opts):
        try:
            result = core.run_batch(
                opts["input_dir"], opts["output_dir"], opts["leapp"],
                type=opts["type"], jobs=opts["jobs"],
                skip_existing=opts["skip_existing"], dry_run=opts["dry_run"],
                hashes=opts["hashes"], extra_args=opts["extra_args"],
                capture=True,                      # never spray to stdout
                log=lambda s: self.q.put(s),
                should_stop=self.stop_event.is_set,
            )
            self.q.put((DONE, result, None))
        except core.BatchError as ex:
            self.q.put((DONE, None, str(ex)))
        except Exception as ex:                    # surface unexpected errors
            self.q.put((DONE, None, f"Unexpected error: {ex}"))

    def _stop(self):
        self.stop_event.set()
        self.status.configure(text="Stopping…")
        self._append("\n[stop requested — terminating running LEAPP jobs]\n")

    def _on_close(self):
        """Don't leave LEAPP processes orphaned when the window is closed."""
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(
                    "Quit Batch LEAPP",
                    "A batch is still running.\n\nStop the running LEAPP jobs "
                    "and quit?"):
                return
            self.stop_event.set()
            self.status.configure(text="Stopping…")
            self._await_close()
        else:
            self.root.destroy()

    def _await_close(self, tries=0):
        # Let the worker terminate its child processes, then close (cap ~12s).
        if self.worker and self.worker.is_alive() and tries < 60:
            self.root.after(200, lambda: self._await_close(tries + 1))
        else:
            self.root.destroy()

    # ---- queue pump (runs on the Tk main thread) -------------------------
    def _drain(self):
        try:
            while True:
                item = self.q.get_nowait()
                if isinstance(item, tuple) and item and item[0] == DONE:
                    self._finish(item[1], item[2])
                else:
                    self._append(item + "\n")
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _finish(self, result, error):
        self.run_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        if error:
            self.status.configure(text="Error")
            messagebox.showerror("Batch LEAPP", error)
            return
        self.last_index = result.get("index")
        self.last_output = Path(self.output_dir.get()).expanduser()
        n_inv = len(result.get("invalid", []))
        inv = f", {n_inv} invalid" if n_inv else ""
        self.status.configure(
            text=f"Done — {len(result['ok'])} ok, {len(result['failed'])} "
                 f"failed{inv}, {len(result['skipped'])} skipped")
        if self.last_index:
            self.open_index_btn.configure(state="normal")
        if self.last_output and self.last_output.is_dir():
            self.open_out_btn.configure(state="normal")

    # ---- helpers ---------------------------------------------------------
    def _open_index(self):
        if self.last_index:
            webbrowser.open_new_tab(Path(self.last_index).as_uri())

    def _open_output(self):
        if self.last_output:
            open_path(self.last_output)

    def _make_link(self, widget, url, tip):
        """Turn a widget into a clickable link: link cursor, click opens the URL,
        and a small tooltip on hover so it's obviously clickable."""
        widget.configure(cursor=LINK_CURSOR)
        widget.bind("<Button-1>", lambda _e: webbrowser.open_new_tab(url))
        widget.bind("<Enter>", lambda _e: self._show_tip(widget, tip))
        widget.bind("<Leave>", lambda _e: self._hide_tip())

    def _show_tip(self, widget, text):
        self._hide_tip()
        tip = tk.Toplevel(self.root)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{widget.winfo_rootx() + 12}"
                        f"+{widget.winfo_rooty() + widget.winfo_height() + 4}")
        tk.Label(tip, text=text, bg=SURFACE2, fg=ACCENT, padx=8, pady=3,
                 bd=1, relief="solid", font=("Helvetica Neue", 10)).pack()
        self._tip = tip

    def _hide_tip(self):
        tip = getattr(self, "_tip", None)
        if tip is not None:
            tip.destroy()
            self._tip = None

    @staticmethod
    def _line_tag(text):
        up = text.upper()
        s = text.strip()
        # Summary/separator lines mention "failed" even on a clean run — keep
        # them neutral so a successful batch doesn't look like a failure.
        if s.startswith("Done.") or s.startswith("===") or s.startswith("Finished:") \
                or s.startswith("Started:") or s.startswith("Elapsed:"):
            return "muted"
        if any(k in up for k in ("FAIL", "ERROR", "TIMEOUT")):
            return "fail"
        if " OK " in up or up.strip().startswith("OK"):
            return "ok"
        if ("START" in up or "HEARTBEAT" in up
                or "still running" in text or "STILL RUNNING" in up):
            return "muted"
        return ""

    def _append(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text, self._line_tag(text))
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")


def main():
    root = tk.Tk()
    BatchLeappGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
