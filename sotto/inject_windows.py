# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Windows text injection (docs/windows-app.md W3).

pynput's Controller emits SendInput with KEYEVENTF_UNICODE on Windows —
exactly the scouted primary mechanism — so typing is the same code shape as
macOS; only the paste chord differs (Ctrl+V, which Windows Terminal and
modern conhost both honor). Known limits, documented rather than papered
over: UIPI means neither typing nor pasting reaches elevated (admin)
windows, and Windows Terminal's unicode quirks are why terminals belong in
keystroke_apps (the paste path) — defaults land with W5's config branch.
"""

import logging
import time

log = logging.getLogger("sotto")


class WinInjector:
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
        time.sleep(0.05)  # let the clipboard settle before the paste chord
        with self._kb.pressed(Key.ctrl):
            self._kb.press("v")
            self._kb.release("v")
        time.sleep(restore_delay_s)
        if saved is not None:
            pyperclip.copy(saved)


def build_injector():
    return WinInjector()
