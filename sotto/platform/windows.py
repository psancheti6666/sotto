# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Windows implementations (docs/windows-app.md W3): focused-app identity via
Win32, alerts via MessageBox, URLs via os.startfile, system sounds via
winsound. Everything here is ctypes/stdlib — no new dependencies."""

import logging
import os

log = logging.getLogger("sotto")

_MB_ICONWARNING = 0x30
_MB_SETFOREGROUND = 0x10000
_MB_TOPMOST = 0x40000


def active_app_id() -> str:
    """Lowercased exe basename of the foreground window's process (e.g.
    "notepad.exe", "windowsterminal.exe"); "" when undetectable. The Windows
    analogue of the macOS bundle id / X11 WM_CLASS for keystroke_apps
    matching."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        # explicit restypes: HWND/HANDLE are pointer-sized — ctypes' default
        # c_int return type could truncate them on 64-bit Windows
        try:
            user32.GetForegroundWindow.restype = wintypes.HWND
            kernel32.OpenProcess.restype = wintypes.HANDLE
        except Exception:
            pass  # fakes in the unit tier need not carry attributes
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        # PROCESS_QUERY_LIMITED_INFORMATION — works across privilege levels
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(260)
            size = wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf,
                                                   ctypes.byref(size)):
                import ntpath  # explicit \\ semantics (unit-testable off-Windows)
                return ntpath.basename(buf.value).lower()
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        pass
    return ""


def alert(title: str, text: str):
    """MessageBox on a daemon thread — safe from any thread, never blocks the
    caller, needs no UI mainloop (the zenity-subprocess philosophy, in
    process form because Windows has no zenity)."""
    import threading

    def show():
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, text, title,
                _MB_ICONWARNING | _MB_SETFOREGROUND | _MB_TOPMOST)
        except Exception:
            log.warning("alert (MessageBox failed): %s — %s", title, text)

    threading.Thread(target=show, daemon=True).start()


def open_url(url: str):
    try:
        os.startfile(url)  # default browser via the shell
    except Exception as e:
        log.warning("startfile failed (%s) — falling back to webbrowser", e)
        import webbrowser
        webbrowser.open(url)


def play_sound(name: str):
    """Play a Windows system sound alias (e.g. "SystemAsterisk") or an
    absolute .wav path (C:\\Windows\\Media ships plenty). Async, silent
    no-op on failure. The event→sound mapping is auditioned and set in W6."""
    if not name:
        return
    try:
        import winsound
        flags = winsound.SND_ASYNC | winsound.SND_NODEFAULT
        if os.path.isabs(name):
            winsound.PlaySound(name, winsound.SND_FILENAME | flags)
        else:
            winsound.PlaySound(name, winsound.SND_ALIAS | flags)
    except Exception:
        pass
