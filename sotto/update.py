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
from .platform import IS_LINUX, IS_MACOS, IS_WINDOWS, alert

log = logging.getLogger("sotto")

_RELEASES_API_DEFAULT = \
    "https://api.github.com/repos/psancheti6666/sotto/releases/latest"
RELEASES_PAGE = "https://github.com/psancheti6666/sotto/releases"


def _releases_api() -> str:
    """SOTTO_RELEASES_API: test seam — lets the friend-test round point the
    whole flow at a localhost fixture without code edits. Read at call time
    (an import-time read would freeze before a test can set it)."""
    return os.environ.get("SOTTO_RELEASES_API", _RELEASES_API_DEFAULT)
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


def asset_suffix(system: str = None, machine: str = None, bundle: str = None):
    """The release-asset suffix this install updates from, or None where no
    artifact exists (arm64 Linux, Windows, unbundled checkouts) — None keeps
    the updater silent. On Linux the BUNDLE decides (a deb must never be
    offered to an AppImage user or vice versa). Naming convention:
    Sotto-<ver>-apple-silicon.dmg / -intel.dmg / -amd64.deb / -x86_64.AppImage."""
    system = system if system is not None else platform.system().lower()
    machine = machine if machine is not None else platform.machine()
    if system == "darwin":
        return "-apple-silicon.dmg" if machine == "arm64" else "-intel.dmg"
    if system == "linux" and machine in ("x86_64", "amd64"):
        if bundle is None and IS_LINUX:
            from . import update_linux
            bundle = update_linux.bundle_type()
        if bundle == "deb":
            return "-amd64.deb"
        if bundle == "appimage":
            return "-x86_64.AppImage"
    return None


def evaluate(release: dict, current: str, suffix: str):
    """Given the /releases/latest JSON, return {version, url, name} when it
    is a real newer release with an asset for this platform (matched by
    asset_suffix — a bare machine string would happily match a DMG on Linux)
    — else None. Linux assets (.deb/.AppImage) must publish their detached
    signature alongside (sig_url); a release without one is not offered.
    (DMGs carry their own codesign identity instead.)"""
    tag = (release.get("tag_name") or "").lstrip("v")
    if not tag or not suffix or release.get("draft") or release.get("prerelease"):
        return None
    if _parse(tag) <= _parse(current):
        return None
    assets = release.get("assets") or []
    for asset in assets:
        name = asset.get("name") or ""
        if name.endswith(suffix) and asset.get("browser_download_url"):
            info = {"version": tag, "url": asset["browser_download_url"],
                    "name": name}
            if not suffix.endswith(".dmg"):
                sig = next((a for a in assets
                            if a.get("name") == name + ".sig"
                            and a.get("browser_download_url")), None)
                if sig is None:
                    log.warning("release %s has %s but no .sig — skipping",
                                tag, name)
                    return None
                info["sig_url"] = sig["browser_download_url"]
            return info
    return None


def evaluate_notify(release: dict, current: str):
    """Windows notify-and-open (docs/windows-app.md W9 phase 1.5): is there
    ANY real newer release? Returns {version, page} for the "open the
    download page" flow. No asset or signature requirement — deliberately,
    because nothing is downloaded or executed; the moment a Windows install
    channel exists, that flow goes through evaluate() and its gates."""
    tag = (release.get("tag_name") or "").lstrip("v")
    if not tag or release.get("draft") or release.get("prerelease"):
        return None
    if _parse(tag) <= _parse(current):
        return None
    return {"version": tag, "page": release.get("html_url") or RELEASES_PAGE}


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
    """True only in a released bundle — never a checkout (run.sh git-pulls).
    macOS: the released Sotto.app (not Sotto Dev). Linux: an installed
    package (SOTTO_BUNDLE set by the deb's /usr/bin/sotto launcher).
    Windows: False BY DESIGN, both bundle kinds (docs/windows-app.md W9):
    "msix" = the Store owns updates for its installs, a self-updater would
    fight it; "exe" = no distribution channel exists until Round C's
    verdict — if it lands on Inno, the signature-gated backend (the L8
    pattern) gets built THEN, never a package-name check."""
    if IS_WINDOWS:
        return False
    if IS_LINUX:
        from . import update_linux
        return update_linux.bundle_type() is not None
    from . import menubar
    if not menubar.running_in_bundle():
        return False
    from Foundation import NSBundle
    return NSBundle.mainBundle().bundleIdentifier() == RELEASE_BUNDLE_ID


