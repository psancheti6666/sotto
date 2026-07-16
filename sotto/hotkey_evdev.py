# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Linux global hotkey via evdev (/dev/input) — works on X11 and Wayland alike.

Reuses HotkeyListener's gesture state machine; only the event source differs.
evdev reads keys at the kernel level but cannot swallow them, so the gestures
adjust vs macOS:
- hands-free is entered by double-tap only (hold+Space would leak Ctrl+Space
  into the focused app — an input-method toggle on many systems);
- Escape cancels dictation but still reaches the focused app;
- any other key pressed while holding = the hotkey is being used as a normal
  modifier combo → dictation is silently discarded, the combo passes through.

Requires read access to /dev/input:  sudo usermod -aG input $USER  (re-login).
"""

import logging
import selectors
import time

from .hotkey import HotkeyListener

log = logging.getLogger("sotto")

# Linux input-event keycodes (linux/input-event-codes.h), defined locally so
# this module — and the gesture tests — import on any OS without evdev.
KEY_ESC = 1
KEY_CODES = {
    "ctrl_r": 97, "ctrl": 29, "ctrl_l": 29,
    "alt_r": 100, "alt": 56, "alt_l": 56,
    "shift_r": 54, "shift": 42, "shift_l": 42,
    "super": 125, "super_l": 125, "super_r": 126,
    "menu": 127, "pause": 119, "scroll_lock": 70,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64,
    "f7": 65, "f8": 66, "f9": 67, "f10": 68, "f11": 87, "f12": 88,
}
_KEY_A = 30  # capability probe: a device that can emit KEY_A is a keyboard

_DOWN, _UP, _REPEAT = 1, 0, 2

PERMISSION_HELP = (
    "Cannot read /dev/input — Sotto needs to see the hotkey at the kernel level.\n"
    "Fix:  sudo usermod -aG input $USER   then log out and back in."
)


class EvdevHotkeyListener(HotkeyListener):
    def __init__(self, key_name: str, on_start, on_stop,
                 tap_max_s: float = 0.3, double_tap_window_s: float = 0.5,
                 on_handsfree=None, on_cancel=None):
        # "fn" skips the parent's pynput key resolution (pynput is macOS-only
        # here); the evdev keycode below replaces the placeholder vk.
        super().__init__("fn", on_start, on_stop, tap_max_s, double_tap_window_s,
                         on_handsfree, on_cancel)
        self._key_name = key_name
        code = KEY_CODES.get(key_name)
        if code is None:
            raise ValueError(
                f"unknown Linux hotkey {key_name!r} — pick one of: "
                + ", ".join(sorted(KEY_CODES)))
        self._vk = code

    # ------------------------------------------------------------- gesture core
    def _handle_event(self, code: int, value: int):
        """Translate one kernel key event into the shared state machine.
        Pure logic — unit-tested with synthetic (code, value) pairs."""
        if value == _REPEAT:
            return
        if code == self._vk:
            if value == _DOWN and not self._down:
                self._down = True
                self._hotkey_press()
            elif value == _UP and self._down:
                self._down = False
                self._hotkey_release()
            return
        if value != _DOWN:
            return
        if code == KEY_ESC:
            if self._active:
                self.cancel()  # the Escape itself still reaches the app
            return
        if self._down and self._active and not self._toggle:
            self._cancel_combo()  # hotkey used as a modifier; combo passes through

    # ------------------------------------------------------------- event source
    def _open_keyboards(self, evdev):
        devices, denied = [], False
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
            except PermissionError:
                denied = True
                continue
            except OSError:
                continue
            try:
                keys = dev.capabilities().get(evdev.ecodes.EV_KEY) or []
            except OSError:  # device vanished between open and query
                dev.close()
                continue
            if self._vk in keys and _KEY_A in keys:
                devices.append(dev)
            else:
                dev.close()
        if not devices and denied:
            raise RuntimeError(PERMISSION_HELP)
        return devices

    def run(self):
        import evdev
        while True:
            devices = self._open_keyboards(evdev)
            if not devices:
                log.warning("no keyboard with a %r key found — retrying in 5s "
                            "(is one plugged in?)", self._key_name)
                time.sleep(5.0)
                continue
            log.info("hotkey %s on: %s", self._key_name,
                     ", ".join(d.name for d in devices))
            sel = selectors.DefaultSelector()
            for dev in devices:
                sel.register(dev, selectors.EVENT_READ)
            while sel.get_map():
                for key, _ in sel.select():
                    dev = key.fileobj
                    try:
                        for event in dev.read():
                            if event.type == evdev.ecodes.EV_KEY:
                                self._handle_event(event.code, event.value)
                    except OSError:  # device unplugged
                        sel.unregister(dev)
                        dev.close()
            log.warning("all keyboards disappeared — rescanning…")
            time.sleep(1.0)
