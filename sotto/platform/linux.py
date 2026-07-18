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


def clean_env() -> dict:
    """Environment for HOST binaries launched from the frozen bundle.

    PyInstaller exports LD_LIBRARY_PATH pointing INTO the bundle so its own
    binaries resolve — but system tools (zenity, xdg-open, paplay, browsers…)
    then load the bundle's libraries instead of the system's and can crash or
    silently misbehave (VM validation round: the tray's Insights opened
    nothing, alerts fell through to the weakest backend). PyInstaller saves
    the pre-launch values in *_ORIG — restore those, drop the overrides.
    Harmless outside a bundle (nothing to restore, env returned unchanged)."""
    env = os.environ.copy()
    for var in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
        orig = env.pop(var + "_ORIG", None)
        if orig is not None:
            env[var] = orig
        else:
            env.pop(var, None)
    return env


def open_url(url: str):
    """Open a URL in the user's browser — xdg-open with a sanitized env
    (webbrowser.open inherits the poisoned one inside a bundle), falling
    back to webbrowser for exotic setups."""
    if shutil.which("xdg-open"):
        try:
            subprocess.Popen(["xdg-open", url], env=clean_env(),
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return
        except Exception as e:
            log.warning("xdg-open failed (%s) — falling back", e)
    import webbrowser
    webbrowser.open(url)


def play_sound(name: str):
    """Play a freedesktop sound-theme name (or an absolute file path).
    Silent no-op if paplay or the file is missing."""
    path = name if os.path.isabs(name) else os.path.join(_SOUND_DIR, f"{name}.oga")
    if not os.path.exists(path) or not shutil.which("paplay"):
        return
    try:
        subprocess.Popen(["paplay", path], env=clean_env(),
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
        subprocess.Popen(argv, env=clean_env(), stdout=subprocess.DEVNULL,
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
        win = subprocess.run(["xdotool", "getactivewindow"], env=clean_env(),
                             capture_output=True, text=True, timeout=1)
        wid = win.stdout.strip()
        if win.returncode != 0 or not wid:
            return ""
        out = subprocess.run(["xprop", "-id", wid, "WM_CLASS"], env=clean_env(),
                             capture_output=True, text=True, timeout=1)
        parts = out.stdout.split('"')  # WM_CLASS(STRING) = "instance", "Class"
        if len(parts) >= 4:
            return parts[3].lower()
        if len(parts) >= 2:
            return parts[1].lower()
    except Exception:
        pass
    return ""