def menu_available() -> bool:
    """Gates ONLY the tray/menu "Check for Updates…" item. Windows shows it
    even though enabled() is False there: the item runs the notify-and-open
    flow (check → tell → open the download page), which needs no install
    channel. enabled() keeps gating auto-install and the scheduled check."""
    return enabled() or IS_WINDOWS


def check():
    """One API call. Returns evaluate()'s verdict; raises on network trouble."""
    suffix = asset_suffix()
    if suffix is None:
        return None
    import requests
    r = requests.get(_releases_api(), timeout=10,
                     headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    return evaluate(r.json(), __version__, suffix)


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
                # Scheduled checks stay gentle: a notification banner with
                # Update Now / Later buttons. The modal dialog is reserved
                # for the user-initiated menu check (and the fallback when
                # notifications aren't permitted).
                if not _post_banner(info):
                    _offer(info)
        time.sleep(POLL_S)


def _manual_check():
    if IS_WINDOWS:
        _manual_check_windows()
        return
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


def _manual_check_windows():
    """Notify-and-open (W9 phase 1.5, by request from the 2026-07-22 friend
    round): no Windows install channel exists yet, so instead of the
    download-and-swap flow, tell the user and take them to the release page.
    Runs on check_from_menu's worker thread — the blocking ask() is fine."""
    try:
        import requests
        r = requests.get(_releases_api(), timeout=10,
                         headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
        info = evaluate_notify(r.json(), __version__)
    except Exception as e:
        alert("Couldn't check for updates",
              f"Sotto couldn't reach GitHub: {e}")
        return
    mark_checked(STATE_PATH)
    if info is None:
        alert("You're up to date",
              f"Sotto {__version__} is the latest version.")
        return
    from .platform import windows as win
    if win.ask(f"Sotto {info['version']} is available",
               f"You're using {__version__}. Open the download page? "
               "(Download the new zip and replace your Sotto folder — "
               "in-app updates are coming with the installer.)"):
        win.open_url(info["page"])


def _offer(info):
    if _update_lock.locked():
        _progress_front()
        return
    # Deliberately terse: updates keeping your data/permissions is table
    # stakes — spelling it out ("…are untouched") only plants the doubt.
    if not _ask(f"Sotto {info['version']} is available",
                f"You're using {__version__}. Update and relaunch now?"):
        return
    _run_update(info)


def _run_update(info):
    """Download + install under the single-flight lock. The user has already
    consented (dialog button or notification action)."""
    if not _update_lock.acquire(blocking=False):
        _progress_front()
        return
    try:
        _progress_show(f"Downloading Sotto {info['version']}…")
        try:
            download_and_install(info)
        except Exception as e:
            _progress_hide()
            log.error("update failed: %s", e)
            if IS_LINUX:
                from . import update_linux
                if update_linux.bundle_type() == "appimage":
                    manual = ("download the new AppImage from github.com/"
                              "psancheti6666/sotto/releases and replace "
                              "your current file with it")
                else:
                    manual = ("download the .deb from github.com/"
                              "psancheti6666/sotto/releases and open it "
                              "to install")
            else:
                manual = ("download the DMG from github.com/psancheti6666/"
                          "sotto/releases and drag Sotto to Applications")
            alert("Update failed", f"{e}\n\nYou can install manually: {manual}.")
    finally:
        _update_lock.release()


# ------------------------------------------------------ notification banner
# The scheduled check surfaces as a macOS notification banner (Sotto icon,
# Update Now / Later buttons on hover) instead of a modal dialog. Permission
# is requested lazily — the first time an update is actually found — so
# first-run stays three prompts, not four. Denied → dialog fallback.

_pending_banner = None   # info dict the posted banner refers to
_nc_delegate = None      # retained; center.delegate is weak


def _post_banner(info) -> bool:
    """Post the update notification. False → caller shows the dialog."""
    global _pending_banner, _nc_delegate
    if not IS_MACOS:
        return False  # Linux scheduled checks use the zenity dialog directly
    try:
        import UserNotifications as UN

        center = UN.UNUserNotificationCenter.currentNotificationCenter()

        granted = {"ok": False}
        done = threading.Event()

        def auth_cb(ok, _error):
            granted["ok"] = bool(ok)
            done.set()

        center.requestAuthorizationWithOptions_completionHandler_(
            UN.UNAuthorizationOptionAlert, auth_cb)
        # First ever call shows the system permission prompt — give the user
        # time; later calls resolve instantly from the stored decision.
        done.wait(timeout=120)
        if not granted["ok"]:
            return False

        if _nc_delegate is None:
            from Foundation import NSObject

            class _NCDelegate(NSObject):
                def userNotificationCenter_willPresentNotification_withCompletionHandler_(
                        self, _center, _notification, handler):
                    # Sotto is technically frontmost-running; without this,
                    # macOS suppresses banners from the posting app.
                    handler(UN.UNNotificationPresentationOptionBanner)

                def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
                        self, _center, response, handler):
                    action = response.actionIdentifier()
                    handler()
                    info = _pending_banner
                    if action == "SOTTO_UPDATE_LATER":
                        return
                    if info is None:
                        # stale banner from a previous run — re-check instead
                        check_from_menu()
                    elif action == "SOTTO_UPDATE_NOW":
                        threading.Thread(target=_run_update, args=(info,),
                                         daemon=True).start()
                    else:  # clicked the banner body → the full dialog
                        threading.Thread(target=_offer, args=(info,),
                                         daemon=True).start()

            _nc_delegate = _NCDelegate.alloc().init()
            center.setDelegate_(_nc_delegate)

        act_now = UN.UNNotificationAction.actionWithIdentifier_title_options_(
            "SOTTO_UPDATE_NOW", "Update Now",
            UN.UNNotificationActionOptionForeground)
        act_later = UN.UNNotificationAction.actionWithIdentifier_title_options_(
            "SOTTO_UPDATE_LATER", "Later", 0)
        category = (UN.UNNotificationCategory
                    .categoryWithIdentifier_actions_intentIdentifiers_options_(
                        "SOTTO_UPDATE", [act_now, act_later], [], 0))
        center.setNotificationCategories_({category})

        content = UN.UNMutableNotificationContent.alloc().init()
        content.setTitle_(f"Sotto {info['version']} is available")
        content.setBody_("Sotto will install it and relaunch itself.")
        content.setCategoryIdentifier_("SOTTO_UPDATE")
        request = UN.UNNotificationRequest.requestWithIdentifier_content_trigger_(
            "sotto-update", content, None)
        center.addNotificationRequest_withCompletionHandler_(request, None)
        _pending_banner = info
        return True
    except Exception as e:  # notification stack unavailable → dialog
        log.warning("update banner failed (%s) — falling back to dialog", e)
        return False


# --------------------------------------------------------- progress window
# A small floating "Updating Sotto" window: determinate bar while the DMG
# downloads (we know content-length), indeterminate while installing. All
# AppKit access is dispatched to the main thread; the download worker only
# calls these wrappers. On Linux each wrapper forwards to update_linux's
# zenity-subprocess equivalents (child processes — no main-thread choreography
# needed, same reasoning as platform.alert()).

def _progress_show(text: str):
    if IS_LINUX:
        from . import update_linux
        return update_linux.progress_show(text)
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
    if IS_LINUX:
        from . import update_linux
        return update_linux.progress_set(text, fraction)
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
    if IS_LINUX:
        from . import update_linux
        return update_linux.progress_hide()
    from .platform.macos import _on_main
    _on_main(lambda: _progress and _progress["win"].orderOut_(None))


def _progress_front():
    if IS_LINUX:
        return  # zenity window manages its own stacking
    from .platform.macos import _on_main
    _on_main(lambda: _progress and _progress["win"].makeKeyAndOrderFront_(None))


def _ask(title: str, text: str) -> bool:
    """Two-button modal on the main thread; safe to call from any thread.
    True = Update Now."""
    if IS_LINUX:
        from . import update_linux
        return update_linux.ask(title, text)
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
    if IS_LINUX:
        from . import update_linux
        return update_linux.download_and_install(info, _progress_set)
    from Foundation import NSBundle
    target = NSBundle.mainBundle().bundlePath()
    if not target.endswith(".app"):
        raise RuntimeError(f"not running from an app bundle ({target})")

    import requests
    workdir = tempfile.mkdtemp(prefix="sotto-update-")
    # basename: the asset name comes from the release JSON — never let it
    # path-traverse out of the workdir
    dmg = os.path.join(workdir, os.path.basename(info["name"]))
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
