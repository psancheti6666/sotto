# Created by Pratik Sancheti / https://github.com/psancheti6666
"""First-run experience for the Sotto.app bundle — replaces setup.sh there.

On .app launch, fast offline checks decide whether setup is complete: mic /
Accessibility / Input Monitoring authorization, the Globe-key setting, and
both models present. If anything is missing, launch() shows a welcome window
(one row per item, live status, an action button each, progress bars for the
model downloads) and owns the process; when everything is green, it relaunches
the app once — Input Monitoring grants only take effect in a fresh process.

The git-checkout path never sees any of this (app.py gates on the bundle),
and every check degrades to "ok" off-macOS so the module imports anywhere.

Model stores: fresh machines download into ~/.sotto (hf/ for ASR via HF_HOME,
ollama/ for the LLM via OLLAMA_MODELS); machines that already hold the models
in the default stores (~/.cache/huggingface, ~/.ollama) keep using those — no
duplicate multi-GB downloads. SOTTO_FIRSTRUN=1 forces the window (preview),
=0 skips it.
"""
import logging
import os
import subprocess
import sys
import threading

log = logging.getLogger("sotto")

HF_DEFAULT_CACHE = os.path.expanduser("~/.cache/huggingface")
SOTTO_HF_HOME = os.path.expanduser("~/.sotto/hf")
OLLAMA_DEFAULT_STORE = os.path.expanduser("~/.ollama/models")
SOTTO_OLLAMA_STORE = os.path.expanduser("~/.sotto/ollama")
# Written when the welcome window shows; the next normal startup announces
# "setup complete" and removes it. Needed because macOS itself may quit &
# reopen Sotto when Input Monitoring is granted — the user then lands in a
# running app without ever clicking "Start Sotto".
PENDING_MARKER = os.path.expanduser("~/.sotto/.firstrun-pending")

_GREEN, _GRAY = "✓", "○"


# ---------------------------------------------------------------- checks --

def _hf_model_dir(model_id: str, cache_root: str) -> str:
    return os.path.join(cache_root, "hub",
                        "models--" + model_id.replace("/", "--"))


def asr_model_ok(model_id: str) -> bool:
    """True when the ASR model is present in any store we'd actually use:
    an explicit HF_HOME, the default HF cache, or Sotto's own store."""
    roots = [os.environ.get("HF_HOME") or "", HF_DEFAULT_CACHE, SOTTO_HF_HOME]
    for root in filter(None, roots):
        snap = os.path.join(_hf_model_dir(model_id, root), "snapshots")
        if os.path.isdir(snap) and os.listdir(snap):
            return True
    return False


def _manifest_path(store: str, model: str) -> str:
    name, _, tag = model.partition(":")
    return os.path.join(store, "manifests", "registry.ollama.ai", "library",
                        name, tag or "latest")


def llm_model_ok(model: str) -> bool:
    return any(os.path.isfile(_manifest_path(s, model))
               for s in (OLLAMA_DEFAULT_STORE, SOTTO_OLLAMA_STORE))


def mic_ok() -> bool:
    if sys.platform != "darwin":
        return True
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
        return AVCaptureDevice.authorizationStatusForMediaType_(
            AVMediaTypeAudio) == 3  # AVAuthorizationStatusAuthorized
    except Exception:
        return True  # can't tell — the OS prompt still fires on first use


def accessibility_ok() -> bool:
    if sys.platform != "darwin":
        return True
    try:
        from ApplicationServices import AXIsProcessTrusted
        return bool(AXIsProcessTrusted())
    except Exception:
        return True


