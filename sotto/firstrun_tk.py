# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Shared first-run windows (tkinter): welcome walkthrough + download screen.

Mirrors the AppKit flow in firstrun.py: rows re-verified every second and
again at the Start click; models never gate beyond their consent checkbox;
completion writes the pending marker and relaunches. Each window owns the
main thread with its own mainloop — they run BEFORE the overlay exists,
exactly like their AppKit counterparts own the process on macOS.

The platform specifics (rows, checks, gating, fix actions, relaunch idiom)
come from a BACKEND module — firstrun_linux or firstrun_windows (W5) — with
the same surface: ROWS, GATING, SUBTITLE, statuses(cfg), run_fix(action),
engine_missing(cfg), setup_missing(cfg), relaunch().
"""

import logging
import queue
import threading

from . import firstrun

log = logging.getLogger("sotto")


def _backend():
    from .platform import IS_WINDOWS
    if IS_WINDOWS:
        from . import firstrun_windows
        return firstrun_windows
    from . import firstrun_linux
    return firstrun_linux

GREEN, GRAY = "#2e9e4f", "#b9b3a9"
AMBER = "#d99a06"  # authorized-but-not-done (the models consent) — never
                   # green until the download actually exists ("no lying
                   # green rows")


class _Walkthrough:
    def __init__(self, cfg, backend=None):
        import tkinter as tk
        self.tk = tk
        self.cfg = cfg
        self.be = backend or _backend()
        root = self.root = tk.Tk()
        root.title("Welcome to Sotto")
        root.resizable(False, False)
        tk.Label(root, text="Welcome to Sotto",
                 font=("", 16, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=18, pady=(14, 2))
        tk.Label(root, text=self.be.SUBTITLE,
                 fg="#555").grid(row=1, column=0, columnspan=3,
                                 sticky="w", padx=18, pady=(0, 8))
        self.dots, self.buttons = {}, {}
        # Downloading 3-4 GB needs explicit consent, not an FYI row: Start
        # stays disabled until the user ticks this (or the models already
        # exist) — VM-round product decision, 2026-07-19.
        self.models_ok = tk.BooleanVar(value=False)
        for i, (key, title, detail, btn, action) in enumerate(self.be.ROWS):
            r = 2 + i * 2
            dot = tk.Canvas(root, width=14, height=14, highlightthickness=0)
            dot.grid(row=r, column=0, sticky="ne", padx=(18, 6), pady=(6, 0))
            self.dots[key] = dot
            tk.Label(root, text=title, font=("", 11, "bold")).grid(
                row=r, column=1, sticky="w")
            tk.Label(root, text=detail, wraplength=330, justify="left",
                     fg="#555").grid(row=r + 1, column=1, sticky="w",
                                     pady=(0, 4))
            if btn:
                b = tk.Button(root, text=btn, command=(
                    lambda a=action: self.be.run_fix(a)))
                b.grid(row=r, column=2, rowspan=2, sticky="e", padx=(8, 18))
                self.buttons[key] = b
            if key == "models":
                cb = tk.Checkbutton(root, text="OK, download",
                                    variable=self.models_ok,
                                    command=lambda: self.tick(loop=False))
                cb.grid(row=r, column=2, rowspan=2, sticky="e", padx=(8, 18))
                self.buttons[key] = cb
        self.start_btn = tk.Button(root, text="Start Sotto",
                                   command=self.start, state="disabled")
        self.start_btn.grid(row=20, column=0, columnspan=3,
                            pady=(10, 14))
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._closed = False

    def _models_gate(self, st) -> bool:
        """Models gate Start until either they exist or the user has
        explicitly OK'd the download."""
        return bool(st.get("models")) or self.models_ok.get()

    # one honest re-check per second, same cadence as the AppKit window
    def tick(self, loop=True):
        st = self.be.statuses(self.cfg)
        for key, dot in self.dots.items():
            dot.delete("all")
            if st.get(key):
                fill = GREEN
            elif key == "models" and self.models_ok.get():
                fill = AMBER  # consented, download still ahead
            else:
                fill = GRAY
            dot.create_oval(2, 2, 12, 12, fill=fill, outline="")
        for key, btn in self.buttons.items():
            btn.grid_remove() if st.get(key) else btn.grid()
        ready = (all(st[k] for k in self.be.GATING)
                 and self._models_gate(st))
        self.start_btn.config(state="normal" if ready else "disabled")
        if loop and not self._closed:
            self.root.after(1000, self.tick)
        return st

    def start(self):
        # trust nothing: re-verify at the click, refuse + repaint if a
        # permission regressed since the last tick
        st = self.be.statuses(self.cfg)
        if (not all(st[k] for k in self.be.GATING)
                or not self._models_gate(st)):
            self.tick(loop=False)
            return
        open(firstrun.PENDING_MARKER, "w").close()
        self._closed = True
        self.root.destroy()
        self.be.relaunch()

    def close(self):
        self._closed = True
        self.root.destroy()


