# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Linux first-run logic: honest permission checks + fix actions + gating.

The Tk windows live in firstrun_tk.py; model detection/download and the
pending-marker flow are reused from firstrun.py (already pure). Every check
here reads REAL state at call time — opening devices, running probes — never
a cached flag; a lying green row cost days of debugging on macOS.
"""

import logging
import os
import shutil
import subprocess
import sys
import threading

from . import firstrun
from . import hotkey_evdev  # module ref: _list_raw stays patchable
from .hotkey_evdev import KEY_CODES, _KEY_A

log = logging.getLogger("sotto")

HELPER = "/usr/libexec/sotto/sotto-perms"
AUTOSTART_PATH = os.path.expanduser("~/.config/autostart/sotto.desktop")
YDOTOOLD_UNIT = os.path.expanduser(
    "~/.config/systemd/user/sotto-ydotoold.service")


def bundle_type() -> str | None:
    """"deb" | "appimage" | None (source checkout / bare onedir)."""
    if not getattr(sys, "frozen", False):
        return None
    if os.environ.get("APPIMAGE"):
        return "appimage"
    if os.environ.get("SOTTO_BUNDLE") == "deb":
        return "deb"
    return None


# ------------------------------------------------------------------ checks --

def input_ok(hotkey: str = "ctrl_r", evdev=None) -> bool:
    """Can we actually read a keyboard? Opens event devices and inspects key
    capabilities — os.access lies under ACLs, and evdev.list_devices silently
    filters unreadable nodes, so nothing short of a real open is honest."""
    if evdev is None:
        try:
            import evdev
        except ImportError:
            return False
    vk = KEY_CODES.get(hotkey) or KEY_CODES["ctrl_r"]
    for path in hotkey_evdev._list_raw():
        try:
            dev = evdev.InputDevice(path)
        except (PermissionError, OSError):
            continue
        try:
            keys = dev.capabilities().get(evdev.ecodes.EV_KEY) or []
        except OSError:
            continue
        finally:
            dev.close()
        if vk in keys and _KEY_A in keys:
            return True
    return False


def uinput_ok(opener=os.open) -> bool:
    """Writable /dev/uinput (ydotool's injection path on Wayland)."""
    try:
        fd = opener("/dev/uinput", os.O_WRONLY)
    except OSError:
        return False
    os.close(fd)
    return True


def injection_ok() -> bool:
    """Run the real injector probe chain; green only when a typing-capable
    injector won (the clipboard+notification fallback is not 'working')."""
    from . import inject_linux
    try:
        chain = inject_linux.build_injector()
        first = type(chain._injectors[0]).__name__
    except Exception:
        return False
    return first != "_ClipboardNotifyInjector"


def autostart_ok() -> bool:
    return os.path.exists(AUTOSTART_PATH)


def statuses(cfg) -> dict:
    """Row states for the walkthrough. 'models' folds engine + both models —
    informational, never gates (same rule as macOS)."""
    return {
        "input": input_ok(cfg.hotkey),
        "injection": injection_ok(),
        "models": not setup_missing(cfg),
        "autostart": autostart_ok(),
    }


def needed(cfg) -> bool:
    """Show the walkthrough? Permissions only — models/engine download on
    their own screen, and autostart is optional."""
    force = os.environ.get("SOTTO_FIRSTRUN")
    if force == "1":
        return True
    if force == "0":
        return False
    return not (input_ok(cfg.hotkey) and injection_ok())


def setup_missing(cfg) -> bool:
    """Anything left to download? Models, plus (Linux-only) the LLM engine —
    unless a running server is adoptable or a system ollama exists."""
    if firstrun.models_missing(cfg):
        return True
    return engine_missing(cfg)


def engine_missing(cfg) -> bool:
    from . import llm_server, ollama_runtime
    if llm_server._reachable(cfg.ollama_url):
        return False
    return ollama_runtime.resolve() is None


# ------------------------------------------------------------- fix actions --

def fix_input_argv() -> list:
    """pkexec invocation for the Keyboard-access Fix button. The .deb install
    already laid the helper down; an AppImage bootstraps it on first click
    (generic polkit admin prompt — expected) and uses the installed helper
    afterwards."""
    user = os.environ.get("USER", "")
    if bundle_type() == "appimage" and not os.path.exists(HELPER):
        payload = os.path.join(os.environ.get("APPDIR", ""),
                               "share", "sotto-setup")
        return ["pkexec", os.path.join(payload, "sotto-perms"),
                "bootstrap", payload, user]
    return ["pkexec", HELPER, "apply", user]


def fix_input():
    """Run the pkexec helper (blocking — call from a worker thread), then log
    the resulting device ACL state for diagnosis."""
    argv = fix_input_argv()
    log.info("fix input: %s", " ".join(argv))
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=180)
        log.info("sotto-perms exited %d: %s", r.returncode,
                 (r.stdout + r.stderr).strip())
        v = subprocess.run(["pkexec", HELPER, "verify"] if r.returncode == 0
                           else ["ls", "-l", "/dev/uinput"],
                           capture_output=True, text=True, timeout=60)
        log.info("device state after fix:\n%s", (v.stdout + v.stderr).strip())
    except Exception as e:
        log.warning("fix input failed: %s", e)


