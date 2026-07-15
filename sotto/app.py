"""Wires the pipeline: hotkey → record → ASR → dictionary → clean → inject."""

import logging
import os
import queue
import subprocess
import threading
import time

from .asr import ParakeetASR
from .audio import Recorder
from .clean import Cleaner
from .config import CONFIG_DIR, DICTIONARY_PATH, Config, load_config
from .dictionary import Dictionary
from .hotkey import FnHotkeyListener, HotkeyListener
from .inject import frontmost_bundle_id, inject

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

    def _watchdog(self):
        while True:
            time.sleep(1.0)
            remaining = self._remaining()
            if remaining is None:
                continue
            if remaining <= self.cfg.warn_remaining_s and not self._warned:
                self._warned = True
                if self.cfg.sounds:
                    from .overlay import play_sound
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
            from .overlay import play_sound
            play_sound(self.cfg.handsfree_sound)

    def _on_start(self):
        self._cancelled = None  # a new dictation supersedes any pending undo
        self._warned = False
        self.recorder.start()
        self._rec_started = time.monotonic()
        if self.overlay:
            self.overlay.show_listening()
        if self.cfg.sounds:
            from .overlay import play_sound
            play_sound(self.cfg.start_sound)
        if self.cfg.haptics:
            from .overlay import haptic
            haptic()
        log.info("recording…")

    def _on_stop(self, discard: bool = False):
        audio = self.recorder.stop()
        if discard or audio.size == 0:
            if self.overlay:
                self.overlay.hide()
            return
        if self.overlay:
            self.overlay.show_processing()
        self._jobs.put((audio, frontmost_bundle_id()))

    def _on_cancel(self):
        """Escape or ✕: stop recording but hold the audio for the Undo window."""
        audio = self.recorder.stop()
        if audio.size == 0:
            if self.overlay:
                self.overlay.hide()
            return
        self._cancelled = (audio, frontmost_bundle_id())
        log.info("dictation cancelled — Undo available for %.0fs", self.cfg.undo_window_s)
        if self.cfg.sounds:
            from .overlay import play_sound
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
        if self.overlay:
            self.overlay.show_processing()
        self._jobs.put(pending)

    def _expire_cancel(self):
        self._cancelled = None

    def _worker(self):
        log.info("loading ASR model %s…", self.cfg.asr_model)
        self.asr = ParakeetASR(self.cfg.asr_model, self.cfg.sample_rate)
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
            from .overlay import play_sound
            play_sound(self.cfg.done_sound)
        t3 = time.perf_counter()
        log.info("asr=%.2fs clean=%.2fs inject=%.2fs total=%.2fs | %r -> %r",
                 t1 - t0, t2 - t1, t3 - t2, t3 - t0, raw, cleaned)

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
        threading.Thread(target=self._worker, daemon=True).start()
        log.info("warming Ollama model %s…", self.cfg.ollama_model)
        self.cleaner.warm()
        self._asr_ready.wait()
        self.recorder.open()
        log.info("ready — hold %s to dictate (double-tap for hands-free)", self.cfg.hotkey)
        if self.cfg.hotkey == "fn":
            self._check_globe_key_setting()
            listener = FnHotkeyListener(self._on_start, self._on_stop,
                                        self.cfg.tap_max_s, self.cfg.double_tap_window_s,
                                        on_handsfree=self._on_handsfree, on_cancel=self._on_cancel)
        else:
            listener = HotkeyListener(self.cfg.hotkey, self._on_start, self._on_stop,
                                      self.cfg.tap_max_s, self.cfg.double_tap_window_s,
                                      on_handsfree=self._on_handsfree, on_cancel=self._on_cancel)
        self.listener = listener
        threading.Thread(target=self._watchdog, daemon=True).start()
        if self.cfg.indicator:
            # AppKit UI owns the main thread; the hotkey listener runs alongside.
            from . import overlay
            self.overlay = overlay.Overlay(
                lambda: self.recorder.level,
                self.cfg.indicator_offset_y,
                remaining_supplier=self._remaining,
                warn_remaining_s=self.cfg.warn_remaining_s,
                on_cancel_click=listener.cancel,
                on_done_click=listener.force_stop,
                on_undo_click=self._undo_cancel,
                on_cancel_expire=self._expire_cancel)
            threading.Thread(target=listener.run, daemon=True).start()
            overlay.run_forever()
        else:
            try:
                listener.run()
            finally:
                self.recorder.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    Sotto(load_config()).run()
