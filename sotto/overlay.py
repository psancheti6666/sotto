"""On-screen feedback: a small floating capsule at the bottom-center of the screen.

Modes:
- listening:  compact live waveform (hold-to-talk — the user's finger is on the
              key, so no buttons; Escape cancels). Click-through.
- handsfree:  slightly larger: ✕ button | waveform | ✓ button, clickable, plus
              an amber countdown when the dictation limit is near.
- processing: a dot spinner until the cleaned text is injected.
- cancelled:  a larger "Transcript cancelled  [Undo]" toast with a progress
              line running left→right; when it completes the audio is dropped
              and the toast fades out. Clicking Undo transcribes anyway.

The panel is borderless and non-activating (clicks never steal focus from the
app being dictated into). All AppKit mutations are dispatched to the main
thread; the AppKit run loop must own the main thread (see run_forever()).
"""

import collections
import math
import signal
import time

import objc
from AppKit import (
    NSApplication,
    NSBezierPath,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMakeRect,
    NSOperationQueue,
    NSPanel,
    NSScreen,
    NSTimer,
    NSView,
)
from Foundation import NSString

_STYLE = 0 | (1 << 7)          # borderless | non-activating panel
_BACKING_BUFFERED = 2
_LEVEL_STATUS = 25             # NSStatusWindowLevel
_ALL_SPACES = 1 | 16 | 256     # canJoinAllSpaces | stationary | fullScreenAuxiliary
_ACCESSORY = 1                 # NSApplicationActivationPolicyAccessory

# capsule size per mode
SIZES = {
    "listening": (110, 24),
    "handsfree": (170, 26),
    "processing": (110, 24),
    "cancelled": (240, 32),
}
# modes that need mouse clicks; the rest stay click-through
_CLICKABLE = {"handsfree", "cancelled"}
BARS = 12
FPS = 20.0
BTN_R = 9.0                    # ✕ / ✓ button radius
FADE_S = 0.35                  # toast fade-out duration


def _on_main(fn):
    NSOperationQueue.mainQueue().addOperationWithBlock_(fn)


