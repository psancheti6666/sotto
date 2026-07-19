# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Global push-to-talk hotkey.

Behavior:
- Hold the key to dictate; release inserts the cleaned text.
- While holding, press SPACE to switch to hands-free (the space is swallowed,
  Wispr-style); press the hotkey again to stop. Double-tap also enters hands-free.
- If any OTHER key is pressed while holding, the hotkey is being used as a normal
  modifier combo (e.g. Option+Delete) — dictation is cancelled and discarded.

`fn` cannot be intercepted by pynput on macOS, so pick a modifier (alt_r, cmd_r,
ctrl) or an F-key ("f5", "f13", …) in config.

Windows (docs/windows-app.md W2): pynput's listener is a WH_KEYBOARD_LL hook
there, and its win32_event_filter gives the same selective swallowing that
darwin_intercept gives on macOS — _win32_filter below mirrors
_darwin_intercept event for event, so the full macOS gesture set carries over.
"""

import sys
import time

_SPACE_KEYCODE = 49          # macOS virtual keycode for space
_ESCAPE_KEYCODE = 53         # macOS virtual keycode for escape
_KEY_DOWN, _KEY_UP = 10, 11  # kCGEventKeyDown / kCGEventKeyUp

# Windows: WH_KEYBOARD_LL message ids + virtual-key codes (Alt-modified keys
# arrive as WM_SYSKEY*)
_WM_KEYDOWN, _WM_SYSKEYDOWN = 0x0100, 0x0104
_WM_KEYUP, _WM_SYSKEYUP = 0x0101, 0x0105
_VK_SPACE, _VK_ESCAPE = 0x20, 0x1B
_LLKHF_INJECTED = 0x10       # KBDLLHOOKSTRUCT.flags: event came from SendInput


class HotkeyListener:
    def __init__(self, key_name: str, on_start, on_stop,
                 tap_max_s: float = 0.3, double_tap_window_s: float = 0.5,
                 on_handsfree=None, on_cancel=None):
        if key_name == "fn":
            self._key, self._vk = None, 63  # handled by FnHotkeyListener
        else:
            from pynput import keyboard  # lazy: pynput only on macOS/Windows
            self._key = getattr(keyboard.Key, key_name, None) or keyboard.KeyCode.from_char(key_name)
            self._vk = getattr(self._key, "value", self._key).vk
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_handsfree = on_handsfree
        self._on_cancel = on_cancel
        self._tap_max = tap_max_s
        self._double_tap_window = double_tap_window_s

        self._down = False          # hotkey physically held
        self._active = False        # capturing audio
        self._toggle = False        # hands-free mode
        self._press_time = 0.0
        self._last_tap = 0.0
        self._consume_release = False
        self._swallow_space_up = False
        self._swallow_esc_up = False

    def _matches(self, key) -> bool:
        return key == self._key

    def _enter_handsfree(self):
        self._toggle = True
        if self._on_handsfree:
            self._on_handsfree()

    def force_stop(self):
        """Stop and process from outside the key handlers (time limit, ✓ button)."""
        if not self._active:
            return
        self._toggle = False
        self._active = False
        if self._down:
            self._consume_release = True
        self._on_stop()

    def cancel(self):
        """Cancel dictation (Escape or the ✕ button). The app decides what to do
        with the captured audio (it offers an Undo window)."""
        if not self._active:
            return
        self._toggle = False
        self._active = False
        if self._down:
            self._consume_release = True
        if self._on_cancel:
            self._on_cancel()
        else:
            self._on_stop(discard=True)

    def _cancel_combo(self):
        """Hotkey is being used as a modifier for a shortcut — discard dictation."""
        self._active = False
        self._toggle = False
        self._on_stop(discard=True)

    def _hotkey_press(self):
        now = time.monotonic()
        if self._active and self._toggle:
            # Press during hands-free: stop and process.
            self._toggle = False
            self._active = False
            self._consume_release = True
            self._on_stop()
            return
        if not self._active:
            self._active = True
            self._press_time = now
            was_double_tap = now - self._last_tap < self._double_tap_window
            self._on_start()
            if was_double_tap:
                self._enter_handsfree()

    def _hotkey_release(self):
        now = time.monotonic()
        if self._consume_release:
            self._consume_release = False
            return
        if not self._active or self._toggle:
            return
        held = now - self._press_time
        if held < self._tap_max:
            # Quick tap: discard (too short to be speech); may be first of a double-tap.
            self._active = False
            self._last_tap = now
            self._on_stop(discard=True)
        else:
            self._active = False
            self._on_stop()

    def _on_press(self, key):
        if not self._matches(key):
            return
        if self._down:
            return  # key autorepeat
        self._down = True
        self._hotkey_press()

    def _on_release(self, key):
        if not self._matches(key):
            return
        self._down = False
        self._hotkey_release()

    def _darwin_intercept(self, event_type, event):
        """Runs inside the CGEventTap. Swallows space during hold (→ hands-free)
        and cancels dictation when the hotkey is used in a combo. Must be fast
        and never raise, or macOS disables the tap."""
        try:
            if event_type not in (_KEY_DOWN, _KEY_UP):
                return event
            import Quartz
            keycode = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode)
            if keycode == _ESCAPE_KEYCODE:
                if self._swallow_esc_up and event_type == _KEY_UP:
                    self._swallow_esc_up = False
                    return None
                if event_type == _KEY_DOWN and self._active:
                    self._swallow_esc_up = True
                    self.cancel()
                    return None
                return event
            if keycode == _SPACE_KEYCODE:
                if self._swallow_space_up and event_type == _KEY_UP:
                    self._swallow_space_up = False
                    return None
                if (event_type == _KEY_DOWN and self._down
                        and self._active and not self._toggle):
                    self._swallow_space_up = True
                    self._enter_handsfree()
                    return None
                return event
            if (event_type == _KEY_DOWN and self._down and self._active
                    and not self._toggle and keycode != self._vk):
                self._cancel_combo()
            return event
        except Exception:
            return event

    def _win32_filter(self, msg, data):
        """Runs inside the WH_KEYBOARD_LL hook (pynput win32_event_filter) —
        the Windows mirror of _darwin_intercept. suppress_event() RAISES to
        block an event system-wide (pynput then also skips its callbacks for
        it, matching the intercept returning None), so it must never sit
        inside a try/except; any OTHER exception escaping the filter kills
        the listener, so everything else is guarded."""
        try:
            down = msg in (_WM_KEYDOWN, _WM_SYSKEYDOWN)
            up = msg in (_WM_KEYUP, _WM_SYSKEYUP)
            if not (down or up):
                return
            if data.flags & _LLKHF_INJECTED:
                return  # our own SendInput typing — never feed it back
            vk = data.vkCode
            swallow = False
            if vk == _VK_ESCAPE:
                if self._swallow_esc_up and up:
                    self._swallow_esc_up = False
                    swallow = True
                elif down and self._active:
                    self._swallow_esc_up = True
                    self.cancel()
                    swallow = True
            elif vk == _VK_SPACE:
                if self._swallow_space_up and up:
                    self._swallow_space_up = False
                    swallow = True
                elif (down and self._down and self._active
                        and not self._toggle):
                    self._swallow_space_up = True
                    self._enter_handsfree()
                    swallow = True
            elif (down and self._down and self._active and not self._toggle
                    and vk != self._vk):
                self._cancel_combo()  # hotkey used as a modifier — passes through
        except Exception:
            return
        if swallow:
            self._listener.suppress_event()

    def run(self):
        from pynput import keyboard
        kwargs = {}
        if sys.platform == "darwin":
            kwargs["darwin_intercept"] = self._darwin_intercept
        elif sys.platform == "win32":
            kwargs["win32_event_filter"] = self._win32_filter
        self._listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release, **kwargs)
        with self._listener as listener:
            listener.join()


class FnHotkeyListener(HotkeyListener):
    """The fn/Globe key, Wispr-style. pynput cannot map fn, but a raw Quartz event
    tap sees its flagsChanged events (keycode 63). Same gestures as the base class:
    hold fn to dictate, fn+Space for hands-free, other keys cancel (so fn+Delete,
    fn+arrows keep working).

    Setup required once: System Settings → Keyboard → "Press 🌐 key to" → Do Nothing
    (else macOS pops the emoji picker / input-source switcher on fn taps), and make
    sure macOS's own hold-fn Dictation shortcut is off (Keyboard → Dictation).
    """

    _FN_FLAG = 0x800000  # kCGEventFlagMaskSecondaryFn

    def __init__(self, on_start, on_stop, tap_max_s: float = 0.3,
                 double_tap_window_s: float = 0.5, on_handsfree=None, on_cancel=None):
        super().__init__("fn", on_start, on_stop, tap_max_s, double_tap_window_s,
                         on_handsfree, on_cancel)
        self._tap = None

    def _tap_callback(self, _proxy, etype, event, _refcon):
        import Quartz
        try:
            if etype in (Quartz.kCGEventTapDisabledByTimeout,
                         Quartz.kCGEventTapDisabledByUserInput):
                Quartz.CGEventTapEnable(self._tap, True)
                return event
            keycode = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode)
            if etype == Quartz.kCGEventFlagsChanged:
                if keycode == self._vk:
                    fn_down = bool(Quartz.CGEventGetFlags(event) & self._FN_FLAG)
                    if fn_down and not self._down:
                        self._down = True
                        self._hotkey_press()
                    elif not fn_down and self._down:
                        self._down = False
                        self._hotkey_release()
                return event
            if etype == Quartz.kCGEventKeyDown:
                if keycode == _ESCAPE_KEYCODE and self._active:
                    self._swallow_esc_up = True
                    self.cancel()
                    return None
                if (keycode == _SPACE_KEYCODE and self._down
                        and self._active and not self._toggle):
                    self._swallow_space_up = True
                    self._enter_handsfree()
                    return None
                if self._down and self._active and not self._toggle:
                    self._cancel_combo()  # fn used as a modifier (fn+Delete, fn+arrow…)
                return event
            if etype == Quartz.kCGEventKeyUp:
                if keycode == _SPACE_KEYCODE and self._swallow_space_up:
                    self._swallow_space_up = False
                    return None
                if keycode == _ESCAPE_KEYCODE and self._swallow_esc_up:
                    self._swallow_esc_up = False
                    return None
            return event
        except Exception:
            return event

    def run(self):
        import Quartz
        mask = (Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
                | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
                | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged))
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault, mask, self._tap_callback, None)
        if self._tap is None:
            raise RuntimeError(
                "macOS hasn't granted keyboard access: enable Sotto (or your "
                "terminal, when running from a checkout) under System Settings "
                "→ Privacy & Security → Accessibility AND Input Monitoring.")
        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), source,
                                  Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        Quartz.CFRunLoopRun()
