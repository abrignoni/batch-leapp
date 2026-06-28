#!/usr/bin/env python3
"""
batch_leapp_gui.py — a small Tkinter front-end for batch_leapp.

Pick an input directory of zips, an output directory, and a LEAPP tool (a .py
script OR a compiled binary / macOS .app), then run the whole batch with a live
log. Shares the exact engine the CLI uses (batch_leapp.run_batch).

    python batch_leapp_gui.py
"""

import os
import queue
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

import batch_leapp as core


DONE = "__DONE__"      # sentinel pushed on the queue when a run finishes


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

        self.input_dir = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.leapp = tk.StringVar(value=detect_leapp())
        self.ftype = tk.StringVar(value="zip")
        self.jobs = tk.IntVar(value=1)
        self.skip_existing = tk.BooleanVar(value=False)
        self.dry_run = tk.BooleanVar(value=False)

        self._build()
        self.root.after(100, self._drain)

    # ---- layout ----------------------------------------------------------
    def _build(self):
        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(self.root, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Batch LEAPP", font=("", 16, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        self._path_row(frm, 1, "Input dir (zips)", self.input_dir, self._pick_indir)
        self._path_row(frm, 2, "Output dir", self.output_dir, self._pick_outdir)
        self._path_row(frm, 3, "LEAPP tool", self.leapp, self._pick_leapp)

        opts = ttk.Frame(frm)
        opts.grid(row=4, column=0, columnspan=3, sticky="we", **pad)
        ttk.Label(opts, text="Type").pack(side="left")
        ttk.Entry(opts, textvariable=self.ftype, width=7).pack(side="left", padx=(4, 16))
        ttk.Label(opts, text="Parallel jobs").pack(side="left")
        ttk.Spinbox(opts, from_=1, to=64, width=4, textvariable=self.jobs).pack(
            side="left", padx=(4, 16))
        ttk.Checkbutton(opts, text="Skip existing", variable=self.skip_existing).pack(
            side="left", padx=6)
        ttk.Checkbutton(opts, text="Dry run", variable=self.dry_run).pack(
            side="left", padx=6)

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=3, sticky="we", **pad)
        self.run_btn = ttk.Button(btns, text="Run", command=self._start)
        self.run_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="Stop", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.open_index_btn = ttk.Button(btns, text="Open report index",
                                         command=self._open_index, state="disabled")
        self.open_index_btn.pack(side="left", padx=6)
        self.open_out_btn = ttk.Button(btns, text="Open output folder",
                                       command=self._open_output, state="disabled")
        self.open_out_btn.pack(side="left", padx=6)
        self.status = ttk.Label(btns, text="Idle")
        self.status.pack(side="right")

        self.log = scrolledtext.ScrolledText(frm, height=18, wrap="none",
                                             font=("Menlo", 11))
        self.log.grid(row=6, column=0, columnspan=3, sticky="nsew", **pad)
        frm.rowconfigure(6, weight=1)
        self.log.configure(state="disabled")

    def _path_row(self, frm, row, label, var, cmd):
        ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(frm, textvariable=var).grid(row=row, column=1, sticky="we", pady=4)
        ttk.Button(frm, text="Browse…", command=cmd).grid(row=row, column=2, padx=8)

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
        f = filedialog.askopenfilename(
            title="LEAPP script, binary, or .app",
            filetypes=[("LEAPP tool", "*.py *.exe *.app *"), ("All files", "*")])
        if f:
            self.leapp.set(f)

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

        self._clear_log()
        self.stop_event.clear()
        self.last_index = self.last_output = None
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.open_index_btn.configure(state="disabled")
        self.open_out_btn.configure(state="disabled")
        self.status.configure(text="Running…")

        opts = dict(
            input_dir=self.input_dir.get(), output_dir=self.output_dir.get(),
            leapp=self.leapp.get() or "ileapp.py", type=self.ftype.get() or "zip",
            jobs=max(1, self.jobs.get()), skip_existing=self.skip_existing.get(),
            dry_run=self.dry_run.get(),
        )
        self.worker = threading.Thread(target=self._run_worker, args=(opts,), daemon=True)
        self.worker.start()

    def _run_worker(self, opts):
        try:
            result = core.run_batch(
                opts["input_dir"], opts["output_dir"], opts["leapp"],
                type=opts["type"], jobs=opts["jobs"],
                skip_existing=opts["skip_existing"], dry_run=opts["dry_run"],
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
        self._append("\n[stop requested — finishing in-flight jobs]\n")

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
        n_fail = len(result["failed"])
        self.status.configure(
            text=f"Done — {len(result['ok'])} ok, {n_fail} failed, "
                 f"{len(result['skipped'])} skipped")
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

    def _append(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
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