class _IndicatorView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(_IndicatorView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.mode = "hidden"          # hidden | listening | processing | cancelled
        self.levels = collections.deque([0.0] * BARS, maxlen=BARS)
        self.phase = 0
        self.level_supplier = None
        self.remaining_supplier = None
        self.warn_remaining_s = 60.0
        self.remaining = None         # seconds left, when in warning range
        self.toast_started = 0.0
        self.toast_duration = 3.0
        self.toast_expired = False
        self.undo_rect = (0, 0, 0, 0)
        # callbacks wired by Overlay
        self.on_cancel_click = None
        self.on_done_click = None
        self.on_undo_click = None
        self.on_toast_expire = None
        return self

    def acceptsFirstMouse_(self, _event):
        return True  # buttons work with a single click, without activating us

    # ------------------------------------------------------------------ ticking
    def tick_(self, _timer):
        if self.mode == "hidden":
            return
        if self.mode in ("listening", "handsfree"):
            if self.level_supplier is not None:
                self.levels.append(min(1.0, float(self.level_supplier()) * 14.0))
            rem = self.remaining_supplier() if self.remaining_supplier else None
            self.remaining = rem if (rem is not None and rem <= self.warn_remaining_s) else None
        elif self.mode == "cancelled":
            elapsed = time.monotonic() - self.toast_started
            left = self.toast_duration - elapsed
            self.window().setAlphaValue_(max(0.0, min(1.0, left / FADE_S)))
            if left <= 0 and not self.toast_expired:
                self.toast_expired = True
                if self.on_toast_expire:
                    self.on_toast_expire()
                return
        self.phase = (self.phase + 1) % 8
        self.setNeedsDisplay_(True)

    # ------------------------------------------------------------------ drawing
    def drawRect_(self, _rect):
        b = self.bounds()
        capsule = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            b, b.size.height / 2.0, b.size.height / 2.0)
        NSColor.colorWithCalibratedWhite_alpha_(0.04, 0.88).setFill()
        capsule.fill()
        if self.mode == "listening":
            self._draw_waveform(b, buttons=False)
        elif self.mode == "handsfree":
            self._draw_waveform(b, buttons=True)
        elif self.mode == "processing":
            self._draw_spinner(b)
        elif self.mode == "cancelled":
            self._draw_cancelled(b)

    def _draw_waveform(self, b, buttons: bool):
        h = b.size.height
        if buttons:
            self._draw_x_button(14.0, h / 2.0)
            self._draw_check_button(b.size.width - 14.0, h / 2.0)

        warning = self.remaining is not None
        bar_w, gap = 2.0, 2.0
        total = BARS * (bar_w + gap) - gap
        x = (b.size.width - total) / 2.0 - (12.0 if warning else 0.0)
        color = (NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.72, 0.2, 0.95)
                 if warning else NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.92))
        color.setFill()
        for lvl in self.levels:
            bh = 2.5 + lvl * (h - 13.0)
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(x, (h - bh) / 2.0, bar_w, bh), bar_w / 2.0, bar_w / 2.0).fill()
            x += bar_w + gap
        if warning:
            self._draw_text(f"{max(0, int(self.remaining))}s", 9.0, color,
                            right=b.size.width - 28.0, center_y=h / 2.0)

    def _draw_x_button(self, cx, cy):
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.22).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(cx - BTN_R, cy - BTN_R, BTN_R * 2, BTN_R * 2)).fill()
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.95).setStroke()
        d = 3.2
        for sx in (-1, 1):
            p = NSBezierPath.bezierPath()
            p.setLineWidth_(1.6)
            p.moveToPoint_((cx - d * sx, cy - d))
            p.lineToPoint_((cx + d * sx, cy + d))
            p.stroke()

    def _draw_check_button(self, cx, cy):
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.95).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(cx - BTN_R, cy - BTN_R, BTN_R * 2, BTN_R * 2)).fill()
        NSColor.colorWithCalibratedWhite_alpha_(0.08, 1.0).setStroke()
        p = NSBezierPath.bezierPath()
        p.setLineWidth_(1.8)
        p.moveToPoint_((cx - 3.6, cy - 0.2))
        p.lineToPoint_((cx - 1.0, cy - 3.0))
        p.lineToPoint_((cx + 4.0, cy + 3.0))
        p.stroke()

    def _draw_spinner(self, b):
        cx, cy, ring, dot = b.size.width / 2.0, b.size.height / 2.0, 5.5, 1.7
        for i in range(8):
            angle = math.pi / 2.0 - (i * math.pi / 4.0)
            alpha = 0.15 + 0.85 * (((i + self.phase) % 8) / 7.0)
            NSColor.colorWithCalibratedWhite_alpha_(1.0, alpha).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(cx + ring * math.cos(angle) - dot,
                           cy + ring * math.sin(angle) - dot,
                           dot * 2, dot * 2)).fill()

    def _draw_cancelled(self, b):
        w, h = b.size.width, b.size.height
        white = NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.95)
        self._draw_text("Transcript cancelled", 12.0, white, left=16.0, center_y=h / 2.0)
        # Undo pill
        uw, uh = 54.0, 20.0
        ux, uy = w - uw - 10.0, (h - uh) / 2.0
        self.undo_rect = (ux, uy, uw, uh)
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.18).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(ux, uy, uw, uh), uh / 2.0, uh / 2.0).fill()
        self._draw_text("Undo", 11.0, white, center_x=ux + uw / 2.0, center_y=h / 2.0)
        # progress line, left → right along the bottom
        frac = min(1.0, (time.monotonic() - self.toast_started) / self.toast_duration)
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.55).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(h / 2.0, 2.0, (w - h) * frac, 2.0), 1.0, 1.0).fill()

    def _draw_text(self, s, size, color, left=None, right=None, center_x=None, center_y=0.0):
        text = NSString.stringWithString_(s)
        attrs = {NSFontAttributeName: NSFont.systemFontOfSize_(size),
                 NSForegroundColorAttributeName: color}
        ts = text.sizeWithAttributes_(attrs)
        if center_x is not None:
            x = center_x - ts.width / 2.0
        elif right is not None:
            x = right - ts.width
        else:
            x = left
        text.drawAtPoint_withAttributes_((x, center_y - ts.height / 2.0), attrs)

    # ------------------------------------------------------------------ clicks
    def mouseDown_(self, event):
        p = self.convertPoint_fromView_(event.locationInWindow(), None)
        b = self.bounds()
        if self.mode == "handsfree":
            if math.hypot(p.x - 14.0, p.y - b.size.height / 2.0) <= BTN_R + 3:
                if self.on_cancel_click:
                    self.on_cancel_click()
            elif math.hypot(p.x - (b.size.width - 14.0), p.y - b.size.height / 2.0) <= BTN_R + 3:
                if self.on_done_click:
                    self.on_done_click()
        elif self.mode == "cancelled" and not self.toast_expired:
            ux, uy, uw, uh = self.undo_rect
            if ux - 4 <= p.x <= ux + uw + 4 and uy - 4 <= p.y <= uy + uh + 4:
                if self.on_undo_click:
                    self.on_undo_click()


