# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Best-effort tray icon for Linux (docs/linux-app.md, L7) and Windows
(docs/windows-app.md W6 — pystray's Win32 backend is its native, best one;
the module keeps its historical name to spare the Linux spec/tests churn).

pystray drives the icon. On Ubuntu GNOME the only tray protocol the shell
renders is StatusNotifierItem (via the preinstalled AppIndicator extension),
which pystray reaches through its appindicator backend — gi + libayatana,
provided by the deb's Depends and the PyGObject stack the bundle carries.
Elsewhere pystray falls back to its gtk/xorg backends on its own. Every
failure mode (no pystray, no display, no tray host, missing gi) collapses to
one log line and the app runs tray-less — the overlay and dashboard are
Sotto's primary surfaces (settled decision, docs/linux-app.md).

Quit reuses the existing shutdown path instead of inventing a second one:
SIGINT → overlay_tk's handler destroys the tk root (or KeyboardInterrupt
unwinds a headless listener.run() on the main thread) → the process exits →
llm_server's atexit hook stops any ollama Sotto spawned. The tk tick keeps
rescheduling while hidden, so the signal is handled within one TICK_MS.

INVARIANT: icon.run() must stay OFF the main thread. pystray's gtk-family
backends reset the SIGINT handler to SIG_DFL during init — off-main that
raises and is swallowed inside pystray, but on the main thread it would
silently clobber overlay_tk's Ctrl+C handler and break quit entirely.
"""

import logging
import os
import signal
import sys
import threading

from .platform import IS_WINDOWS

log = logging.getLogger(__name__)

# the deb's postinst-installed icon; a checkout/tarball falls back to
# cropping the repo wordmark (same fractions as make_deb.sh / make_icns.py)
INSTALLED_ICON = "/usr/share/icons/hicolor/128x128/apps/sotto.png"
PAPER = "#F7F7F5"


def _menu_items(insights_available: bool, update_enabled: bool):
    """Pure menu description: (label, action) pairs in display order.
    Split from pystray so the gating unit-tests on macOS. Mirrors
    menubar.py: Insights only when the dashboard is up, "Check for
    Updates…" only once update.enabled() is true on Linux (L8), Quit
    always last."""
    items = []
    if insights_available:
        items.append(("Insights", "insights"))
    if update_enabled:
        items.append(("Check for Updates…", "updates"))
    items.append(("Quit Sotto", "quit"))
    return items


def _quit(*_):
    log.info("quit from tray")
    if IS_WINDOWS:
        # os.kill(SIGINT) on Windows is TerminateProcess-adjacent for a
        # windowed app — no unwinding, no atexit, orphaned ollama. Quit
        # through the overlay's command path instead (docs/windows-app.md
        # W6); headless (no tk loop to unwind, main thread parked in the
        # pynput hook), shut the engine down ourselves and leave.
        from . import llm_server, overlay_tk
        if not overlay_tk.request_quit():
            # headless: no loop to unwind
            llm_server.shutdown()
            os._exit(0)
        return  # mainloop exits → the normal teardown path runs
    os.kill(os.getpid(), signal.SIGINT)


def _logo_path():
    """The wordmark PNG: bundled under logo/ in the frozen onedir (spec
    datas), at the repo root in a checkout."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "logo", "sottoLogo.png")


def _icon_image():
    """A square PIL image for the tray: the installed hicolor icon when the
    deb laid one down, else the waveform mark cropped out of the wordmark
    (2%/5% offset, 28%×90% crop, centered on a 130%-of-mark-height paper
    square — the exact fractions make_deb.sh and make_icns.py use)."""
    from PIL import Image
    if os.path.exists(INSTALLED_ICON):
        return Image.open(INSTALLED_ICON)
    img = Image.open(_logo_path()).convert("RGBA")
    lw, lh = img.size
    mx, my = lw * 2 // 100, lh * 5 // 100
    mw, mh = lw * 28 // 100, lh * 90 // 100
    mark = img.crop((mx, my, mx + mw, my + mh))
    sq = mh * 130 // 100
    canvas = Image.new("RGBA", (sq, sq), PAPER)
    canvas.paste(mark, ((sq - mark.width) // 2, (sq - mark.height) // 2),
                 mark)
    return canvas


def _tray_thread(dashboard_port):
    """Body of the tray thread. Imports live here: pystray picks its backend
    at import time and may sit on a GLib loop forever after — none of that
    may ever touch, block, or kill the caller."""
    try:
        import pystray

        from . import update

        # native window when the platform can host one (WebView2 / WebKitGTK);
        # falls back to the browser tab inside show_soon() — the tray gains
        # zero new failure modes from it
        if IS_WINDOWS:
            from . import insights_windows as insights_native
        else:
            from . import insights_linux as insights_native
        open_insights = lambda *_: insights_native.show_soon()
        actions = {
            "insights": open_insights,
            "updates": lambda *_: update.check_from_menu(),
            "quit": _quit,
        }
        menu = [
            pystray.MenuItem(label, actions[action],
                             # left-click on hosts that support a default
                             # action opens Insights, like the macOS Dock icon
                             default=(action == "insights"))
            # menu_available, not enabled(): Windows gets the notify-and-
            # open check even though auto-install is gated off there (W9)
            for label, action in _menu_items(dashboard_port is not None,
                                             update.menu_available())
        ]
        icon = pystray.Icon("sotto", _icon_image(), "Sotto",
                            pystray.Menu(*menu))
        if not icon.HAS_MENU:
            # pystray's xorg fallback renders the icon but NO menu at all —
            # Quit would silently not exist. With a dashboard the left-click
            # default action still opens Insights, so the icon earns its
            # place; without one it would be a dead pixel — skip it, honestly.
            if dashboard_port is None:
                log.info("tray backend has no menu support and no dashboard "
                         "to open — running without a tray icon")
                return
            log.warning("tray backend has no menu support — left-click opens "
                        "Insights; quit Sotto with Ctrl+C or by ending the "
                        "process")
        log.info("tray icon starting (backend %s)", pystray.Icon.__module__)
        icon.run()  # owns this thread until the process exits
    except Exception as e:
        log.info("tray unavailable (%s) — running without a tray icon", e)


# the live tray thread (insights_linux reads it: while this thread is alive
# its pystray loop is — or is about to be — the GLib dispatcher, so the
# standby loop gives it a head start on acquiring the context)
_thread = None


def start(dashboard_port=None):
    """Spawn the tray in a daemon thread; returns immediately, never raises.
    dashboard_port None = no dashboard = no Insights item."""
    global _thread
    t = threading.Thread(target=_tray_thread, args=(dashboard_port,),
                         name="tray", daemon=True)
    _thread = t
    t.start()
    return t
