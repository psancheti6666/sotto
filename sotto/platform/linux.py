"""Linux implementations: freedesktop theme sounds via paplay, X11 active window."""

import os
import shutil
import subprocess

from . import session_type

_SOUND_DIR = "/usr/share/sounds/freedesktop/stereo"


def play_sound(name: str):
    """Play a freedesktop sound-theme name (or an absolute file path).
    Silent no-op if paplay or the file is missing."""
    path = name if os.path.isabs(name) else os.path.join(_SOUND_DIR, f"{name}.oga")
    if not os.path.exists(path) or not shutil.which("paplay"):
        return
    try:
        subprocess.Popen(["paplay", path],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def haptic():
    pass  # no trackpad-haptics equivalent on Linux


def active_app_id() -> str:
    """Lowercased WM_CLASS class name of the focused window on X11 (e.g.
    "google-chrome", "slack", "firefox"); "" on Wayland (no portable way to
    ask the compositor) or when the tools are missing."""
    if session_type() != "x11":
        return ""
    try:
        win = subprocess.run(["xdotool", "getactivewindow"],
                             capture_output=True, text=True, timeout=1)
        wid = win.stdout.strip()
        if win.returncode != 0 or not wid:
            return ""
        out = subprocess.run(["xprop", "-id", wid, "WM_CLASS"],
                             capture_output=True, text=True, timeout=1)
        parts = out.stdout.split('"')  # WM_CLASS(STRING) = "instance", "Class"
        if len(parts) >= 4:
            return parts[3].lower()
        if len(parts) >= 2:
            return parts[1].lower()
    except Exception:
        pass
    return ""
