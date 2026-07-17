# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Self-update for the released Sotto.app bundle.

One GET to the GitHub releases API — the app's only network request beyond
localhost (documented in README + config.toml; update_check_days = 0 turns the
scheduled check off). When a newer release exists, an alert offers Update Now /
Later; "Later" stays quiet until the next scheduled check. Update Now downloads
the DMG for this machine's chip, stages the app out of it, then a detached
shell swaps /Applications/Sotto.app after this process exits and relaunches.

Smoothness notes, hard-won elsewhere in this project:
- TCC permissions survive the swap: the release signing identity is stable
  (bundle id + cert leaf), so the new binary matches the existing grants.
- No Gatekeeper "Open Anyway" on updates: quarantine is stamped by browsers
  (LSFileQuarantineEnabled apps); a DMG we download ourselves carries none.
- Only the release app self-updates. Sotto Dev and source checkouts are
  excluded — run.sh already self-updates via git pull.
"""

import json
import logging
import os
import platform
import re
import shlex
import subprocess
import tempfile
import threading
import time

from . import __version__
from .config import CONFIG_DIR
from .platform import alert

log = logging.getLogger("sotto")

RELEASES_API = "https://api.github.com/repos/psancheti6666/sotto/releases/latest"
RELEASE_BUNDLE_ID = "io.github.psancheti6666.sotto"
STATE_PATH = os.path.join(CONFIG_DIR, "update-state.json")
INITIAL_DELAY_S = 30.0    # let launch (and any first-run alert) settle first
POLL_S = 3600.0           # how often the scheduled thread re-checks if due

# One update flow at a time: clicking Update Now must not be able to start a
# second download/swap race (live-tested: the button got clicked twice).
_update_lock = threading.Lock()
_progress = None          # cached progress window parts; main-thread only


# ---------------------------------------------------- pure logic (unit-tested)

def _parse(version: str) -> tuple:
    """'v0.10.0' → (0, 10, 0). Numeric compare, so 0.10 > 0.9."""
    return tuple(int(p) for p in re.findall(r"\d+", version)[:3])


def evaluate(release: dict, current: str, machine: str):
    """Given the /releases/latest JSON, return {version, url, name} when it is
    a real newer release with a DMG for this chip — else None."""
    tag = (release.get("tag_name") or "").lstrip("v")
    if not tag or release.get("draft") or release.get("prerelease"):
        return None
    if _parse(tag) <= _parse(current):
        return None
    arch = "apple-silicon" if machine == "arm64" else "intel"
    for asset in release.get("assets") or []:
        name = asset.get("name") or ""
        if name.endswith(f"-{arch}.dmg") and asset.get("browser_download_url"):
            return {"version": tag, "url": asset["browser_download_url"],
                    "name": name}
    return None


def due(state_path: str, check_days: float, now: float = None) -> bool:
    try:
        with open(state_path) as f:
            last = json.load(f).get("last_check", 0)
    except (OSError, ValueError):
        last = 0
    return (now if now is not None else time.time()) - last >= check_days * 86400


def mark_checked(state_path: str, now: float = None):
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w") as f:
        json.dump({"last_check": now if now is not None else time.time()}, f)


# ------------------------------------------------------------------- plumbing

def enabled() -> bool:
    """True only in the released Sotto.app — never Sotto Dev or a checkout."""
    from . import menubar
    if not menubar.running_in_bundle():
        return False
    from Foundation import NSBundle
    return NSBundle.mainBundle().bundleIdentifier() == RELEASE_BUNDLE_ID


def check():
    """One API call. Returns evaluate()'s verdict; raises on network trouble."""
    import requests
    r = requests.get(RELEASES_API, timeout=10,
                     headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    return evaluate(r.json(), __version__, platform.machine())


def start_scheduled(cfg):
    if cfg.update_check_days <= 0 or not enabled():
        return
    threading.Thread(target=_scheduled_loop, args=(cfg,), daemon=True).start()


def check_from_menu(_sender=None):
    # Menu actions fire on the main thread; the check is network + a modal
    # that _ask() dispatches back to the main thread — so hop off it first.
    threading.Thread(target=_manual_check, daemon=True).start()


def _scheduled_loop(cfg):
    time.sleep(INITIAL_DELAY_S)
    while True:
        if due(STATE_PATH, cfg.update_check_days):
            info = None
            try:
                info = check()
            except Exception as e:
                log.warning("update check failed: %s", e)
            mark_checked(STATE_PATH)
            if info:
                _offer(info)
        time.sleep(POLL_S)


def _manual_check():
    if _update_lock.locked():   # an update is already running — show it
        _progress_front()
        return
    try:
        info = check()
    except Exception as e:
        alert("Couldn't check for updates",
              f"Sotto couldn't reach GitHub: {e}")
        return
    mark_checked(STATE_PATH)
    if info is None:
        alert("You're up to date",
              f"Sotto {__version__} is the latest version.")
    else:
        _offer(info)


def _offer(info):
    if not _update_lock.acquire(blocking=False):
        _progress_front()
        return
    try:
        if not _ask(f"Sotto {info['version']} is available",
                    f"You're using {__version__}. Sotto will download and "
                    "install the update, then relaunch itself. Your "
                    "settings, history, and permissions are untouched."):
            return
        _progress_show(f"Downloading Sotto {info['version']}…")
        try:
            download_and_install(info)
        except Exception as e:
            _progress_hide()
            log.error("update failed: %s", e)
            alert("Update failed",
                  f"{e}\n\nYou can install manually: download the DMG from "
                  "github.com/psancheti6666/sotto/releases and drag Sotto "
                  "to Applications.")
    finally:
        _update_lock.release()


# --------------------------------------------------------- progress window
# A small floating "Updating Sotto" window: determinate bar while the DMG
# downloads (we know content-length), indeterminate while installing. All
# AppKit access is dispatched to the main thread; the download worker only
# calls these wrappers.

def _progress_show(text: str):
    from .platform.macos import _on_main

    def go():
        global _progress
        if _progress is None:
            from AppKit import (
                NSBackingStoreBuffered, NSFloatingWindowLevel, NSMakeRect,
                NSProgressIndicator, NSTextField, NSWindow,
                NSWindowStyleMaskTitled)
            w, h = 400, 92
            win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, w, h), NSWindowStyleMaskTitled,
                NSBackingStoreBuffered, False)
            win.setTitle_("Updating Sotto")
            win.setReleasedWhenClosed_(False)
            win.setLevel_(NSFloatingWindowLevel)
            label = NSTextField.labelWithString_("")
            label.setFrame_(NSMakeRect(20, h - 40, w - 40, 18))
            bar = NSProgressIndicator.alloc().initWithFrame_(
                NSMakeRect(20, h - 68, w - 40, 16))
            bar.setMinValue_(0)
            bar.setMaxValue_(1)
            win.contentView().addSubview_(label)
            win.contentView().addSubview_(bar)
            win.center()
            _progress = {"win": win, "bar": bar, "label": label}
        _progress["label"].setStringValue_(text)
        _progress["bar"].setIndeterminate_(True)
        _progress["bar"].startAnimation_(None)
        from AppKit import NSApp
        NSApp.activateIgnoringOtherApps_(True)
        _progress["win"].makeKeyAndOrderFront_(None)

    _on_main(go)


