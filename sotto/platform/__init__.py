"""Platform detection and dispatch for the small OS-specific helpers.

The big platform components (overlay, hotkey listener, ASR backend) are whole
modules selected in app.py; this package only carries the leaf functions that
several of them share: play_sound, haptic, active_app_id.
"""

import os
import platform as _stdlib_platform
import sys

IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
IS_APPLE_SILICON = IS_MACOS and _stdlib_platform.machine() == "arm64"


def session_type() -> str:
    """Linux display session: "wayland", "x11", or "" when undetectable."""
    if not IS_LINUX:
        return ""
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    st = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if st in ("wayland", "x11"):
        return st
    return "x11" if os.environ.get("DISPLAY") else ""


def play_sound(name: str):
    if not name:
        return
    if IS_MACOS:
        from . import macos
        macos.play_sound(name)
    elif IS_LINUX:
        from . import linux
        linux.play_sound(name)


def haptic():
    if IS_MACOS:
        from . import macos
        macos.haptic()


def active_app_id() -> str:
    """Identifier of the focused app: a bundle id on macOS, a lowercased
    WM_CLASS on Linux/X11, "" when unknown (e.g. Wayland)."""
    if IS_MACOS:
        from . import macos
        return macos.active_app_id()
    if IS_LINUX:
        from . import linux
        return linux.active_app_id()
    return ""