def launch(cfg, backend=None):
    """Show the walkthrough; owns the process. Returns only if the user
    closes the window (the app then exits, like macOS)."""
    w = _Walkthrough(cfg, backend)
    w.tick()
    w.root.mainloop()


class _DownloadScreen:
    def __init__(self, cfg, backend=None):
        import tkinter as tk
        from tkinter import ttk
        self.cfg = cfg
        self.be = backend or _backend()
        self.q = queue.Queue()
        root = self.root = tk.Tk()
        root.title("Setting up Sotto")
        root.resizable(False, False)
        tk.Label(root, text="Downloading Sotto's models",
                 font=("", 14, "bold")).grid(
            row=0, column=0, sticky="w", padx=18, pady=(14, 2))
        tk.Label(root, text="One-time setup (~3–4 GB). Sotto starts by "
                            "itself when this finishes.",
                 fg="#555").grid(row=1, column=0, sticky="w", padx=18)
        self.bar = ttk.Progressbar(root, length=380, mode="determinate",
                                   maximum=1.0)
        self.bar.grid(row=2, column=0, padx=18, pady=(12, 4), sticky="we")
        self.status = tk.Label(root, text="starting…", fg="#555")
        self.status.grid(row=3, column=0, sticky="w", padx=18, pady=(0, 10))
        self.retry = tk.Button(root, text="Retry", command=self.begin)
        self.retry.grid(row=4, column=0, pady=(0, 12))
        self.retry.grid_remove()
        self._busy = False
        root.protocol("WM_DELETE_WINDOW", self._quit)

    def _quit(self):
        self.root.destroy()

    def begin(self):
        # guard against a double-click on Retry queuing two runs before the
        # button unmaps → two worker threads and two done-sentinels
        if self._busy:
            return
        self._busy = True
        self.retry.grid_remove()
        self.status.config(text="starting…")

        def progress(label, frac):
            self.q.put((label, frac))

        def work():
            try:
                if self.be.engine_missing(self.cfg):
                    from . import ollama_runtime
                    progress("downloading cleanup engine…", None)
                    ollama_runtime.download(
                        lambda f: progress(f"cleanup engine: {int(f*100)}%", f))
            except Exception as e:
                log.warning("engine download failed: %s", e)
                progress(f"download failed: {e}", None)
                self.q.put(("__done__", None))
                return
            # ASR + LLM pull (spawns its own worker; calls back on done)
            firstrun.download_models(self.cfg, progress,
                                     lambda: self.q.put(("__done__", None)))

        threading.Thread(target=work, daemon=True).start()
        self._pump()  # arm the drain loop for this run

    def _pump(self):
        if self.drain():
            self.root.after(100, self._pump)

    def drain(self) -> bool:
        """Consume queued progress. Returns True to keep polling, False when
        the run finished — a later Retry re-arms polling through begin()."""
        try:
            while True:
                label, frac = self.q.get_nowait()
                if label == "__done__":
                    self._busy = False
                    self.finish()
                    return False
                self.status.config(text=label)
                if frac is not None:
                    self.bar["value"] = frac
        except queue.Empty:
            pass
        return True

    def finish(self):
        # only relaunch when everything is really here — a failed download
        # must show Retry, never loop the app through relaunch forever
        if self.be.setup_missing(self.cfg):
            self.retry.grid()
            return
        self.root.destroy()
        self.be.relaunch()


def download_screen(cfg, backend=None):
    """Download engine + models with progress; owns the process; relaunches
    into a normal start when complete."""
    s = _DownloadScreen(cfg, backend)
    s.begin()
    s.root.mainloop()