def input_monitoring_ok() -> bool:
    """IOHIDCheckAccess, NOT CGPreflightListenEventAccess: once
    CGRequestListenEventAccess() has run in this process (our Open Settings
    button calls it), preflight answers YES forever after — the row went
    green the moment the button was clicked, grant or no grant
    (live-tested on a fresh machine). IOHIDCheckAccess reads the real TCC
    state: 0 granted, 1 denied, 2 not yet asked."""
    if sys.platform != "darwin":
        return True
    try:
        import ctypes
        iokit = ctypes.CDLL(
            "/System/Library/Frameworks/IOKit.framework/IOKit")
        iokit.IOHIDCheckAccess.argtypes = [ctypes.c_uint32]
        iokit.IOHIDCheckAccess.restype = ctypes.c_uint32
        return iokit.IOHIDCheckAccess(1) == 0  # kIOHIDRequestTypeListenEvent
    except Exception:
        try:
            import Quartz
            return bool(Quartz.CGPreflightListenEventAccess())
        except Exception:
            return True


def globe_key_ok() -> bool:
    """AppleFnUsageType must be 0 ("Do Nothing") or macOS opens the emoji
    picker on every fn press."""
    if sys.platform != "darwin":
        return True
    try:
        out = subprocess.run(
            ["defaults", "read", "com.apple.HIToolbox", "AppleFnUsageType"],
            capture_output=True, text=True, timeout=5)
        return out.stdout.strip() == "0"
    except Exception:
        return True


def statuses(cfg) -> dict:
    s = {
        "mic": mic_ok(),
        "accessibility": accessibility_ok(),
        "input_monitoring": input_monitoring_ok(),
        "asr_model": asr_model_ok(cfg.asr_model),
        "llm_model": llm_model_ok(cfg.ollama_model),
    }
    if cfg.hotkey == "fn":
        s["globe_key"] = globe_key_ok()
    return s


def needed(cfg) -> bool:
    """Show the welcome walkthrough? Permissions only — the model download
    is Sotto's job, not the user's, and happens on its own screen after the
    Input Monitoring quit-&-reopen (the user shouldn't wait out a ~3 GB
    download just to press Start)."""
    force = os.environ.get("SOTTO_FIRSTRUN")
    if force == "1":
        return True
    if force == "0":
        return False
    s = statuses(cfg)
    s.pop("asr_model")
    s.pop("llm_model")
    return not all(s.values())


def models_missing(cfg) -> bool:
    return not (asr_model_ok(cfg.asr_model) and llm_model_ok(cfg.ollama_model))


def announce_if_setup_just_finished(hotkey: str = "fn"):
    """One-time 'Sotto is ready' note on the first normal start after the
    welcome window — the restart that gets the user here may have been
    macOS's own quit-&-reopen for Input Monitoring, not their click."""
    if not os.path.exists(PENDING_MARKER):
        return
    try:
        os.remove(PENDING_MARKER)
    except OSError:
        return
    from .platform import IS_MACOS, alert
    name = _app_name()
    where = "in the menu bar" if IS_MACOS else "in the background"
    key = "fn" if IS_MACOS else hotkey.replace("ctrl_r", "Right Ctrl")
    alert(f"{name} is ready",
          f"Setup is complete and {name} is now running {where}.\n\n"
          f"Hold the {key} key anywhere, speak, release — the cleaned-up "
          "text lands at your cursor.")


def consolidate_model_stores(cfg):
    """Point downloads at ~/.sotto unless the default stores already hold the
    models. Must run before huggingface_hub is first imported (it snapshots
    HF_HOME at import); the ollama side lives in llm_server._spawn."""
    if not asr_model_ok(cfg.asr_model) and "HF_HOME" not in os.environ:
        os.environ["HF_HOME"] = SOTTO_HF_HOME


# --------------------------------------------------------------- actions --

def request_mic():
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
        AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVMediaTypeAudio, lambda ok: None)
    except Exception:
        pass


def request_accessibility():
    try:
        from ApplicationServices import (AXIsProcessTrustedWithOptions,
                                         kAXTrustedCheckOptionPrompt)
        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
    except Exception:
        pass
    _open_settings_pane("Privacy_Accessibility")


def request_input_monitoring():
    try:
        import Quartz
        Quartz.CGRequestListenEventAccess()
    except Exception:
        pass
    _open_settings_pane("Privacy_ListenEvent")


