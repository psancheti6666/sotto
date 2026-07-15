"""Text injection at the cursor: clipboard + simulated Cmd+V (save/restore clipboard),
with per-app keystroke fallback for paste-blocking apps."""

import time

import pyperclip
from pynput.keyboard import Controller, Key

_kb = Controller()


def frontmost_bundle_id() -> str:
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return app.bundleIdentifier() or ""
    except Exception:
        return ""


def inject(text: str, restore_delay_s: float = 0.15, use_keystrokes: bool = False):
    if not text:
        return
    if use_keystrokes:
        _kb.type(text)
        return
    saved = None
    try:
        saved = pyperclip.paste()
    except pyperclip.PyperclipException:
        pass
    pyperclip.copy(text)
    time.sleep(0.05)  # let the pasteboard settle before the paste event
    with _kb.pressed(Key.cmd):
        _kb.press("v")
        _kb.release("v")
    time.sleep(restore_delay_s)
    if saved is not None:
        pyperclip.copy(saved)
