"""Microphone capture: always-open 16 kHz mono stream, frames kept only while recording."""

import threading

import numpy as np
import sounddevice as sd


class Recorder:
    def __init__(self, sample_rate: int = 16000, max_utterance_s: float = 930.0):
        self.sample_rate = sample_rate
        self._max_frames = int(sample_rate * max_utterance_s)
        self._frames: list[np.ndarray] = []
        self._n_frames = 0
        self._recording = False
        self._level = 0.0  # live RMS of the last block, drives the waveform UI
        self._lock = threading.Lock()
        self._stream = sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            blocksize=0,
            callback=self._callback,
        )

    def _callback(self, indata, frames, time_info, status):
        if self._recording:
            self._level = float(np.sqrt(np.mean(indata ** 2)))
            with self._lock:
                # Cap by dropping the TAIL, never the beginning — dictation must
                # be captured from the start (the app's watchdog stops recording
                # before this limit is ever reached).
                if self._n_frames < self._max_frames:
                    self._frames.append(indata.copy())
                    self._n_frames += len(indata)

    def open(self):
        self._stream.start()

    def close(self):
        self._stream.stop()
        self._stream.close()

    def start(self):
        with self._lock:
            self._frames = []
            self._n_frames = 0
        self._recording = True

    @property
    def level(self) -> float:
        return self._level if self._recording else 0.0

    def stop(self) -> np.ndarray:
        """Stop recording and return the captured mono float32 audio."""
        self._recording = False
        self._level = 0.0
        with self._lock:
            if not self._frames:
                return np.zeros(0, dtype=np.float32)
            audio = np.concatenate(self._frames)[:, 0]
            self._frames = []
            self._n_frames = 0
        return audio[:self._max_frames]

    @property
    def is_recording(self) -> bool:
        return self._recording