# Update-notification permission (release bundle only). OPTIONAL: it is
# deliberately kept out of statuses()/needed() — denying it must neither
# block Start Sotto nor re-open the walkthrough; update offers then simply
# arrive as a dialog instead of a banner (update.py falls back on its own).
# The authorization query is async, so the row reads a cache that each
# tick refreshes for the next one.

_notif_status = {"value": None}   # UNAuthorizationStatus; None = not fetched


def notifications_ok() -> bool:
    return _notif_status["value"] in (2, 3)   # authorized / provisional


def poll_notifications():
    try:
        import UserNotifications as UN
        UN.UNUserNotificationCenter.currentNotificationCenter() \
            .getNotificationSettingsWithCompletionHandler_(
                lambda s: _notif_status.update(value=s.authorizationStatus()))
    except Exception:
        pass


def request_notifications():
    try:
        import UserNotifications as UN
        UN.UNUserNotificationCenter.currentNotificationCenter() \
            .requestAuthorizationWithOptions_completionHandler_(
                UN.UNAuthorizationOptionAlert, lambda ok, err: None)
    except Exception:
        pass


def _open_settings_pane(anchor: str):
    subprocess.run(["open",
                    f"x-apple.systempreferences:com.apple.preference.security?{anchor}"],
                   check=False)


def fix_globe_key():
    """Send the user to the Keyboard pane to set "Press 🌐 key to" →
    "Do Nothing" themselves. Writing AppleFnUsageType with `defaults`
    persists the pref (and greens our check) but running apps keep the old
    cached value until re-login — live-tested: the emoji picker kept
    opening and hold-to-dictate typed the transcript into its search bar.
    The Settings UI applies the change immediately, so the green tick then
    means what it says."""
    subprocess.run(["open",
                    "x-apple.systempreferences:com.apple.Keyboard-Settings"
                    ".extension"], check=False)


def strip_quarantine():
    """Once Sotto is running, the user has by definition passed Gatekeeper —
    drop the quarantine flag from our own bundle so it is never re-evaluated.
    macOS 26 seals "Open Anyway" only after one more MANUAL launch, and
    first-run's automatic quit-&-reopen (Input Monitoring) races that —
    live-tested: users hit the malware screen twice. With the flag gone,
    every later launch (including our own relaunches) skips Gatekeeper.
    No-op when the flag is absent; the xattr walk over a ~700 MB bundle
    runs on a background thread."""
    try:
        from Foundation import NSBundle
        bundle = NSBundle.mainBundle().bundlePath()
        if not bundle.endswith(".app"):
            return
        threading.Thread(
            target=lambda: subprocess.run(
                ["xattr", "-rd", "com.apple.quarantine", bundle],
                capture_output=True),
            daemon=True).start()
    except Exception:
        pass


def relaunch():
    """Replace this process with a fresh one (Input Monitoring grants only
    apply to processes started after the grant)."""
    from Foundation import NSBundle
    bundle = NSBundle.mainBundle().bundlePath()
    env = {k: v for k, v in os.environ.items() if k != "SOTTO_FIRSTRUN"}
    # open passes its environment through — SOTTO_FIRSTRUN=1 (the forced
    # preview) must not leak or the fresh instance shows the window again.
    subprocess.Popen(["/bin/sh", "-c", f"sleep 1; open '{bundle}'"],
                     start_new_session=True, env=env)
    from AppKit import NSApp
    NSApp.terminate_(None)


# ------------------------------------------------------------- downloads --

# Approximate relative download sizes, so ONE bar fills smoothly across the
# separate downloads (bundled engine + speech model + cleanup model) instead of
# resetting 0→100 per file. The individual downloads are never shown as separate
# cycles — the user just sees a single percentage climb once (friend feedback,
# 2026-07-23: "three rounds of 0-100 … is something broken?").
_SEG_WEIGHTS = {"engine": 0.05, "asr": 0.30, "llm": 0.65}
_DL_LABEL = "Downloading Sotto's models"


