# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Native Insights window for Linux (docs/linux-app.md).

A Gtk.Window hosting a WebKit2.WebView pointed at the local dashboard server
— the exact same page a browser shows at http://127.0.0.1:<port>/,
deliberately unchanged (localhost-only, zero external requests, textContent
rendering, X-Sotto CSRF header — none of that is touched here). The macOS
equivalent is insights.py (WKWebView); this module mirrors its public
surface: configure(port) / available() / show_soon(). Closing the window
hides it; the tray's "Insights" item brings it back.

Mainloop: the tk overlay owns the main thread on Linux, so GTK work cannot
run there. All Gtk/WebKit calls happen inside GLib.idle_add callbacks, which
post to GLib's DEFAULT main context — the same context pystray's
gtk/appindicator backends iterate on the tray daemon thread. When no tray
loop exists, _ensure_loop_thread starts a standby daemon thread running
GLib.MainLoop().run(); g_main_loop_run from a thread that cannot acquire the
context simply blocks until it can, so tray and standby compose safely and
exactly ONE thread executes GTK calls at any moment (GTK3's single-thread
rule holds). PyGObject only installs its SIGINT glue when run() is called
from the main thread, so neither loop can clobber overlay_tk's Ctrl+C
handler (the same property tray_linux's INVARIANT relies on).

Fallback: any failure — no gi, no WebKit2 typelib, no display, window or
load breakage — logs one line, opens the dashboard in the browser exactly as
before, and is remembered so later clicks skip straight to the browser.
The native window is an upgrade, never a new failure mode.
"""

import logging
import os
import sys
import threading
import time

log = logging.getLogger("sotto")

# newest first: 24.04 ships the 4.1 (libsoup3) introspection data; 22.04 has
# both. Both are GTK3-based — 4.x here is the WebKit API version, not GTK4.
WEBKIT_VERSIONS = ("4.1", "4.0")

_port = None
_failed = False       # sticky: once the webview path breaks, browser only
_sanitized = False
_window = None        # Gtk.Window, built once and reused (close = hide)
_webview = None
_loop_thread = None
_lock = threading.Lock()


def configure(port: int):
    """Record the dashboard port; called once at startup before any show."""
    global _port
    _port = port


def available() -> bool:
    return _port is not None


def show_soon():
    """Thread-safe: queue the window onto whichever thread iterates GLib's
    default context (tray loop or our standby). Falls back to the browser
    tab on any failure — never raises, never crashes the caller."""
    if _port is None:
        return
    if _failed:
        _open_browser()
        return
    try:
        GLib = _gi_modules()[0]
        _ensure_loop_thread(GLib)
        GLib.idle_add(_show)
    except Exception as e:
        _fall_back(e)


def _open_browser():
    from . import dashboard
    dashboard.open_in_browser(_port)


def _fall_back(reason):
    """Remember the failure, say so once, and give the user the page anyway."""
    global _failed
    if not _failed:
        _failed = True
        log.info("native Insights window unavailable (%s) — opening the "
                 "dashboard in your browser instead", reason)
    _open_browser()


def _require_webkit(gi_mod):
    """Register the newest available WebKit2 introspection namespace.
    Split out so the 4.1→4.0 preference is unit-testable with a fake gi."""
    err = None
    for version in WEBKIT_VERSIONS:
        try:
            gi_mod.require_version("WebKit2", version)
            return version
        except ValueError as e:
            err = e
    raise err


def _gi_modules():
    """Lazy import of the GLib/Gtk/WebKit2 trio. Anything missing raises and
    the caller falls back — gi never loads on macOS or in the unit tier.
    PyGObject's Gtk override initializes GTK at import and raises when there
    is no display, which lands in the same fallback."""
    sanitize_environ()
    import gi
    gi.require_version("Gtk", "3.0")
    _require_webkit(gi)
    from gi.repository import GLib, Gtk, WebKit2
    return GLib, Gtk, WebKit2


def sanitize_environ():
    """Frozen bundle only: WebKitGTK spawns SYSTEM helper binaries
    (WebKitWebProcess/WebKitNetworkProcess) asynchronously, and they inherit
    os.environ — with PyInstaller's LD_LIBRARY_PATH they load bundle
    libraries and die (the #63 bug class). Helpers spawn at times we don't
    control, so a scoped swap can't cover them: apply the sanitized env to
    os.environ once, permanently. app.py calls this at startup BEFORE any
    thread spawns, so nothing ever reads os.environ mid-mutation. The
    process's own dlopens are unaffected — the dynamic linker captured
    LD_LIBRARY_PATH at exec; environ changes only reach children.

    Two values are deliberately put back after the clean_env() sweep:
    - GI_TYPELIB_PATH: read LIVE by libgirepository, and the bundled
      typelibs are what keep the tray working on systems without the gir
      packages (helpers are plain C binaries — they never read it, so
      keeping it costs them nothing).
    - LD_LIBRARY_PATH_ORIG/LD_PRELOAD_ORIG: clean_env() pops these while
      restoring the user's own values; without re-adding them every LATER
      clean_env() call would find no _ORIG and silently drop the restored
      value — keeping them makes clean_env() idempotent forever after."""
    global _sanitized
    if _sanitized or not getattr(sys, "frozen", False):
        return
    from .platform.linux import clean_env
    keep_typelib = os.environ.get("GI_TYPELIB_PATH")
    keep_orig = {var: os.environ.get(var)
                 for var in ("LD_LIBRARY_PATH_ORIG", "LD_PRELOAD_ORIG")}
    env = clean_env()
    for key in set(os.environ) - set(env):
        os.environ.pop(key, None)
    os.environ.update(env)
    if keep_typelib is not None:
        os.environ["GI_TYPELIB_PATH"] = keep_typelib
    for var, val in keep_orig.items():
        if val is not None:
            os.environ[var] = val
    _sanitized = True
    log.debug("environment sanitized for the WebKit helper processes")


# Startup grace before the standby loop starts dispatching: gives pystray's
# tray loop time to acquire the default context first. pystray does a few
# GTK calls directly on its own thread during init (Gtk.init_check,
# Indicator.new) — if the standby loop were already dispatching our window
# build at that moment, two threads would be inside GTK at once. Once EITHER
# loop owns the context the other blocks in acquire, so the race only exists
# in this startup window; the grace closes it unless the tray takes longer
# than this to reach its loop (then we accept the tiny residual window
# rather than never showing).
TRAY_LOOP_GRACE_S = 3.0


def _loop_grace() -> float:
    from . import tray_linux
    t = getattr(tray_linux, "_thread", None)
    return TRAY_LOOP_GRACE_S if (t is not None and t.is_alive()) else 0.0


def _ensure_loop_thread(GLib):
    """Guarantee some thread is iterating the default main context so
    idle_add callbacks actually run. If pystray's tray loop already owns the
    context this thread blocks as a standby (g_main_loop_run semantics) —
    harmless, and it takes over dispatch only if the tray loop ever exits."""
    global _loop_thread
    with _lock:
        if _loop_thread is None or not _loop_thread.is_alive():
            grace = _loop_grace()

            def standby():
                if grace:
                    time.sleep(grace)
                GLib.MainLoop().run()

            _loop_thread = threading.Thread(
                target=standby, name="insights-glib", daemon=True)
            _loop_thread.start()


def _show():
    """Build on first use, then raise. Runs on the GLib dispatch thread.
    Returns False so idle_add fires it exactly once per queueing."""
    global _window, _webview
    try:
        if _window is None:
            _window, _webview = _build()
        if not _webview.get_uri():  # first show, or a load never landed
            _webview.load_uri(f"http://127.0.0.1:{_port}/")
        _window.show_all()
        _window.present()
    except Exception as e:
        _fall_back(e)
    return False


def _build():
    _, Gtk, WebKit2 = _gi_modules()

    win = Gtk.Window(title="Sotto — Insights")
    win.set_default_size(1080, 780)
    win.set_size_request(640, 480)
    win.set_position(Gtk.WindowPosition.CENTER)
    try:  # the deb installs a hicolor icon; a checkout just keeps the default
        from .tray_linux import INSTALLED_ICON
        if os.path.exists(INSTALLED_ICON):
            win.set_icon_from_file(INSTALLED_ICON)
    except Exception:
        pass

    view = WebKit2.WebView()
    win.add(view)
    # async breakage (WebKit's web process dying is the exact failure family
    # a frozen bundle risks) must reach the same fallback as a sync one — a
    # permanently blank window would betray the "never worse than the
    # browser tab" contract
    view.connect("load-failed", _on_load_failed)
    view.connect("web-process-terminated", _on_web_process_died)
    view.load_uri(f"http://127.0.0.1:{_port}/")

    # close = hide, window survives for the next tray click (macOS parity:
    # insights.py sets releasedWhenClosed False for the same reason)
    win.connect("delete-event", _on_delete)
    return win, view


def _on_delete(win, _event):
    win.hide()
    return True  # stop propagation — never destroy


def _abandon_window(reason):
    """Tear the window down and hand the user the browser instead (sticky)."""
    global _window, _webview
    win, _window, _webview = _window, None, None
    if win is not None:
        try:
            win.destroy()
        except Exception:
            pass
    _fall_back(reason)


def _on_load_failed(_view, _event, uri, error):
    _abandon_window(f"load failed for {uri}: {error}")
    return True  # handled — no default error page


def _on_web_process_died(_view, reason):
    _abandon_window(f"web process terminated ({reason})")


def smoke(port: int, timeout_s: float = 30.0) -> int:
    """CI-only: prove the real webview renders the real dashboard inside the
    frozen bundle (xvfb). Serves the dashboard, loads it, and succeeds only
    when the page title arrives — the first actual execution of this module,
    since no local Linux hardware exists. Returns a process exit code."""
    from . import dashboard
    server = dashboard.start(port)
    if server is None:
        print(f"webview smoke: port {port} unavailable", file=sys.stderr)
        return 1
    configure(port)
    GLib, Gtk, WebKit2 = _gi_modules()
    result = {}

    def on_load(view, event):
        if event == WebKit2.LoadEvent.FINISHED:
            result["title"] = view.get_title() or ""
            loop.quit()

    def start():
        win, view = _build()
        view.connect("load-changed", on_load)
        return False

    def give_up():
        result.setdefault("title", None)
        loop.quit()
        return False

    loop = GLib.MainLoop()
    GLib.idle_add(start)
    GLib.timeout_add(int(timeout_s * 1000), give_up)
    loop.run()
    title = result.get("title")
    if title and "sotto" in title.lower():
        print(f"webview smoke OK (title={title!r})")
        return 0
    print(f"webview smoke FAILED (title={title!r})", file=sys.stderr)
    return 1
