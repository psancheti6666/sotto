# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Native Insights window for Windows (docs/windows-app.md W8).

pywebview hosting the local dashboard in a WebView2 window — the same
unchanged page (localhost-only, zero external requests, textContent
rendering, X-Sotto CSRF) macOS shows in WKWebView and Linux in WebKitGTK.
Same public surface as both: configure(port) / available() / show_soon().

Why pywebview and not a hand-rolled host (decision table): WebView2 has no
gi/pyobjc-style introspected binding — hand-rolling means COM vtables and a
Win32 message pump, ~500 lines of exactly the code a maintained library
exists for. pywebview's main-thread requirement is a macOS/Cocoa
constraint; on Windows its loop runs on any thread, so it lives on a
daemon thread here (the tk overlay owns the main thread, as everywhere).
gui="edgechromium" is FORCED: without it a machine missing the WebView2
runtime would silently degrade to the legacy MSHTML engine — the honest
failure is the browser-tab fallback, not IE11.

pywebview's loop exits when its last window is DESTROYED, and it cannot be
restarted in-process — so close must be hide (the closing handler cancels
the close), which also happens to be the macOS/Linux behavior. Fallback:
any failure — pywebview missing (checkout), no WebView2 runtime, loop
death — logs one line, opens the browser tab, and is remembered (sticky).
"""

import logging
import threading

log = logging.getLogger("sotto")

_port = None
_failed = False     # sticky: once the webview path breaks, browser only
_window = None      # pywebview window proxy; close = hide, so it lives on
_started = False    # webview.start() runs at most once per process
_lock = threading.Lock()


def configure(port: int):
    """Record the dashboard port; called once at startup before any show."""
    global _port
    _port = port


def available() -> bool:
    return _port is not None


def show_soon():
    """Thread-safe, never blocks the caller (tray click / startup path):
    the webview work runs on its own daemon thread; any failure falls back
    to the browser tab."""
    if _port is None:
        return
    if _failed:
        _open_browser()
        return
    threading.Thread(target=_show, name="insights-webview",
                     daemon=True).start()


def _open_browser():
    from . import dashboard
    dashboard.open_in_browser(_port)


def _fall_back(reason):
    """Remember the failure, say so once, give the user the page anyway."""
    global _failed
    if not _failed:
        _failed = True
        log.info("native Insights window unavailable (%s) — opening the "
                 "dashboard in your browser instead", reason)
    _open_browser()


def _import_webview():
    """Split out so the unit tier fakes it (pywebview is win32-only)."""
    import webview
    return webview


def _show():
    """Create-once / reshow: the first call builds the window and starts
    pywebview's loop ON THIS THREAD (blocks for the process lifetime —
    that's the loop owner); later calls just surface the hidden window.
    The lock only guards the bookkeeping, never the blocking start()."""
    global _window, _started
    try:
        webview = _import_webview()
        start_loop = False
        with _lock:
            if _window is None:
                _window = webview.create_window(
                    "Sotto — Insights", f"http://127.0.0.1:{_port}/",
                    width=1080, height=780, min_size=(640, 480))
                _window.events.closing += _on_closing
                if not _started:
                    _started = True
                    start_loop = True
        if start_loop:
            # blocks until the last window is DESTROYED — which close=hide
            # prevents, so effectively forever. Reaching the line after it
            # means the loop died underneath us: sticky-fallback.
            webview.start(gui="edgechromium")
            raise RuntimeError("webview loop exited")
        else:
            _window.show()
    except Exception as e:
        with _lock:
            _window = None
        _fall_back(e)


def _on_closing():
    """Close = hide (macOS/Linux parity) — and, on Windows, a hard
    requirement: destroying the last window ends pywebview's loop for the
    process lifetime."""
    try:
        _window.hide()
    except Exception:
        pass
    return False  # cancel the close


def smoke(port: int, timeout_s: float = 60.0) -> int:
    """CI-only (--smoke-webview): prove the real WebView2 renders the real
    dashboard inside the frozen bundle. Runs pywebview's loop on the MAIN
    thread (no tk loop exists in the smoke process). The exit code is the
    contract AND every diagnostic goes to webview-smoke.txt in the cwd —
    the windowed bootloader devnulls the std streams, so a print here is
    invisible in CI (learned on this milestone's first red run). A
    watchdog hard-exits on hang — CI must never wait on a wedged GUI
    loop."""
    import os
    import traceback

    report = os.path.join(os.getcwd(), "webview-smoke.txt")

    def write(line: str):
        try:
            with open(report, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    try:
        write("smoke starting")
        from . import dashboard
        server = dashboard.start(port)
        if server is None:
            write(f"FAILED: port {port} unavailable")
            return 1
        configure(port)
        webview = _import_webview()
        write("webview imported")
        result = {}
        done = threading.Event()

        def on_loaded():
            try:
                result["title"] = win.evaluate_js("document.title")
            except Exception as e:
                result["error"] = str(e)
            done.set()
            win.destroy()  # last window → start() returns

        def watchdog():
            if not done.wait(timeout_s):
                write(f"FAILED: no load event within {timeout_s}s")
                os._exit(1)

        threading.Thread(target=watchdog, daemon=True).start()
        win = webview.create_window("smoke", f"http://127.0.0.1:{port}/",
                                    hidden=True)
        win.events.loaded += on_loaded
        write("window created — starting loop (a native crash after this "
              "line leaves no further breadcrumbs)")
        webview.start(gui="edgechromium")
        title = result.get("title") or ""
        ok = "sotto" in title.lower()
        write(f"{'OK' if ok else 'FAILED'}: title={title!r} "
              f"err={result.get('error')!r}")
        return 0 if ok else 1
    except Exception:
        write("FAILED with traceback:\n" + traceback.format_exc())
        return 1