class _DownloadBar:
    """Maps each download's own 0..1 progress onto a single MONOTONIC 0..1 bar
    with one unified label, spanning only the segments that will actually run."""

    def __init__(self, on_progress, keys):
        self._on = on_progress
        weights = [(k, _SEG_WEIGHTS.get(k, 0.1)) for k in keys]
        total = sum(w for _, w in weights) or 1.0
        self._base, self._span, acc = {}, {}, 0.0
        for k, w in weights:
            self._base[k] = acc / total
            self._span[k] = w / total
            acc += w
        self._last = 0.0

    def report(self, key, frac):
        if frac is None:  # indeterminate sub-step: hold the bar, keep one label
            self._on(f"{_DL_LABEL}…", self._last)
            return
        g = self._base.get(key, 0.0) + self._span.get(key, 0.0) * max(0.0, min(1.0, frac))
        self._last = max(self._last, g)  # never let the bar go backwards
        self._on(f"{_DL_LABEL}… {int(self._last * 100)}%", self._last)


def download_models(cfg, on_progress, on_done, engine_missing=False):
    """Fetch whatever is missing — the bundled runtime (frozen Linux/Windows),
    then the speech + cleanup models — as ONE smooth 0→100 bar. Reports
    (label, fraction) to on_progress from a worker thread; UI marshals to main.

    engine_missing may be a bool OR a 0-arg callable — a callable is resolved
    ON THE WORKER THREAD, because the backend's check probes the network
    (requests.get with a timeout) and must never run on the UI thread."""
    def work():
        need_engine = engine_missing() if callable(engine_missing) else engine_missing
        keys = (["engine"] if need_engine else [])
        need_asr = not asr_model_ok(cfg.asr_model)
        need_llm = not llm_model_ok(cfg.ollama_model)
        if need_asr:
            keys.append("asr")
        if need_llm:
            keys.append("llm")
        bar = _DownloadBar(on_progress, keys)
        try:
            if need_engine:
                from . import ollama_runtime
                bar.report("engine", None)
                ollama_runtime.download(lambda f: bar.report("engine", f))
                bar.report("engine", 1.0)  # fill the segment even if the
                #                            server omitted content-length
            if need_asr:
                _download_asr(cfg, bar)
            if need_llm:
                _download_llm(cfg, bar)
        except Exception as e:
            log.warning("model download failed: %s", e)
            on_progress(f"download failed: {e}", None)  # raw label, shows Retry
        on_done()
    threading.Thread(target=work, daemon=True).start()


def _download_asr(cfg, bar):
    bar.report("asr", None)
    from huggingface_hub import snapshot_download
    from tqdm.auto import tqdm

    class _Progress(tqdm):
        def update(self, n=1):
            super().update(n)
            # only the weights file is big enough to be worth a bar
            if self.total and self.total > 50 * 1024 * 1024:
                bar.report("asr", self.n / self.total)

    snapshot_download(cfg.asr_model, tqdm_class=_Progress)
    bar.report("asr", 1.0)


def _download_llm(cfg, bar):
    from . import llm_server
    bar.report("llm", None)
    llm_server.ensure(cfg, on_pull_progress=lambda pct: bar.report("llm", pct / 100))
    # ensure() swallows pull failures BY DESIGN (at normal startup a missing
    # model just means the regex fallback until it arrives) — but here
    # "ready" must mean ready: a dropped connection otherwise ends the run
    # as "ready" sitting above a Retry button (seen live, 2026-07-21 Windows
    # friend round). Verify on disk and fail loudly.
    if not llm_model_ok(cfg.ollama_model):
        raise RuntimeError("the cleanup model didn't finish downloading — "
                           "check your connection and press Retry")
    bar.report("llm", 1.0)


# -------------------------------------------------------------------- UI --

