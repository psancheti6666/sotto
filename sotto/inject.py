"""Text injection at the cursor.

Default ("auto"): type the text as real keystrokes — it appears character by
character like typing. Falls back to clipboard-paste (with save/restore)
when the text contains newlines (a typed Enter can trigger actions, e.g. send
a chat message) or is very long (typing is ~300 chars/s; pasting is instant).

The mode routing lives here; the actual key events come from a per-platform
injector: pynput on macOS, the xdotool/wtype/ydotool chain on Linux.
"""

import sys
import time

_injector = None


def _get_injector():
    global _injector
    if _injector is None:
        if sys.platform.startswith("linux"):
            from .inject_linux import build_injector
            _injector = build_injector()
        else:
            _injector = _MacInjector()
    return _injector


class _MacInjector:
    def __init__(self):
        from pynput.keyboard import Controller
        self._kb = Controller()

    def type_text(self, text: str, interval_s: float):
        for ch in text:
            self._kb.type(ch)
            if interval_s:
                time.sleep(interval_s)

    def paste_text(self, text: str, restore_delay_s: float):
        import pyperclip
        from pynput.keyboard import Key
        saved = None
        try:
            saved = pyperclip.paste()
        except pyperclip.PyperclipException:
            pass
        pyperclip.copy(text)
        time.sleep(0.05)  # let the pasteboard settle before the paste event
        with self._kb.pressed(Key.cmd):
            self._kb.press("v")
            self._kb.release("v")
        time.sleep(restore_delay_s)
        if saved is not None:
            pyperclip.copy(saved)


def inject(text: str, mode: str = "auto", type_max_chars: int = 2000,
           type_interval_s: float = 0.003, restore_delay_s: float = 0.15):
    if not text:
        return
    injector = _get_injector()
    if mode == "type":
        injector.type_text(text, type_interval_s)
    elif mode == "paste":
        injector.paste_text(text, restore_delay_s)
    else:  # auto
        if "\n" not in text and len(text) <= type_max_chars:
            injector.type_text(text, type_interval_s)
        else:
            injector.paste_text(text, restore_delay_s)
