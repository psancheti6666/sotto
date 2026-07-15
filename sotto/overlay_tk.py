"""On-screen feedback for Linux: a tkinter port of overlay.py's capsule.

Same public surface and modes as the AppKit overlay (listening / handsfree /
processing / cancelled with Undo). tkinter is stdlib, works on X11 and — via
XWayland — on GNOME/KDE Wayland sessions, and its mainloop owns the main
thread exactly like the AppKit run loop does on macOS.

Divergences from the AppKit capsule (accepted): whole-window transparency
only (rounded corners show a dark square on non-compositing WMs), and the
listening capsule is not truly click-through (it has no click handlers, but
a click can land on the window itself).

All public methods are thread-safe: they enqueue commands that the tk main
thread applies on its next tick. Raises OverlayUnavailable when no display
(or python3-tk) is present — the app then runs headless.
"""

import collections
import math
import queue
import signal
import time

SIZES = {
    "listening": (110, 24),
    "handsfree": (170, 26),
    "processing": (110, 24),
    "cancelled": (240, 32),
}
BARS = 12
TICK_MS = 50               # 20 fps
BTN_R = 9.0                # ✕ / ✓ button radius
FADE_S = 0.35              # toast fade-out duration

BG = "#0d0d0d"
FG = "#ececec"
AMBER = "#ffb833"
BTN_X_BG = "#3a3a3a"
BTN_CHECK_BG = "#f2f2f2"
PILL_BG = "#2f2f2f"
LINE = "#8c8c8c"

_root = None  # singleton tk.Tk, created by Overlay, run by run_forever()


class OverlayUnavailable(Exception):
    pass


