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
import shlex
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

import batch_leapp as core


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
# Native "this is a link" pointer (Safari-style finger on macOS).
LINK_CURSOR = "pointinghand" if sys.platform == "darwin" else "hand2"

# High-resolution logo rendered at header size (no runtime upscaling).
GUI_LOGO_DATA_URI = "data:image/png;base64,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ZyZrLaE2P/nkk7Zs2TK3pZs+rF271qUf/ehHblvOhz70IfvABz7gFv2vvfZae+mll+y1117rXZDlHeAv/uIvXIRNjDlKKONB/jfFSHEMQLVMt3seXG533/uGCLFRWoE5kBNEcbyNxMY86XJENr50tB07d5It1uT8+PmTbVpZsRZIk7NVnzaiLUlF8qqViUmD0KUF4iZpsep9TS70aNvOfW6da2tFje3aW2fVNS0u8qJd7Yet0F5oxJ6eNEf0pxxfbjd84nQ7YYG0Rphg4UwE1qyCwGIrTEO69dZbbenSpQYj/du//ZtzLBBjx7wJLfSXf/mXdv3119v48ezyObQhxUgxxocog9VrdtoPf/G8vfjaZrdwGq8XDrPNaR59ji8tsJOOm2ZnnzrbTl441S14eskdo/oR+Yl2EC5EmjGtxE49qby3XszEakVhbJcXcvPWatuoReUt22ts+6591iMeufziY+2qy052oUe9L+3/Y8mSJXb33Xe7HaYcNhIJcHujoe666y7HUNddd519/etfd95imOm5555z5iEMiTn4wQ9+0NjWwME6hxogVtmY9GOly8MbhxT47W9/ayDkSISGxjZ76Km37X9/9aLtlBnE3CQeJeTNqZycDJt31ES74Jx5dvZps+XVGtNr9ryb8YkGIyqiQyZqseZyscxborh/97vfuX1Gb775Zj/3dhAPnEJ15ZVX2umnn24//elP7fXXX5dpeoAJYSIYjXnWSEfdaFuHO/Bn7969wWbfowc3phgpiBZ9J7r6Z799WV65FW5yHs/6CBqI9STi2E45frpdeuGxMt+m2xgFjh7pQJgP86U//OEP9uCDD7qNckP1xMFs3/zmN+2aa64xNv+NFAzESIeejhwpzESoB5fzhi2VdttPnrVnXlrvFhjjYSIcEbm5mXb6ybPtykuOt1PEQMSspSCEATxyZ511lkt/93d/Zw899JA7SId5UnNz86DQ1NDQ4LQSa0qEEBFadChAipH2j0Kn5kMr39lht/7wSVv59g5N0GXKDTBCmDlM6HH7XnXZCXbW4tlurjHAa0f0z5wBwlzoox/9qJsD/e///q89+uij7rDGeBHD/Okf//EfXVT4e9/73kNiz1yKkTR6uHxfW77VvvfDJ2zD5soBw3rc+o8mwBPHj7Er3n+czLjjbNL4VKR8vIxAPjTK+973PnvPe95jzzzz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"


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
        self.ftype = tk.StringVar(value="auto")
        self.jobs = tk.IntVar(value=1)
        self.skip_existing = tk.BooleanVar(value=False)
        self.dry_run = tk.BooleanVar(value=False)
        self.hashes = tk.BooleanVar(value=True)
        self.extra = tk.StringVar()

        self._apply_theme()
        self._build()
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
                        bordercolor=BORDER, focuscolor=GOLD, padding=6)
        style.map("TButton",
                  background=[("active", SURFACE), ("disabled", OFF_BLACK)],
                  foreground=[("disabled", MUTED)],
                  bordercolor=[("active", GOLD)])
        style.configure("Accent.TButton", background=GOLD, foreground=OFF_BLACK,
                        font=("Helvetica Neue", 12, "bold"), padding=6)
        style.map("Accent.TButton",
                  background=[("active", GOLD_DK), ("disabled", BORDER)],
                  foreground=[("disabled", MUTED)])
        style.configure("TEntry", fieldbackground=SURFACE2, foreground=TEXT,
                        insertcolor=GOLD, bordercolor=BORDER, padding=4)
        style.configure("TCheckbutton", background=OFF_BLACK, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", OFF_BLACK)],
                  indicatorcolor=[("selected", GOLD)], foreground=[("active", GOLD)])
        style.configure("TSpinbox", fieldbackground=SURFACE2, foreground=TEXT,
                        arrowcolor=GOLD, bordercolor=BORDER, padding=3)

    def _load_logo(self):
        """Build a PhotoImage of the LEAPPs logo. Prefers GUI_LOGO_DATA_URI, a
        crisp copy pre-rendered at header size (drawn 1:1, no upscaling). Falls
        back to the index logo (subsampled) if needed. Kept on self for Tk GC."""
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
            bg=SURFACE, fg=TEXT, insertbackground=GOLD, borderwidth=0,
            highlightthickness=1, highlightbackground=BORDER,
            selectbackground=GOLD_DK, selectforeground=OFF_BLACK)
        self.log.grid(row=7, column=0, columnspan=3, sticky="nsew", **pad)
        frm.rowconfigure(7, weight=1)
        self.log.tag_configure("ok", foreground=OK_GREEN)
        self.log.tag_configure("fail", foreground=FAIL_RED)
        self.log.tag_configure("muted", foreground=MUTED)
        self.log.tag_configure("accent", foreground=GOLD)
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
        if not f:
            return
        if core.is_gui_build(Path(f)):
            messagebox.showerror(
                "GUI build selected",
                f"'{Path(f).name}' is the interactive GUI build and can't be used "
                f"for batch processing.\n\nChoose the command-line LEAPP tool "
                f"(e.g. ileapp.py or the CLI binary).")
            return
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
        if self.leapp.get() and core.is_gui_build(Path(self.leapp.get())):
            messagebox.showerror(
                "GUI build selected",
                f"'{Path(self.leapp.get()).name}' is the interactive GUI build and "
                f"can't be used for batch processing.\n\nChoose the command-line "
                f"LEAPP tool instead.")
            return

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
        tk.Label(tip, text=text, bg=SURFACE2, fg=GOLD, padx=8, pady=3,
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
        if any(k in up for k in ("FAIL", "ERROR", "TIMEOUT")):
            return "fail"
        if " OK " in up or up.strip().startswith("OK"):
            return "ok"
        if (text.startswith("===") or "START" in up or "HEARTBEAT" in up
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
