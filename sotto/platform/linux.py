# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Linux implementations: freedesktop theme sounds via paplay, X11 active
window, and user-visible alerts via zenity/kdialog/notify-send."""

import logging
import os
import shutil
import subprocess

from . import session_type

log = logging.getLogger("sotto")

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


def _alert_argv(title: str, text: str, which):
    """Argv for the first available dialog tool, or None when there is none.
    Dialogs (zenity/kdialog) beat a notification: alerts carry instructions
    the user must read, and notifications can be missed or disabled."""
    if which("zenity"):
        # --no-markup: zenity parses text as Pango markup by default, which
        # would garble alerts containing & or < (e.g. "Privacy & Security");
        # --flag=value / trailing -- forms keep a -leading string from being
        # read as an option.
        return ["zenity", "--warning", f"--title={title}", f"--text={text}",
                "--no-wrap", "--no-markup"]
    if which("kdialog"):
        return ["kdialog", "--title", title, "--sorry", text]
    if which("notify-send"):
        return ["notify-send", "-a", "Sotto", "-u", "critical", "--", title, text]
    return None


def alert(title: str, text: str):
    """Show a user-visible warning. Fire-and-forget child process, so it is
    safe from any thread, never blocks the caller, and needs no UI mainloop.
    Falls back to the log when no dialog tool exists (headless/minimal box)."""
    argv = _alert_argv(title, text, shutil.which)
    if argv is None:
        log.warning("alert (no zenity/kdialog/notify-send): %s — %s", title, text)
        return
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except Exception:
        log.warning("alert (%s failed to spawn): %s — %s", argv[0], title, text)


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
