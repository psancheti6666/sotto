# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Windows first-run logic (docs/windows-app.md W5): checks + fix actions +
gating for the shared Tk windows in firstrun_tk.py.

No permission walkthrough in the macOS/Linux sense — hooks and SendInput
need no grants on Windows. What remains honest-checkable: the microphone.
A classic (non-MSIX) desktop app gets NO OS mic prompt — capture is
governed by the global "Let desktop apps access your microphone" toggle,
and when it's off, recording silently yields nothing. So the mic row reads
the REAL toggle from the registry at call time and its Fix opens the exact
Settings page; it gates Start (a first dictation that types nothing would
be a broken first impression), but every uncertainty fails OPEN — a
missing key or unreadable registry must never lock a user out on a
Windows build we haven't met.

The models/consent row and download screen are the same shared Tk flow the
Linux app uses (the ≥100 MB consent gate covers the ~1.5 GB engine zip +
~3-4 GB models). Relaunch is spawn-then-exit — os.execv has broken
semantics on Windows (new pid, argv mangling, early console return).
"""

import logging
import os
import subprocess
import sys
import threading

from . import firstrun

log = logging.getLogger("sotto")

_CONSENT_KEY = (r"SOFTWARE\Microsoft\Windows\CurrentVersion"
                r"\CapabilityAccessManager\ConsentStore\microphone")
AUTOSTART_DIR = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
AUTOSTART_PATH = os.path.join(AUTOSTART_DIR, "Sotto.lnk")

SUBTITLE = ("Private dictation, fully on this computer. "
            "One quick check and you're set.")

_APPMODEL_ERROR_NO_PACKAGE = 15700


def bundle_type() -> str | None:
    """"msix" (package identity present) | "exe" (frozen, unpackaged) |
    None (source checkout). Drives the first-run gate and, later, the
    updater's channel selection (Store updates MSIX; W9)."""
    if not getattr(sys, "frozen", False):
        return None
    try:
        import ctypes
        length = ctypes.c_uint32(0)
        rc = ctypes.windll.kernel32.GetCurrentPackageFullName(
            ctypes.byref(length), None)
        if rc != _APPMODEL_ERROR_NO_PACKAGE:
            return "msix"
    except Exception:
        pass
    return "exe"


# ------------------------------------------------------------------ checks --

def _read_mic_consent(subkey: str) -> str | None:
    """The registry 'Value' under the mic ConsentStore subkey ("Allow" /
    "Deny"), or None when unreadable. Split out so mic_ok unit-tests with a
    fake on macOS (winreg does not exist there)."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                        _CONSENT_KEY + "\\" + subkey) as key:
        value, _ = winreg.QueryValueEx(key, "Value")
        return value


def mic_ok(reader=None) -> bool:
    """Is microphone access on for this app class? MSIX packages get a real
    OS prompt (per-app consent, handled by Windows); classic apps hang off
    the global NonPackaged toggle. Every failure mode returns True — a
    Windows build quirk must never false-block Start."""
    read = reader or _read_mic_consent
    subkey = "NonPackaged" if bundle_type() != "msix" else None
    if subkey is None:
        return True  # MSIX: the OS prompt owns this; nothing to pre-check
    try:
        return read(subkey) != "Deny"
    except Exception:
        return True


def autostart_ok() -> bool:
    return os.path.exists(AUTOSTART_PATH)


def statuses(cfg) -> dict:
    return {
        "mic": mic_ok(),
        "models": not setup_missing(cfg),
        "autostart": autostart_ok(),
    }


def needed(cfg) -> bool:
    """Show the walkthrough? Anything left to download, or the mic toggle
    off (dictation would silently type nothing — worth the window). Same
    SOTTO_FIRSTRUN / pending-marker contract as Linux."""
    force = os.environ.get("SOTTO_FIRSTRUN")
    if force == "1":
        return True
    if force == "0":
        return False
    if os.path.exists(firstrun.PENDING_MARKER):
        return False  # consent given; setup continuing post-relaunch
    return setup_missing(cfg) or not mic_ok()


def setup_missing(cfg) -> bool:
    """Anything left to download? Models, plus the LLM engine — unless a
    running server is adoptable or a system ollama exists. Same shape as
    firstrun_linux (the engine now resolves on Windows too, W4)."""
    if firstrun.models_missing(cfg):
        return True
    return engine_missing(cfg)


def engine_missing(cfg) -> bool:
    from . import llm_server, ollama_runtime
    if llm_server._reachable(cfg.ollama_url):
        return False
    return ollama_runtime.resolve() is None


# ------------------------------------------------------------- fix actions --

def fix_mic():
    """Open the exact Settings page; the walkthrough's 1 s tick sees the
    toggle flip. ms-settings: URIs only resolve through the shell."""
    os.startfile("ms-settings:privacy-microphone")


def autostart_argv(target: str) -> list:
    """Pure argv builder for the Startup-folder shortcut (unit-tested;
    .lnk creation needs COM, which powershell wraps in one line)."""
    script = ("$ws = New-Object -ComObject WScript.Shell; "
              f"$s = $ws.CreateShortcut('{AUTOSTART_PATH}'); "
              f"$s.TargetPath = '{target}'; "
              "$s.WorkingDirectory = "
              f"'{os.path.dirname(target) or '.'}'; "
              "$s.Save()")
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]


def fix_autostart():
    target = sys.executable
    os.makedirs(AUTOSTART_DIR, exist_ok=True)
    subprocess.run(autostart_argv(target), check=False, timeout=30,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def run_fix(action):
    """Fix buttons may block (Settings launch, powershell) — run off the UI
    thread; the walkthrough's tick picks up the state change."""
    threading.Thread(target=action, daemon=True).start()


# -------------------------------------------------------------------- rows --

# (key, title, detail, btn_title, action) — same shape as firstrun_linux.ROWS.
ROWS = [
    ("mic", "Microphone",
     "Windows lets desktop apps use the microphone only while a system "
     "toggle is on. Sotto listens only while you hold the hotkey.",
     "Open Settings", fix_mic),
    ("models", "Models & engine",
     "~4–5 GB, one time. Sotto downloads these by itself after setup.",
     None, None),
    ("autostart", "Start at login",
     "Optional: launch Sotto automatically when you sign in.",
     "Enable", fix_autostart),
]

GATING = ("mic",)  # models consent is the walkthrough's checkbox; autostart
                   # never blocks. mic_ok fails open, so this can only gate
                   # on a POSITIVE "Deny" read.


# ---------------------------------------------------------------- relaunch --

def relaunch_argv() -> list:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "sotto"]


def relaunch():
    """Spawn a fresh Sotto and exit this process — NEVER os.execv here
    (Windows execv gives the child a new pid, mangles spaced argv, and
    returns the parent's console early). The download screen's ollama child
    is shut down first for the same reason as Linux: the fresh process must
    spawn and OWN the engine, not adopt an orphan."""
    try:
        from . import llm_server
        llm_server.shutdown()
    except Exception:
        pass
    env = dict(os.environ)
    env.pop("SOTTO_FIRSTRUN", None)  # leaked once on macOS — never again
    env.pop("HF_HUB_OFFLINE", None)
    argv = relaunch_argv()
    log.info("relaunching: %s", argv)
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    subprocess.Popen(argv, env=env, close_fds=True, creationflags=flags)
    os._exit(0)  # daemon threads only; ollama already shut down above