def fix_injection():
    """GNOME-Wayland path: run ydotoold as a user service. No root — uinput
    access came from the Keyboard-access fix; a user-scoped daemon also
    avoids ydotoold-as-root's 0600 socket that locks clients out."""
    ydotoold = shutil.which("ydotoold")
    if not ydotoold:
        log.warning("fix injection: ydotoold not installed")
        return
    os.makedirs(os.path.dirname(YDOTOOLD_UNIT), exist_ok=True)
    with open(YDOTOOLD_UNIT, "w") as f:
        f.write("# Created by Sotto (io.github.psancheti6666.sotto)\n"
                "[Unit]\nDescription=ydotool daemon for Sotto dictation\n\n"
                "[Service]\n"
                f"ExecStart={ydotoold} --socket-path=%t/.ydotool_socket\n"
                "Restart=on-failure\n\n"
                "[Install]\nWantedBy=default.target\n")
    for argv in (["systemctl", "--user", "daemon-reload"],
                 ["systemctl", "--user", "enable", "--now",
                  "sotto-ydotoold.service"]):
        try:
            subprocess.run(argv, capture_output=True, timeout=30)
        except Exception as e:
            log.warning("fix injection: %s failed: %s", argv, e)


def fix_autostart():
    exec_line = (os.environ.get("APPIMAGE")
                 or ("/usr/bin/sotto" if bundle_type() == "deb"
                     else sys.executable))
    os.makedirs(os.path.dirname(AUTOSTART_PATH), exist_ok=True)
    with open(AUTOSTART_PATH, "w") as f:
        f.write("[Desktop Entry]\nType=Application\nName=Sotto\n"
                f"Exec={exec_line}\nX-GNOME-Autostart-enabled=true\n")


def run_fix(action):
    """Fix buttons block on pkexec/systemctl — run them off the UI thread;
    the walkthrough's 1 s tick picks up the state change."""
    threading.Thread(target=action, daemon=True).start()


# -------------------------------------------------------------------- rows --

# (key, title, detail, btn_title, action) — same shape as firstrun.ROWS.
ROWS = [
    ("input", "Keyboard access",
     "Sotto sees the hotkey at the kernel level. Nothing is recorded until "
     "you hold it. Asks for your password once.", "Fix", fix_input),
    ("injection", "Typing",
     "Lets Sotto type the cleaned-up text at your cursor. On GNOME this "
     "starts a small background helper (ydotool).", "Fix", fix_injection),
    ("models", "Models & engine",
     "~3–4 GB, one time. Sotto downloads these by itself after setup.",
     None, None),
    ("autostart", "Start at login",
     "Optional: launch Sotto automatically when you log in.",
     "Enable", fix_autostart),
]

GATING = ("input", "injection")  # models/autostart never block Start


# ---------------------------------------------------------------- relaunch --

def relaunch_argv() -> list:
    if os.environ.get("APPIMAGE"):
        return [os.environ["APPIMAGE"]]
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "sotto"]


def relaunch():
    """Replace this process with a fresh Sotto — same flow as macOS (the
    fresh process re-runs every gate; consolidate_model_stores runs before
    huggingface_hub is ever imported there)."""
    os.environ.pop("SOTTO_FIRSTRUN", None)  # it leaked once on macOS and
    argv = relaunch_argv()                  # looped the window — never again
    log.info("relaunching: %s", argv)
    os.execv(argv[0], argv)