def _progress_set(text: str, fraction=None):
    from .platform.macos import _on_main

    def go():
        if _progress is None:
            return
        _progress["label"].setStringValue_(text)
        bar = _progress["bar"]
        if fraction is None:
            bar.setIndeterminate_(True)
            bar.startAnimation_(None)
        else:
            bar.stopAnimation_(None)
            bar.setIndeterminate_(False)
            bar.setDoubleValue_(fraction)

    _on_main(go)


def _progress_hide():
    from .platform.macos import _on_main
    _on_main(lambda: _progress and _progress["win"].orderOut_(None))


def _progress_front():
    from .platform.macos import _on_main
    _on_main(lambda: _progress and _progress["win"].makeKeyAndOrderFront_(None))


def _ask(title: str, text: str) -> bool:
    """Two-button modal on the main thread; safe to call from any thread.
    True = Update Now."""
    from .platform.macos import _on_main
    done = threading.Event()
    result = {"now": False}

    def go():
        try:
            from AppKit import NSAlert, NSAlertFirstButtonReturn, NSApp
            a = NSAlert.alloc().init()
            a.setMessageText_(title)
            a.setInformativeText_(text)
            a.addButtonWithTitle_("Update Now")
            a.addButtonWithTitle_("Later")
            NSApp.activateIgnoringOtherApps_(True)
            result["now"] = a.runModal() == NSAlertFirstButtonReturn
        except Exception:
            pass
        finally:
            done.set()

    _on_main(go)
    done.wait()
    return result["now"]


def download_and_install(info):
    """Download the DMG, stage Sotto.app out of it, then hand off to a
    detached shell that swaps the bundle once this process exits and reopens
    it. Runs on a worker thread; only the final terminate touches AppKit."""
    from Foundation import NSBundle
    target = NSBundle.mainBundle().bundlePath()
    if not target.endswith(".app"):
        raise RuntimeError(f"not running from an app bundle ({target})")

    import requests
    workdir = tempfile.mkdtemp(prefix="sotto-update-")
    dmg = os.path.join(workdir, info["name"])
    log.info("downloading %s", info["url"])
    with requests.get(info["url"], stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        got = 0
        with open(dmg, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                got += len(chunk)
                if total:
                    _progress_set(
                        f"Downloading Sotto {info['version']}… "
                        f"{got >> 20} of {total >> 20} MB", got / total)
    _progress_set("Installing… Sotto will relaunch itself in a moment.")

    mnt = os.path.join(workdir, "mnt")
    staged = os.path.join(workdir, "Sotto.app")
    subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly",
                    "-mountpoint", mnt, dmg], check=True, capture_output=True)
    try:
        src = os.path.join(mnt, "Sotto.app")
        if not os.path.isdir(src):
            raise RuntimeError("downloaded DMG does not contain Sotto.app")
        # ditto preserves the bundle exactly (symlinks, xattrs, signatures)
        subprocess.run(["ditto", src, staged], check=True, capture_output=True)
    finally:
        subprocess.run(["hdiutil", "detach", mnt], capture_output=True)

    q_target, q_staged, q_work = map(shlex.quote, (target, staged, workdir))
    # ditto (not mv) for the swap too: /var/folders and /Applications can sit
    # on different APFS volumes, where a directory mv fails cross-device.
    script = (f"sleep 1; rm -rf {q_target}; ditto {q_staged} {q_target}; "
              f"rm -rf {q_work}; open {q_target}")
    subprocess.Popen(["/bin/sh", "-c", script], start_new_session=True)
    log.info("update staged — relaunching as %s", info["version"])

    from .platform.macos import _on_main

    def quit_():
        from AppKit import NSApp
        NSApp.terminate_(None)

    _on_main(quit_)