def _app_name() -> str:
    """The bundle's real name — 'Sotto Dev' in dev builds, so the welcome
    window stops claiming to be the release app."""
    try:
        from Foundation import NSBundle
        name = NSBundle.mainBundle().objectForInfoDictionaryKey_("CFBundleName")
        if name and str(name).startswith("Sotto"):
            return str(name)
    except Exception:
        pass
    return "Sotto"


ROWS = [
    ("mic", "Microphone",
     "Sotto records only while you hold the hotkey.", "Allow", request_mic),
    ("accessibility", "Accessibility",
     "Lets Sotto type the cleaned text at your cursor.", "Open Settings",
     request_accessibility),
    ("input_monitoring", "Input Monitoring",
     "Sees the hotkey. If Sotto isn't listed in the pane, add it with the "
     "＋ button. macOS may ask to quit & reopen Sotto — allow it.",
     "Open Settings", request_input_monitoring),
    ("globe_key", "Globe key",
     "Set “Press 🌐 key to” → “Do Nothing”, else fn opens emoji.",
     "Open Settings", fix_globe_key),
    ("notifications", "Notifications (optional)",
     "A quiet note when a Sotto update is ready.", "Allow",
     request_notifications),
    ("models", "Models (~3 GB, one time)",
     "Speech recognition + cleanup. Downloads by itself after setup.",
     None, None),
]