class Overlay:
    """Thread-safe facade; construct on the main thread before run_forever()."""

    def __init__(self, level_supplier, offset_y: float = 6.0,
                 remaining_supplier=None, warn_remaining_s: float = 60.0,
                 on_cancel_click=None, on_done_click=None,
                 on_undo_click=None, on_cancel_expire=None):
        self.offset_y = offset_y
        self._on_cancel_expire = on_cancel_expire
        w, h = SIZES["listening"]
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, w, h), _STYLE, _BACKING_BUFFERED, False)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setLevel_(_LEVEL_STATUS)
        panel.setIgnoresMouseEvents_(True)    # per-mode: see _set_mode
        panel.setHasShadow_(True)
        panel.setCollectionBehavior_(_ALL_SPACES)
        view = _IndicatorView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        view.level_supplier = level_supplier
        view.remaining_supplier = remaining_supplier
        view.warn_remaining_s = warn_remaining_s
        view.on_cancel_click = on_cancel_click
        view.on_done_click = on_done_click
        view.on_undo_click = on_undo_click
        view.on_toast_expire = self._toast_expired
        panel.setContentView_(view)
        self.panel, self.view = panel, view
        # The animation timer runs ONLY while the capsule is visible; an idle,
        # hidden Sotto must not wake the CPU 20×/s. Managed on the main thread.
        self._timer = None

    def _start_timer(self):
        if self._timer is None:
            self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1.0 / FPS, self.view, "tick:", None, True)

    def _stop_timer(self):
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None

    def _place(self, mode: str):
        w, h = SIZES.get(mode, SIZES["processing"])
        screen = NSScreen.mainScreen()
        if screen is None:
            screens = NSScreen.screens()
            if not screens:
                return
            screen = screens[0]
        f = screen.frame()
        self.panel.setFrame_display_(
            NSMakeRect(f.origin.x + (f.size.width - w) / 2.0,
                       f.origin.y + self.offset_y, w, h), True)

    def _set_mode(self, mode: str, toast_duration: float = 3.0):
        def go():
            self.view.mode = mode
            self.panel.setIgnoresMouseEvents_(mode not in _CLICKABLE)
            if mode == "hidden":
                self._stop_timer()          # idle: no CPU wakeups
                self.panel.orderOut_(None)
                return
            if mode == "listening":
                self.view.levels.extend([0.0] * BARS)
            if mode == "cancelled":
                self.view.toast_started = time.monotonic()
                self.view.toast_duration = toast_duration
                self.view.toast_expired = False
            # Re-assert level + all-Spaces behavior on every show: a long-idle
            # process can have these dropped by the window server, which is what
            # made the capsule stop floating over full-screen apps after hours.
            self.panel.setLevel_(_LEVEL_STATUS)
            self.panel.setCollectionBehavior_(_ALL_SPACES)
            self.panel.setAlphaValue_(1.0)
            self._place(mode)
            self._start_timer()
            self.panel.orderFrontRegardless()
        _on_main(go)

    def _toast_expired(self):
        self._set_mode("hidden")
        if self._on_cancel_expire:
            self._on_cancel_expire()

    def show_listening(self):
        self._set_mode("listening")

    def show_handsfree(self):
        self._set_mode("handsfree")

    def show_processing(self):
        self._set_mode("processing")

    def show_cancelled(self, duration_s: float = 3.0):
        self._set_mode("cancelled", toast_duration=duration_s)

    def hide(self):
        self._set_mode("hidden")


def run_forever():
    """Own the main thread with the AppKit run loop (Ctrl+C exits)."""
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(_ACCESSORY)
    signal.signal(signal.SIGINT, lambda *_: app.terminate_(None))
    app.run()