class Overlay:
    """Thread-safe facade; construct on the main thread before run_forever()."""

    def __init__(self, level_supplier, offset_y: float = 6.0,
                 remaining_supplier=None, warn_remaining_s: float = 60.0,
                 on_cancel_click=None, on_done_click=None,
                 on_undo_click=None, on_cancel_expire=None):
        global _root
        try:
            import tkinter as tk
        except ImportError as e:
            raise OverlayUnavailable("tkinter is not installed (python3-tk)") from e
        try:
            root = tk.Tk()
        except tk.TclError as e:
            raise OverlayUnavailable(f"no display: {e}") from e
        self._tk = tk
        self._root = _root = root
        self.offset_y = offset_y
        self.level_supplier = level_supplier
        self.remaining_supplier = remaining_supplier
        self.warn_remaining_s = warn_remaining_s
        self.on_cancel_click = on_cancel_click
        self.on_done_click = on_done_click
        self.on_undo_click = on_undo_click
        self.on_cancel_expire = on_cancel_expire

        self.mode = "hidden"
        self.levels = collections.deque([0.0] * BARS, maxlen=BARS)
        self.phase = 0
        self.remaining = None
        self.toast_started = 0.0
        self.toast_duration = 3.0
        self.toast_expired = False
        self.undo_rect = (0, 0, 0, 0)
        self._cmds: queue.Queue = queue.Queue()

        root.withdraw()
        root.overrideredirect(True)
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        w, h = SIZES["listening"]
        self.canvas = tk.Canvas(root, width=w, height=h, bg=BG,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_click)
        root.after(TICK_MS, self._tick)

    # ------------------------------------------------------------ public (any thread)
    def show_listening(self):
        self._cmds.put(("listening", None))

    def show_handsfree(self):
        self._cmds.put(("handsfree", None))

    def show_processing(self):
        self._cmds.put(("processing", None))

    def show_cancelled(self, duration_s: float = 3.0):
        self._cmds.put(("cancelled", duration_s))

    def hide(self):
        self._cmds.put(("hidden", None))

    # ------------------------------------------------------------ tk main thread
    def _apply(self, mode, duration):
        self.mode = mode
        if mode == "hidden":
            self._root.withdraw()
            return
        if mode == "listening":
            self.levels.extend([0.0] * BARS)
        if mode == "cancelled":
            self.toast_started = time.monotonic()
            self.toast_duration = duration or 3.0
            self.toast_expired = False
        self._set_alpha(1.0)
        self._place(mode)
        self._root.deiconify()
        self._root.lift()

    def _place(self, mode):
        w, h = SIZES.get(mode, SIZES["processing"])
        sw = self._root.winfo_screenwidth()
        sh = self._root.winfo_screenheight()
        x = (sw - w) // 2
        y = sh - h - int(self.offset_y)
        self._root.geometry(f"{w}x{h}+{x}+{y}")
        self.canvas.configure(width=w, height=h)

    def _set_alpha(self, value):
        try:
            self._root.attributes("-alpha", max(0.0, min(1.0, value)))
        except self._tk.TclError:
            pass  # compositor-less WM: no transparency, toast just disappears

    def _tick(self):
        try:
            while True:
                mode, duration = self._cmds.get_nowait()
                self._apply(mode, duration)
        except queue.Empty:
            pass
        if self.mode in ("listening", "handsfree"):
            if self.level_supplier is not None:
                self.levels.append(min(1.0, float(self.level_supplier()) * 14.0))
            rem = self.remaining_supplier() if self.remaining_supplier else None
            self.remaining = rem if (rem is not None and rem <= self.warn_remaining_s) else None
        elif self.mode == "cancelled":
            left = self.toast_duration - (time.monotonic() - self.toast_started)
            self._set_alpha(left / FADE_S)
            if left <= 0 and not self.toast_expired:
                self.toast_expired = True
                self._apply("hidden", None)
                if self.on_cancel_expire:
                    self.on_cancel_expire()
        self.phase = (self.phase + 1) % 8
        if self.mode != "hidden":
            self._redraw()
        self._root.after(TICK_MS, self._tick)

    # ------------------------------------------------------------ drawing
    def _redraw(self):
        c = self.canvas
        c.delete("all")
        w, h = SIZES.get(self.mode, SIZES["processing"])
        if self.mode == "listening":
            self._draw_waveform(w, h, buttons=False)
        elif self.mode == "handsfree":
            self._draw_waveform(w, h, buttons=True)
        elif self.mode == "processing":
            self._draw_spinner(w, h)
        elif self.mode == "cancelled":
            self._draw_cancelled(w, h)

    def _draw_waveform(self, w, h, buttons):
        c = self.canvas
        if buttons:
            self._draw_x_button(14.0, h / 2.0)
            self._draw_check_button(w - 14.0, h / 2.0)
        warning = self.remaining is not None
        color = AMBER if warning else FG
        bar_w, gap = 2.0, 2.0
        total = BARS * (bar_w + gap) - gap
        x = (w - total) / 2.0 - (12.0 if warning else 0.0)
        for lvl in self.levels:
            bh = 2.5 + lvl * (h - 13.0)
            y0 = (h - bh) / 2.0
            c.create_rectangle(x, y0, x + bar_w, y0 + bh, fill=color, outline="")
            x += bar_w + gap
        if warning:
            c.create_text(w - 28.0, h / 2.0, text=f"{max(0, int(self.remaining))}s",
                          fill=AMBER, font=("TkDefaultFont", 9), anchor="e")

    def _draw_x_button(self, cx, cy):
        c = self.canvas
        c.create_oval(cx - BTN_R, cy - BTN_R, cx + BTN_R, cy + BTN_R,
                      fill=BTN_X_BG, outline="")
        d = 3.2
        c.create_line(cx - d, cy - d, cx + d, cy + d, fill=FG, width=1.6)
        c.create_line(cx + d, cy - d, cx - d, cy + d, fill=FG, width=1.6)

    def _draw_check_button(self, cx, cy):
        c = self.canvas
        c.create_oval(cx - BTN_R, cy - BTN_R, cx + BTN_R, cy + BTN_R,
                      fill=BTN_CHECK_BG, outline="")
        c.create_line(cx - 3.6, cy + 0.2, cx - 1.0, cy + 3.0, cx + 4.0, cy - 3.0,
                      fill="#141414", width=1.8)

    def _draw_spinner(self, w, h):
        c = self.canvas
        cx, cy, ring, dot = w / 2.0, h / 2.0, 5.5, 1.7
        for i in range(8):
            angle = math.pi / 2.0 - (i * math.pi / 4.0)
            level = 0.15 + 0.85 * (((i + self.phase) % 8) / 7.0)
            v = int(20 + level * 215)
            color = f"#{v:02x}{v:02x}{v:02x}"
            x = cx + ring * math.cos(angle)
            y = cy - ring * math.sin(angle)
            c.create_oval(x - dot, y - dot, x + dot, y + dot, fill=color, outline="")

    def _draw_cancelled(self, w, h):
        c = self.canvas
        c.create_text(16.0, h / 2.0, text="Transcript cancelled", fill=FG,
                      font=("TkDefaultFont", 11), anchor="w")
        uw, uh = 54.0, 20.0
        ux, uy = w - uw - 10.0, (h - uh) / 2.0
        self.undo_rect = (ux, uy, uw, uh)
        c.create_oval(ux, uy, ux + uh, uy + uh, fill=PILL_BG, outline="")
        c.create_oval(ux + uw - uh, uy, ux + uw, uy + uh, fill=PILL_BG, outline="")
        c.create_rectangle(ux + uh / 2, uy, ux + uw - uh / 2, uy + uh,
                           fill=PILL_BG, outline="")
        c.create_text(ux + uw / 2.0, h / 2.0, text="Undo", fill=FG,
                      font=("TkDefaultFont", 10))
        frac = min(1.0, (time.monotonic() - self.toast_started) / self.toast_duration)
        c.create_rectangle(h / 2.0, h - 4.0, h / 2.0 + (w - h) * frac, h - 2.0,
                           fill=LINE, outline="")

    # ------------------------------------------------------------ clicks
    def _on_click(self, event):
        w, h = SIZES.get(self.mode, SIZES["processing"])
        if self.mode == "handsfree":
            if math.hypot(event.x - 14.0, event.y - h / 2.0) <= BTN_R + 3:
                if self.on_cancel_click:
                    self.on_cancel_click()
            elif math.hypot(event.x - (w - 14.0), event.y - h / 2.0) <= BTN_R + 3:
                if self.on_done_click:
                    self.on_done_click()
        elif self.mode == "cancelled" and not self.toast_expired:
            ux, uy, uw, uh = self.undo_rect
            if ux - 4 <= event.x <= ux + uw + 4 and uy - 4 <= event.y <= uy + uh + 4:
                if self.on_undo_click:
                    self.on_undo_click()


def run_forever():
    """Own the main thread with the tk mainloop (Ctrl+C exits)."""
    signal.signal(signal.SIGINT, lambda *_: _root.destroy())
    _root.mainloop()
