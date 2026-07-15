"""On-screen feedback: a small floating capsule at the bottom-center of the screen.

Listening: thin waveform bars animated by the live mic level.
Processing: a dot spinner until the cleaned text is injected.

The panel is borderless, non-activating (never steals focus from the app being
dictated into), click-through, and shown on all Spaces. All AppKit mutations are
dispatched to the main thread; the AppKit run loop must own the main thread
(see run_forever()).
"""

import collections
import math
import signal

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
    NSSound,
    NSTimer,
    NSView,
)
from Foundation import NSString

_STYLE = 0 | (1 << 7)          # borderless | non-activating panel
_BACKING_BUFFERED = 2
_LEVEL_STATUS = 25             # NSStatusWindowLevel
_ALL_SPACES = 1 | 16 | 256     # canJoinAllSpaces | stationary | fullScreenAuxiliary
_ACCESSORY = 1                 # NSApplicationActivationPolicyAccessory

WIDTH, HEIGHT = 110, 24
BARS = 12
FPS = 20.0


def _on_main(fn):
    NSOperationQueue.mainQueue().addOperationWithBlock_(fn)


def play_sound(name: str):
    def go():
        snd = NSSound.soundNamed_(name)
        if snd is not None:
            snd.play()
    _on_main(go)


def haptic():
    try:
        from AppKit import NSHapticFeedbackManager
        _on_main(lambda: NSHapticFeedbackManager.defaultPerformer()
                 .performFeedbackPattern_performanceTime_(0, 0))
    except Exception:
        pass


class _IndicatorView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(_IndicatorView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.mode = "hidden"                    # hidden | listening | processing
        self.levels = collections.deque([0.0] * BARS, maxlen=BARS)
        self.phase = 0
        self.level_supplier = None
        self.remaining_supplier = None
        self.warn_remaining_s = 60.0
        self.remaining = None                   # seconds left, when in warning range
        return self

    def tick_(self, _timer):
        if self.mode == "hidden":
            return
        if self.mode == "listening":
            if self.level_supplier is not None:
                self.levels.append(min(1.0, float(self.level_supplier()) * 14.0))
            rem = self.remaining_supplier() if self.remaining_supplier else None
            self.remaining = rem if (rem is not None and rem <= self.warn_remaining_s) else None
        self.phase = (self.phase + 1) % 8
        self.setNeedsDisplay_(True)

    def drawRect_(self, _rect):
        b = self.bounds()
        capsule = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            b, b.size.height / 2.0, b.size.height / 2.0)
        NSColor.colorWithCalibratedWhite_alpha_(0.04, 0.88).setFill()
        capsule.fill()
        if self.mode == "listening":
            self._draw_bars(b)
        elif self.mode == "processing":
            self._draw_spinner(b)

    def _draw_bars(self, b):
        warning = self.remaining is not None
        bar_w, gap = 2.0, 2.0
        total = BARS * (bar_w + gap) - gap
        # In the final minute, bars shift left and turn amber; the countdown
        # takes the right side of the capsule.
        x = (b.size.width - total) / 2.0 - (14.0 if warning else 0.0)
        color = (NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.72, 0.2, 0.95)
                 if warning else NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.92))
        color.setFill()
        for lvl in self.levels:
            h = 2.5 + lvl * (b.size.height - 11.0)
            y = (b.size.height - h) / 2.0
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(x, y, bar_w, h), bar_w / 2.0, bar_w / 2.0).fill()
            x += bar_w + gap
        if warning:
            text = NSString.stringWithString_(f"{max(0, int(self.remaining))}s")
            attrs = {NSFontAttributeName: NSFont.monospacedDigitSystemFontOfSize_weight_(9.0, 0.3),
                     NSForegroundColorAttributeName: color}
            size = text.sizeWithAttributes_(attrs)
            text.drawAtPoint_withAttributes_(
                (b.size.width - size.width - 10.0, (b.size.height - size.height) / 2.0), attrs)

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


class Overlay:
    """Thread-safe facade; construct on the main thread before run_forever()."""

    def __init__(self, level_supplier, offset_y: float = 6.0,
                 remaining_supplier=None, warn_remaining_s: float = 60.0):
        self.offset_y = offset_y
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIDTH, HEIGHT), _STYLE, _BACKING_BUFFERED, False)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setLevel_(_LEVEL_STATUS)
        panel.setIgnoresMouseEvents_(True)
        panel.setHasShadow_(True)
        panel.setCollectionBehavior_(_ALL_SPACES)
        view = _IndicatorView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH, HEIGHT))
        view.level_supplier = level_supplier
        view.remaining_supplier = remaining_supplier
        view.warn_remaining_s = warn_remaining_s
        panel.setContentView_(view)
        self.panel, self.view = panel, view
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0 / FPS, view, "tick:", None, True)

    def _place(self):
        screen = NSScreen.mainScreen()
        if screen is None:
            screens = NSScreen.screens()
            if not screens:
                return
            screen = screens[0]
        # Absolute bottom edge of the screen (the panel floats above the Dock).
        f = screen.frame()
        self.panel.setFrameOrigin_((f.origin.x + (f.size.width - WIDTH) / 2.0,
                                    f.origin.y + self.offset_y))

    def _set_mode(self, mode: str):
        def go():
            self.view.mode = mode
            if mode == "hidden":
                self.panel.orderOut_(None)
            else:
                if mode == "listening":
                    self.view.levels.extend([0.0] * BARS)
                self._place()
                self.panel.orderFrontRegardless()
        _on_main(go)

    def show_listening(self):
        self._set_mode("listening")

    def show_processing(self):
        self._set_mode("processing")

    def hide(self):
        self._set_mode("hidden")


def run_forever():
    """Own the main thread with the AppKit run loop (Ctrl+C exits)."""
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(_ACCESSORY)
    signal.signal(signal.SIGINT, lambda *_: app.terminate_(None))
    app.run()
