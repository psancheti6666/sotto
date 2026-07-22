# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Wires the pipeline: hotkey → record → ASR → dictionary → clean → inject."""

import logging
import os
import queue
import subprocess
import threading
import time
from datetime import datetime

from . import dashboard, history, llm_server
from .asr import make_asr
from .audio import Recorder
from .clean import Cleaner
from .config import CONFIG_DIR, DICTIONARY_PATH, Config, load_config
from .dictionary import Dictionary
from .inject import inject, prewarm as inject_prewarm
from .platform import (
    IS_LINUX, IS_MACOS, IS_WINDOWS, active_app_id, alert, end_app_nap, haptic,
    play_sound, prevent_app_nap)

log = logging.getLogger("sotto")


class Sotto:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        # +30 s headroom: the watchdog finishes recording at the limit, so the
        # recorder's own hard cap should never be the thing that truncates.
        self.recorder = Recorder(cfg.sample_rate, cfg.max_utterance_s + 30.0)
        self.listener = None
        self._rec_started = None
        self._cancelled = None  # (audio, bundle_id) held during the Undo window
        self._warned = False    # 1-minute-left sound played for this recording
        self.dictionary = Dictionary(DICTIONARY_PATH)
        self.cleaner = Cleaner(cfg.ollama_url, cfg.ollama_model,
                               cfg.llm_timeout_s, cfg.keep_alive)
        self.asr = None
        self.overlay = None
        # MLX requires model load + inference to happen on the same thread,
        # so a single persistent worker owns the ASR model and processes jobs.
        self._jobs: queue.Queue = queue.Queue()
        self._asr_ready = threading.Event()
        # macOS App Nap opt-out, held only while a dictation is in flight so an
        # idle Sotto stays low-power (None when idle or off-macOS).
        self._app_nap_token = None

    def tone_for(self, bundle_id: str) -> str:
        if bundle_id in self.cfg.tone_map:
            return self.cfg.tone_map[bundle_id]
        for prefix, tone in self.cfg.tone_map.items():
            if bundle_id.startswith(prefix):
                return tone
        return self.cfg.default_tone

    def process_text(self, raw: str, bundle_id: str = "") -> str:
        """Dictionary + mandatory cleaning stage. Testable without mic/hotkey."""
        fixed = self.dictionary.apply(raw)
        return self.cleaner.clean(fixed, self.tone_for(bundle_id), self.dictionary.terms)

    def _remaining(self):
        """Seconds left until the dictation limit, or None when not recording."""
        if not self.recorder.is_recording or self._rec_started is None:
            return None
        return self.cfg.max_utterance_s - (time.monotonic() - self._rec_started)

    def _begin_activity(self):
        """Hold full CPU/timer priority for the dictation now starting (macOS
        App Nap opt-out). Idempotent — safe to call when already held."""
        if self._app_nap_token is None:
            self._app_nap_token = prevent_app_nap()

    def _end_activity(self):
        """Re-allow App Nap, but only once fully idle: nothing is being recorded
        and no job is queued/processing. Called at every return-to-idle point."""
        if (self._app_nap_token is not None
                and not self.recorder.is_recording and self._jobs.empty()):
            end_app_nap(self._app_nap_token)
            self._app_nap_token = None

    def _watchdog(self):
        while True:
            time.sleep(1.0)
            remaining = self._remaining()
            if remaining is None:
                continue
            if remaining <= self.cfg.warn_remaining_s and not self._warned:
                self._warned = True
                if self.cfg.sounds:
                    play_sound(self.cfg.warn_sound)
            if remaining <= 0 and self.listener:
                log.info("dictation limit reached (%.0f min) — transcribing now",
                         self.cfg.max_utterance_s / 60)
                self.listener.force_stop()

    def _on_handsfree(self):
        log.info("hands-free mode — press %s (or click ✓) to finish", self.cfg.hotkey)
        if self.overlay:
            self.overlay.show_handsfree()
        if self.cfg.sounds:
            play_sound(self.cfg.handsfree_sound)

    def _on_start(self):
        self._begin_activity()  # full priority while we record + process
        self._cancelled = None  # a new dictation supersedes any pending undo
        self._warned = False
        self.recorder.start()
        self._rec_started = time.monotonic()
        # Warm the cleaning model while the user speaks, so a cold load (after
        # the keep_alive window expired) overlaps the recording instead of
        # delaying the clean stage. Near-instant no-op when already loaded.
        threading.Thread(target=self.cleaner.warm, daemon=True).start()
        if self.overlay:
            self.overlay.show_listening()
        if self.cfg.sounds:
            play_sound(self.cfg.start_sound)
        if self.cfg.haptics:
            haptic()
        log.info("recording…")

    def _on_stop(self, discard: bool = False):
        audio = self.recorder.stop()
        if discard or audio.size == 0:
            if self.overlay:
                self.overlay.hide()
            self._end_activity()  # nothing queued — back to idle
            return
        if self.overlay:
            self.overlay.show_processing()
        self._jobs.put((audio, active_app_id()))  # worker releases when done

    def _on_cancel(self):
        """Escape or ✕: stop recording but hold the audio for the Undo window."""
        audio = self.recorder.stop()
        if audio.size == 0:
            if self.overlay:
                self.overlay.hide()
            self._end_activity()
            return
        # Recording stopped; audio just waits for an Undo decision — go idle.
        self._end_activity()
        self._cancelled = (audio, active_app_id())
        log.info("dictation cancelled — Undo available for %.0fs", self.cfg.undo_window_s)
        if self.cfg.sounds:
            play_sound(self.cfg.cancel_sound)
        if self.overlay:
            self.overlay.show_cancelled(self.cfg.undo_window_s)
        else:
            self._cancelled = None  # no UI to undo from

    def _undo_cancel(self):
        """Undo clicked: transcribe the held audio after all."""
        pending, self._cancelled = self._cancelled, None
        if pending is None:
            return
        log.info("undo — transcribing the cancelled dictation")
        self._begin_activity()  # full priority for the (re)transcription
        if self.overlay:
            self.overlay.show_processing()
        self._jobs.put(pending)

    def _expire_cancel(self):
        self._cancelled = None

    def _worker(self):
        self.asr = make_asr(self.cfg)
        self._asr_ready.set()
        while True:
            audio, bundle_id = self._jobs.get()
            try:
                self._process_audio(audio, bundle_id)
            except Exception:
                log.exception("pipeline error")
            finally:
                if self.overlay:
                    self.overlay.hide()
                self._end_activity()  # dictation fully done — re-allow App Nap

    def _process_audio(self, audio, bundle_id):
        t0 = time.perf_counter()
        raw = self.asr.transcribe(audio)
        t1 = time.perf_counter()
        if not raw:
            log.info("(empty transcription)")
            return
        cleaned = self.process_text(raw, bundle_id)
        t2 = time.perf_counter()
        if not cleaned:
            return
        mode = "type" if bundle_id in self.cfg.keystroke_apps else self.cfg.inject_mode
        inject(cleaned, mode=mode,
               type_max_chars=self.cfg.type_max_chars,
               type_interval_s=self.cfg.type_interval_s,
               restore_delay_s=self.cfg.paste_restore_delay_s)
        if self.cfg.sounds:
            play_sound(self.cfg.done_sound)
        t3 = time.perf_counter()
        # lengths only — the log persists to ~/.sotto/sotto.log, and
        # transcripts belong in history.jsonl, not in a debug file
        log.info("asr=%.2fs clean=%.2fs inject=%.2fs total=%.2fs | "
                 "%d chars -> %d chars",
                 t1 - t0, t2 - t1, t3 - t2, t3 - t0, len(raw), len(cleaned))
        history.append_entry({
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "text": cleaned,
            "raw": raw,
            "words": len(cleaned.split()),
            "duration_s": round(len(audio) / self.cfg.sample_rate, 2),
            "app": bundle_id,
            "asr_s": round(t1 - t0, 2),
            "clean_s": round(t2 - t1, 2),
            "inject_s": round(t3 - t2, 2),
        })

    def _make_listener(self):
        """Pick the hotkey backend for this platform (imports are lazy — pynput
        exists only on macOS, evdev only on Linux)."""
        kwargs = dict(tap_max_s=self.cfg.tap_max_s,
                      double_tap_window_s=self.cfg.double_tap_window_s,
                      on_handsfree=self._on_handsfree, on_cancel=self._on_cancel)
        if IS_LINUX:
            from .hotkey_evdev import EvdevHotkeyListener
            return EvdevHotkeyListener(self.cfg.hotkey, self._on_start, self._on_stop, **kwargs)
        if IS_MACOS and self.cfg.hotkey == "fn":
            self._check_globe_key_setting()
            from .hotkey import FnHotkeyListener
            return FnHotkeyListener(self._on_start, self._on_stop, **kwargs)
        hotkey = self.cfg.hotkey
        if IS_WINDOWS and hotkey == "fn":
            # the macOS default leaks through until W5's IS_WINDOWS config
            # branch lands; fn does not exist as a pynput key on Windows and
            # the listener would be dead on arrival (docs/windows-app.md W2)
            log.warning("hotkey 'fn' does not exist on Windows — using "
                        "ctrl_r (set hotkey in %s to silence this)",
                        "~/.sotto/config.toml")
            hotkey = "ctrl_r"
        from .hotkey import HotkeyListener
        return HotkeyListener(hotkey, self._on_start, self._on_stop, **kwargs)

    @staticmethod
    def _check_globe_key_setting():
        """With the fn hotkey, macOS's own Globe-key action (emoji picker /
        input-source switcher) must be disabled or it fires on every press."""
        try:
            out = subprocess.run(
                ["defaults", "read", "com.apple.HIToolbox", "AppleFnUsageType"],
                capture_output=True, text=True, timeout=5)
            if out.stdout.strip() != "0":
                log.warning(
                    "macOS will open the emoji picker / switch input sources on fn "
                    "presses! Fix: System Settings → Keyboard → “Press 🌐 key to” → "
                    "Do Nothing (or run: defaults write com.apple.HIToolbox "
                    "AppleFnUsageType -int 0), then log out and back in if it persists.")
        except Exception:
            pass

    def run(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        if IS_MACOS:
            inject_prewarm()  # main thread — see prewarm()'s docstring
            from . import menubar
            if menubar.running_in_bundle():
                from . import firstrun
                # The user passed Gatekeeper to get here — drop the
                # quarantine flag now so our own quit-&-reopens never
                # re-trigger the "Open Anyway" dance (macOS 26 makes it
                # a two-round affair otherwise).
                firstrun.strip_quarantine()
                if firstrun.needed(self.cfg):
                    # Welcome window owns the process; when setup completes
                    # it relaunches the app (Input Monitoring grants only
                    # apply to a fresh process), so this never returns.
                    firstrun.launch(self.cfg)
                    return
                # Fresh machines download models into ~/.sotto; must run
                # before anything imports huggingface_hub.
                firstrun.consolidate_model_stores(self.cfg)
                if firstrun.models_missing(self.cfg):
                    # Permissions done, models not yet here: the download
                    # screen owns the process (progress bar, no user work)
                    # and relaunches into a normal start when finished.
                    firstrun.download_screen(self.cfg)
                    return
                # From here the app runs for real — watch for permissions
                # being revoked mid-session, which is otherwise silent.
                threading.Thread(target=self._permission_watchdog,
                                 daemon=True).start()
        elif IS_LINUX or IS_WINDOWS:
            if IS_LINUX:
                from . import firstrun_linux as fr_platform
            else:
                from . import firstrun_windows as fr_platform
            # packaged builds get the guided setup; checkouts keep today's
            # behavior unless SOTTO_FIRSTRUN=1 previews the windows
            if (fr_platform.bundle_type()
                    or os.environ.get("SOTTO_FIRSTRUN") == "1"):
                from . import firstrun, firstrun_tk
                if fr_platform.needed(self.cfg):
                    firstrun_tk.launch(self.cfg)  # owns the process;
                    return                        # completing it relaunches
                firstrun.consolidate_model_stores(self.cfg)
                if fr_platform.setup_missing(self.cfg):
                    firstrun_tk.download_screen(self.cfg)
                    return
        if IS_LINUX:
            # Frozen bundle: sanitize os.environ NOW, on the sole thread —
            # before ollama/ASR/tray threads spawn and read it. Children
            # (including WebKit's own helper processes, which spawn at times
            # we don't control) then inherit a clean env; the process's own
            # already-resolved libraries are unaffected. No-op in a checkout.
            from . import insights_linux
            insights_linux.sanitize_environ()
        # Bundled ollama (if any) spawns while the ASR model loads; from a
        # checkout this is one fast probe and a return.
        threading.Thread(target=llm_server.ensure, args=(self.cfg,),
                         daemon=True).start()
        if IS_MACOS:
            # scheduled update check — no-op outside the released bundle
            from . import update
            update.start_scheduled(self.cfg)
        threading.Thread(target=self._worker, daemon=True).start()
        self._asr_ready.wait()
        self.recorder.open()
        log.info("ready — hold %s to dictate (double-tap for hands-free)", self.cfg.hotkey)
        if IS_MACOS:
            from . import firstrun, menubar
            if menubar.running_in_bundle():
                # macOS may have quit-&-reopened us to apply Input Monitoring
                # — tell the user setup finished if the welcome window ran.
                firstrun.announce_if_setup_just_finished()
        elif IS_LINUX or IS_WINDOWS:
            from . import firstrun
            # the relaunch after the download screen lands here
            firstrun.announce_if_setup_just_finished(hotkey=self.cfg.hotkey)
        listener = self._make_listener()
        self.listener = listener
        threading.Thread(target=self._watchdog, daemon=True).start()
        server = None
        if self.cfg.dashboard:
            # Serves from its own daemon thread — the AppKit/tk run loop below
            # and the ASR worker are untouched.
            server = dashboard.start(self.cfg.dashboard_port,
                                     dictionary=self.dictionary)
            in_bundle = False
            if server and IS_MACOS:
                from . import insights, menubar
                in_bundle = menubar.running_in_bundle()
                if in_bundle:
                    # arms the menu-bar "Insights" item (menu built later, in
                    # overlay.run_forever)
                    insights.configure(self.cfg.dashboard_port)
            elif server and (IS_LINUX or IS_WINDOWS):
                # arms the tray's "Insights" item; show_soon() opens a native
                # window (WebKitGTK / WebView2) when the system can host
                # one, else the browser tab as before
                if IS_LINUX:
                    from . import insights_linux as insights_native
                else:
                    from . import insights_windows as insights_native
                insights_native.configure(self.cfg.dashboard_port)
        if IS_LINUX or IS_WINDOWS:
            # Best-effort tray (docs/linux-app.md L7; docs/windows-app.md
            # W6): daemon thread, any failure logs one line and the app
            # runs tray-less. Started BEFORE any insights show so on Linux
            # its pystray loop becomes the GLib dispatcher (insights_linux
            # gives it a head start — see TRAY_LOOP_GRACE_S there).
            from . import tray_linux
            tray_linux.start(self.cfg.dashboard_port if server else None)
        if server and self.cfg.open_dashboard_on_start:
            if in_bundle:
                insights.show_soon()  # native window, not a browser tab
            elif IS_LINUX or IS_WINDOWS:
                insights_native.show_soon()
            else:
                dashboard.open_in_browser(self.cfg.dashboard_port)
        overlay_mod = self._overlay_module() if self.cfg.indicator else None
        if overlay_mod:
            # The UI run loop (AppKit or tk) owns the main thread; the hotkey
            # listener runs alongside.
            try:
                self.overlay = overlay_mod.Overlay(
                    lambda: self.recorder.level,
                    self.cfg.indicator_offset_y,
                    remaining_supplier=self._remaining,
                    warn_remaining_s=self.cfg.warn_remaining_s,
                    on_cancel_click=listener.cancel,
                    on_done_click=listener.force_stop,
                    on_undo_click=self._undo_cancel,
                    on_cancel_expire=self._expire_cancel)
            except Exception as e:
                log.warning("indicator unavailable (%s) — running headless", e)
                self.overlay = None
        if self.overlay:
            threading.Thread(target=self._run_listener, args=(listener,),
                             daemon=True).start()
            overlay_mod.run_forever()
        else:
            try:
                listener.run()
            except KeyboardInterrupt:
                # Ctrl+C or the tray's Quit (SIGINT to self) — a designed
                # shutdown, not a crash; without this the excepthook would
                # log it as CRITICAL and log-based triage reads a quit as
                # a failure.
                log.info("interrupt — shutting down")
            finally:
                self.recorder.close()

    LISTENER_RETRY_S = 3.0
    PERMISSION_POLL_S = 15.0

    @staticmethod
    def _permission_poll_once(watched, good):
        """One watchdog pass: alert exactly once per granted→revoked flip.
        `good` carries state between calls; a re-grant re-arms the alert."""
        for name, check in watched.items():
            ok = bool(check())
            if good[name] and not ok:
                log.warning("%s permission was revoked", name)
                extra = (" Then quit and reopen Sotto."
                         if name == "Input Monitoring" else "")
                alert(f"Sotto lost the {name} permission",
                      f"It was just turned off in System Settings → Privacy "
                      f"& Security. Dictation can't work without it — please "
                      f"turn it back on.{extra}")
            good[name] = ok

    def _permission_watchdog(self):
        """Post-setup revocations are otherwise silent — typing just stops
        landing or the hotkey dies with no explanation. Poll the same real
        checks the walkthrough uses and say so the moment one flips."""
        from . import firstrun
        watched = {
            "Microphone": firstrun.mic_ok,
            "Accessibility": firstrun.accessibility_ok,
            "Input Monitoring": firstrun.input_monitoring_ok,
        }
        good = dict.fromkeys(watched, True)  # verified by the startup gate
        while True:
            time.sleep(self.PERMISSION_POLL_S)
            self._permission_poll_once(watched, good)

    def _run_listener(self, listener):
        """Daemon-thread wrapper around listener.run(). A missing keyboard
        permission must not die silently (a Finder-launched app has no visible
        stderr): tell the user once, then keep retrying — TCC is re-checked on
        every attempt, so dictation starts the moment the grant lands, no
        relaunch needed."""
        warned = False
        while True:
            try:
                listener.run()
                return
            except RuntimeError as e:
                if not warned:
                    warned = True
                    log.error("hotkey listener: %s", e)
                    alert("Sotto can't listen for the dictation hotkey",
                          f"{e}\n\nSotto keeps retrying in the background — "
                          "once both are enabled, dictation starts working "
                          "immediately, no relaunch needed.")
                time.sleep(self.LISTENER_RETRY_S)

    def _overlay_module(self):
        backend = self.cfg.indicator_backend
        if backend == "auto":
            backend = "appkit" if IS_MACOS else "tk"
        if backend == "appkit":
            from . import overlay
            return overlay
        from . import overlay_tk
        return overlay_tk


LOG_PATH = os.path.join(CONFIG_DIR, "sotto.log")


def setup_logging(path: str = None):
    """Console + rotating file at ~/.sotto/sotto.log, on every platform.
    The file is the only place a Finder-launched bundle's logs survive
    (no stderr — the 2026-07-17 banner hiccup was undiagnosable), and it
    captures unhandled exceptions from every thread (the pynput/TSM crash
    was only readable from a .ips crash report). SOTTO_DEBUG=1 → DEBUG."""
    import logging.handlers
    import sys

    root = logging.getLogger()
    if any(isinstance(h, logging.handlers.RotatingFileHandler)
           for h in root.handlers):
        return  # already configured (relaunch paths call main() once, but be safe)
    path = path or LOG_PATH
    level = logging.DEBUG if os.environ.get("SOTTO_DEBUG") else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] "
                            "%(module)s:%(lineno)d %(message)s")
    root.setLevel(level)
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as e:
        log.warning("cannot open log file %s (%s) — console only", path, e)
    logging.captureWarnings(True)

    def hook(exc_type, exc, tb):
        log.critical("unhandled exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = hook
    threading.excepthook = lambda a: log.critical(
        "unhandled exception in thread %r",
        a.thread.name if a.thread else "?",
        exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


_instance_lock = None  # held socket — must outlive main()


def _win_instance_lock(kernel32=None):
    """Windows: a per-session named mutex (docs/windows-app.md W5 — the #63
    double-instance lesson; no LaunchServices backstop exists here and MSIX
    apps are multi-instance). The handle lives for the process lifetime and
    the OS reclaims it on ANY exit — no stale state possible. Fails OPEN:
    a mutex API problem must not block startup."""
    try:
        get_err = None
        if kernel32 is None:
            import ctypes
            from ctypes import wintypes
            # use_last_error=True + ctypes.get_last_error(): the bare
            # GetLastError() read is NOT reliably attributable to our call
            # (interpreter internals / AV hooks can clobber it in between)
            # — and a clobbered read here silently defeats second-instance
            # detection, the exact scenario this guard exists for
            # (#78 review; the documented ctypes pattern).
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            get_err = ctypes.get_last_error
        handle = kernel32.CreateMutexW(None, False, "Local\\sotto-instance")
        if not handle:
            return True
        err = get_err() if get_err else kernel32.GetLastError()
        if err == 183:  # ERROR_ALREADY_EXISTS
            return None
        return handle
    except Exception:
        return True


def _acquire_instance_lock(socket_mod=None, win_kernel32=None):
    """Single-instance guard (#63: two instances = double-typed text).
    Linux: hold a lock socket in the user's own 0700 XDG_RUNTIME_DIR —
    dies with the process (no stale files), and being inside the per-user
    runtime dir it can't be squatted by another user the way an
    abstract-namespace name could (#64 review). Windows: named mutex (see
    _win_instance_lock). Returns a truthy token elsewhere (macOS bundles
    are single-instance via LaunchServices) and None when another live
    Sotto already holds it."""
    if (IS_WINDOWS and socket_mod is None) or win_kernel32 is not None:
        return _win_instance_lock(win_kernel32)
    if not IS_LINUX and socket_mod is None:
        return True
    import socket as _socket
    socket_mod = socket_mod or _socket
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    # literal "/" — a Linux path; the W1 unit tier runs this with a fake
    # socket module on Windows too, where os.path.join would backslash it
    path = runtime + "/sotto.lock"
    s = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
    try:
        try:
            s.bind(path)
        except OSError:
            # a bound path means a live holder (bind fails on an in-use
            # socket); a leftover file from a crash won't be bound, so
            # connect to tell the two apart
            try:
                probe = socket_mod.socket(socket_mod.AF_UNIX,
                                          socket_mod.SOCK_STREAM)
                probe.connect(path)  # someone is listening → really running
                probe.close()
                s.close()
                return None
            except OSError:
                os.unlink(path)      # stale — reclaim it
                s.bind(path)
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def main():
    import platform as plat
    import sys

    setup_logging()
    # Boot smoke (macOS analogue of the Linux/Windows --smoke flag): a
    # frozen bundle that's missing a boot module crashes here with
    # ModuleNotFoundError before any UI, and CI can't launch the GUI to
    # notice (#107 — logging.handlers wasn't bundled). SOTTO_SMOKE=1 runs
    # every boot import (setup_logging above already exercised
    # logging.handlers) plus the config load, then exits 0 — so the built
    # bundle can prove it boots without a display.
    if os.environ.get("SOTTO_SMOKE") == "1":
        load_config()
        # Import the macOS runtime surface too (the sibling of winapp's
        # SMOKE_IMPORTS): a future function-level stdlib-submodule miss in
        # any of these would otherwise only surface on a user's launch.
        from . import (asr, clean, dashboard, dictionary, inject,  # noqa: F401
                       insights, menubar, overlay, update)
        print("SOTTO_SMOKE ok: boot imports resolved", flush=True)
        # Hard exit, NOT return: importing the ASR stack pulls onnxruntime on
        # Intel, which leaves a non-daemon thread that blocks a normal
        # interpreter shutdown — a plain return hung the Intel build for an
        # hour (MLX on Apple Silicon has no such thread, so it exited and hid
        # this). The imports are what we came to prove; terminate now.
        os._exit(0)
    from . import __version__
    if IS_LINUX:
        from .firstrun_linux import bundle_type
        kind = {"deb": ".deb install", "appimage": "AppImage"}.get(bundle_type())
    else:
        kind = ("app bundle"
                if getattr(sys, "frozen", None) == "macosx_app" else None)
    log.info("sotto %s starting — %s %s, python %s, %s",
             __version__, plat.platform(), plat.machine(),
             plat.python_version(), kind or "source checkout")
    # One Sotto per user: a second instance would double-type every
    # dictation and race for the dashboard port (both observed in the VM
    # round). The lock must be held for the process lifetime.
    global _instance_lock
    _instance_lock = _acquire_instance_lock()
    if _instance_lock is None:
        log.error("another Sotto instance is already running — exiting")
        alert("Sotto is already running",
              "Another Sotto is active (check the tray/menu bar). "
              "Running two would type every dictation twice.")
        return
    cfg = load_config()
    log.info("config: hotkey=%s asr=%s llm=%s dashboard=%s indicator=%s "
             "inject=%s", cfg.hotkey, cfg.asr_backend, cfg.ollama_model,
             cfg.dashboard_port if cfg.dashboard else "off",
             cfg.indicator_backend if cfg.indicator else "off",
             cfg.inject_mode)
    Sotto(cfg).run()
