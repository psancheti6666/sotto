"""Text injection at the cursor.

Default ("auto"): type the text as real keystrokes — it appears character by
character like typing. Falls back to clipboard + Cmd+V (with save/restore)
when the text contains newlines (a typed Enter can trigger actions, e.g. send
a chat message) or is very long (typing is ~300 chars/s; pasting is instant).
"""

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


def _type_text(text: str, interval_s: float):
    for ch in text:
        _kb.type(ch)
        if interval_s:
            time.sleep(interval_s)


def _paste_text(text: str, restore_delay_s: float):
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


def inject(text: str, mode: str = "auto", type_max_chars: int = 2000,
           type_interval_s: float = 0.003, restore_delay_s: float = 0.15):
    if not text:
        return
    if mode == "type":
        _type_text(text, type_interval_s)
    elif mode == "paste":
        _paste_text(text, restore_delay_s)
    else:  # auto
        if "\n" not in text and len(text) <= type_max_chars:
            _type_text(text, type_interval_s)
        else:
            _paste_text(text, restore_delay_s)