def launch(cfg):
    """Show the welcome window and own the process. Never returns: either the
    user quits, or setup completes and the app relaunches itself."""
    import signal

    from AppKit import (
        NSApplication, NSBackingStoreBuffered, NSButton, NSColor, NSFont,
        NSMakeRect, NSObject, NSScreen, NSTextField,
        NSTimer, NSWindow, NSWindowStyleMaskClosable, NSWindowStyleMaskTitled)
    from PyObjCTools import MachSignals

    from . import menubar

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(0)  # regular: Dock icon, same as the main app
    menubar.install()

    from . import update

    W, H, PAD, ROW_H = 560, 470, 24, 58
    # the optional notifications row only exists where updates do
    show_notifications = update.enabled()
    if show_notifications:
        H += ROW_H

    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, W, H),
        NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
        NSBackingStoreBuffered, False)
    app_name = _app_name()
    win.setTitle_(f"Welcome to {app_name}")
    win.setReleasedWhenClosed_(False)
    content = win.contentView()

    def label(text, size, bold, x, y, w, h, dim=False):
        t = NSTextField.labelWithString_(text)
        t.setFont_(NSFont.boldSystemFontOfSize_(size) if bold
                   else NSFont.systemFontOfSize_(size))
        if dim:
            t.setTextColor_(NSColor.secondaryLabelColor())
        t.setFrame_(NSMakeRect(x, y, w, h))
        content.addSubview_(t)
        return t

    label(f"Welcome to {app_name}", 22, True, PAD, H - 56, W - 2 * PAD, 30)
    label("Private, on-device dictation. A few things need setting up first.",
          12, False, PAD, H - 78, W - 2 * PAD, 16, dim=True)

    rows = {}       # key -> (dot, button)
    y = H - 110
    for key, title, detail, btn_title, _action in ROWS:
        if key == "globe_key" and cfg.hotkey != "fn":
            continue
        if key == "notifications" and not show_notifications:
            continue
        dot = label(_GRAY, 16, True, PAD, y - 34, 22, 22)
        label(title, 13, True, PAD + 30, y - 22, 330, 18)
        # details may run to two lines (the Input Monitoring "+" hint does)
        d = label(detail, 11, False, PAD + 30, y - 54, 330, 30, dim=True)
        d.setUsesSingleLineMode_(False)
        d.setLineBreakMode_(0)  # NSLineBreakByWordWrapping
        d.setMaximumNumberOfLines_(2)
        btn = None
        if btn_title:   # informational rows (models) have no action button
            btn = NSButton.buttonWithTitle_target_action_(btn_title, None, None)
            btn.setFrame_(NSMakeRect(W - PAD - 130, y - 34, 130, 28))
            content.addSubview_(btn)
        rows[key] = (dot, btn)
        y -= ROW_H

    start = NSButton.buttonWithTitle_target_action_("Start Sotto", None, None)
    start.setFrame_(NSMakeRect(W - PAD - 150, 14, 150, 34))
    start.setKeyEquivalent_("\r")
    start.setEnabled_(False)
    content.addSubview_(start)
    quit_btn = NSButton.buttonWithTitle_target_action_("Quit", None, None)
    quit_btn.setFrame_(NSMakeRect(W - PAD - 240, 14, 84, 34))
    content.addSubview_(quit_btn)
    label("Sotto downloads its models and finishes up by itself.", 10, False,
          PAD, 24, 280, 14, dim=True)

    actions = {r[0]: r[4] for r in ROWS}

    class Controller(NSObject):
        def act_(self, sender):
            for key, (_dot, btn) in rows.items():
                if btn is sender:
                    actions[key]()
                    return

        def tick_(self, _timer):
            st = statuses(cfg)
            st["models"] = st.pop("asr_model") and st.pop("llm_model")
            # Start gates on the user's clicks only: models download on
            # their own screen after the relaunch, and the notifications
            # row is optional.
            ready = all(v for k, v in st.items() if k != "models")
            if "notifications" in rows:
                st["notifications"] = notifications_ok()
                poll_notifications()   # refresh the cache for the next tick
            for key, (dot, btn) in rows.items():
                ok = st.get(key, True)
                dot.setStringValue_(_GREEN if ok else _GRAY)
                dot.setTextColor_(NSColor.systemGreenColor() if ok
                                  else NSColor.secondaryLabelColor())
                if btn is not None:
                    btn.setHidden_(ok)
            start.setEnabled_(ready)

        def start_(self, sender):
            # The click is the moment of truth: re-run every check NOW
            # rather than trust the last tick — the user may have just
            # revoked something in the Settings window sitting next to us.
            st = statuses(cfg)
            st.pop("asr_model")
            st.pop("llm_model")
            if not all(st.values()):
                self.tick_(None)   # repaint: the regressed row un-greens
                return
            relaunch()

        def quit_(self, sender):
            from AppKit import NSApp
            NSApp.terminate_(None)

        def windowWillClose_(self, _note):
            # the red close button means "not now" — quit instead of leaving
            # a windowless menu-bar process the user never asked to keep
            from AppKit import NSApp
            NSApp.terminate_(None)

    def _on_main(fn):
        from Foundation import NSOperationQueue
        NSOperationQueue.mainQueue().addOperationWithBlock_(fn)

    ctl = Controller.alloc().init()
    for key, (_dot, btn) in rows.items():
        if btn is None:
            continue
        btn.setTarget_(ctl)
        btn.setAction_("act:")
    start.setTarget_(ctl)
    start.setAction_("start:")
    quit_btn.setTarget_(ctl)
    quit_btn.setAction_("quit:")
    win.setDelegate_(ctl)

    ctl.tick_(None)  # paint real states before the first timer fire
    timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        1.0, ctl, "tick:", None, True)

    try:  # marker: the next normal startup announces that setup finished
        os.makedirs(os.path.dirname(PENDING_MARKER), exist_ok=True)
        open(PENDING_MARKER, "w").close()
    except OSError:
        pass

    frame = NSScreen.mainScreen().visibleFrame()
    win.setFrameOrigin_(((frame.size.width - W) / 2 + frame.origin.x,
                         (frame.size.height - H) / 2 + frame.origin.y))
    win.makeKeyAndOrderFront_(None)
    app.activateIgnoringOtherApps_(True)

    MachSignals.signal(signal.SIGINT, lambda *_: app.terminate_(None))
    # keep strong refs for the run loop's lifetime
    _refs.extend([win, ctl, timer])
    app.run()


