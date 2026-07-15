"""Linux text injection: probe the available tools once, then use the best.

X11:      xdotool type (keyboard-layout aware); paste = clipboard + Ctrl+V.
Wayland:  wtype (virtual-keyboard protocol — wlroots/KDE, NOT GNOME) →
          ydotool (uinput; works on GNOME but needs the ydotoold daemon) →
          last resort: copy the text to the clipboard and pop a desktop
          notification asking the user to press Ctrl+V.

If the chosen tool fails at runtime, the chain falls through to the next one.
"""

import logging
import shutil
import subprocess
import time

from .platform import session_type

log = logging.getLogger("sotto")


def _probe(cmd) -> bool:
    """A tool exists AND a no-op invocation succeeds (e.g. wtype exits non-zero
    on GNOME, ydotool errors when ydotoold isn't running)."""
    try:
        return subprocess.run(cmd, capture_output=True, timeout=3).returncode == 0
    except Exception:
        return False


class _XdotoolInjector:
    name = "xdotool"

    def type_text(self, text: str, interval_s: float):
        subprocess.run(["xdotool", "type", "--clearmodifiers",
                        "--delay", str(max(1, int(interval_s * 1000))), "--", text],
                       check=True)

    def paste_text(self, text: str, restore_delay_s: float):
        import pyperclip
        saved = None
        try:
            saved = pyperclip.paste()
        except Exception:
            pass
        pyperclip.copy(text)
        time.sleep(0.05)
        subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+v"], check=True)
        time.sleep(restore_delay_s)
        if saved is not None:
            pyperclip.copy(saved)


class _WtypeInjector:
    name = "wtype"

    def type_text(self, text: str, interval_s: float):
        subprocess.run(["wtype", "-d", str(max(1, int(interval_s * 1000))), "--", text],
                       check=True)

    def paste_text(self, text: str, restore_delay_s: float):
        saved = _wl_paste()
        _wl_copy(text)
        time.sleep(0.05)
        subprocess.run(["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"], check=True)
        time.sleep(restore_delay_s)
        if saved is not None:
            _wl_copy(saved)


class _YdotoolInjector:
    name = "ydotool"

    def type_text(self, text: str, interval_s: float):
        subprocess.run(["ydotool", "type", "--key-delay",
                        str(max(1, int(interval_s * 1000))), "--", text], check=True)

    def paste_text(self, text: str, restore_delay_s: float):
        saved = _wl_paste()
        _wl_copy(text)
        time.sleep(0.05)
        # keycodes from linux/input-event-codes.h: 29=Ctrl, 47=V
        subprocess.run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"], check=True)
        time.sleep(restore_delay_s)
        if saved is not None:
            _wl_copy(saved)


class _ClipboardNotifyInjector:
    """Nothing can type into the focused window — leave the text on the
    clipboard and tell the user. (No save/restore: the text must stay put.)"""

    name = "clipboard"

    def _deliver(self, text: str):
        if shutil.which("wl-copy"):
            subprocess.run(["wl-copy"], input=text.encode(), check=True)
        elif shutil.which("xclip"):
            subprocess.run(["xclip", "-selection", "clipboard"],
                           input=text.encode(), check=True)
        else:
            import pyperclip
            pyperclip.copy(text)
        if shutil.which("notify-send"):
            subprocess.run(["notify-send", "-a", "Sotto", "Sotto",
                            "Transcript copied — press Ctrl+V to paste."])

    def type_text(self, text: str, interval_s: float):
        self._deliver(text)

    def paste_text(self, text: str, restore_delay_s: float):
        self._deliver(text)


def _wl_copy(text: str):
    subprocess.run(["wl-copy"], input=text.encode(), check=True)


def _wl_paste():
    try:
        out = subprocess.run(["wl-paste", "--no-newline"], capture_output=True, timeout=2)
        return out.stdout.decode() if out.returncode == 0 else None
    except Exception:
        return None


class _Chain:
    def __init__(self, injectors):
        self._injectors = injectors

    def type_text(self, text: str, interval_s: float):
        self._call("type_text", text, interval_s)

    def paste_text(self, text: str, restore_delay_s: float):
        self._call("paste_text", text, restore_delay_s)

    def _call(self, method, *args):
        while True:
            inj = self._injectors[0]
            try:
                getattr(inj, method)(*args)
                return
            except Exception:
                log.exception("injection via %s failed", inj.name)
                if len(self._injectors) == 1:
                    return
                self._injectors.pop(0)
                log.warning("falling back to %s injection", self._injectors[0].name)


def build_injector() -> _Chain:
    session = session_type()
    chain = []
    if session == "wayland":
        if shutil.which("wtype") and _probe(["wtype", "--", ""]):
            chain.append(_WtypeInjector())
        if shutil.which("ydotool") and _probe(["ydotool", "type", "--", ""]):
            chain.append(_YdotoolInjector())
    else:  # x11, or headless/unknown (xdotool will fail loudly there anyway)
        if shutil.which("xdotool"):
            chain.append(_XdotoolInjector())
    chain.append(_ClipboardNotifyInjector())
    log.info("text injection chain: %s", " → ".join(i.name for i in chain))
    return _Chain(chain)