def download_screen(cfg):
    """Owns the process while the models download. The walkthrough no longer
    makes the user wait out the ~3 GB fetch — their clicks finish, the Input
    Monitoring quit-&-reopen happens, and Sotto's own work lands here, with
    a progress bar and nothing to do. Relaunches into a normal start when
    the models are in place; the walkthrough's PENDING_MARKER then triggers
    the one-time "Sotto is ready" note."""
    import signal

    import objc
    from AppKit import (
        NSApplication, NSBackingStoreBuffered, NSButton, NSColor, NSFont,
        NSMakeRect, NSObject, NSProgressIndicator, NSScreen, NSTextField,
        NSWindow, NSWindowStyleMaskClosable, NSWindowStyleMaskTitled)
    from Foundation import NSOperationQueue
    from PyObjCTools import MachSignals

    from . import menubar

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(0)
    menubar.install()

    W, H, PAD = 520, 168, 24
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, W, H),
        NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
        NSBackingStoreBuffered, False)
    app_name = _app_name()
    win.setTitle_(f"Setting up {app_name}")
    win.setReleasedWhenClosed_(False)
    content = win.contentView()

    def label(text, size, bold, x, y, w, h, dim=False):
        t = NSTextField.labelWithString_(text)
        t.setFont_(NSFont.boldSystemFontOfSize_(size) if bold
                   else NSFont.systemFontOfSize_(size))
        if dim:
            t.setTextColor_(NSColor.secondaryLabelColor())
        t.setFrame_(NSMakeRect(x, y, w, h))
        content.addSubview_(t)
        return t

    label(f"Downloading {app_name}'s models", 16, True, PAD, H - 46,
          W - 2 * PAD, 22)
    label(f"Speech recognition + cleanup (~3 GB, one time). {app_name} "
          "starts by itself when this is done.", 11, False, PAD, H - 66,
          W - 2 * PAD, 16, dim=True)
    bar = NSProgressIndicator.alloc().initWithFrame_(
        NSMakeRect(PAD, 56, W - 2 * PAD, 8))
    bar.setStyle_(0)  # bar
    bar.setIndeterminate_(False)
    bar.setMinValue_(0.0)
    bar.setMaxValue_(1.0)
    content.addSubview_(bar)
    status = label("starting download…", 11, False, PAD, 32,
                   W - 2 * PAD, 16, dim=True)
    retry = NSButton.buttonWithTitle_target_action_("Retry", None, None)
    retry.setFrame_(NSMakeRect(W - PAD - 100, 12, 100, 30))
    retry.setHidden_(True)
    content.addSubview_(retry)

    def _on_main(fn):
        NSOperationQueue.mainQueue().addOperationWithBlock_(fn)

    class Controller(NSObject):
        @objc.python_method
        def begin(self):
            _on_main(lambda: retry.setHidden_(True))
            download_models(cfg, self.progress, self.done)

        @objc.python_method
        def progress(self, text, fraction):
            def ui():
                status.setStringValue_(text)
                if fraction is not None:
                    bar.setDoubleValue_(fraction)
            _on_main(ui)

        @objc.python_method
        def done(self):
            # download_models reports done even on failure — only relaunch
            # when the models are actually in place, else offer a retry
            # (relaunching would just loop straight back to this screen)
            if models_missing(cfg):
                _on_main(lambda: retry.setHidden_(False))
            else:
                _on_main(relaunch)

        def retry_(self, _sender):
            self.begin()

        def windowWillClose_(self, _note):
            from AppKit import NSApp
            NSApp.terminate_(None)

    ctl = Controller.alloc().init()
    retry.setTarget_(ctl)
    retry.setAction_("retry:")
    win.setDelegate_(ctl)

    frame = NSScreen.mainScreen().visibleFrame()
    win.setFrameOrigin_(((frame.size.width - W) / 2 + frame.origin.x,
                         (frame.size.height - H) / 2 + frame.origin.y))
    win.makeKeyAndOrderFront_(None)
    app.activateIgnoringOtherApps_(True)

    MachSignals.signal(signal.SIGINT, lambda *_: app.terminate_(None))
    _refs.extend([win, ctl])
    ctl.begin()
    app.run()


_refs: list = []
