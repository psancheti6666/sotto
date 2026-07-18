# Created by Pratik Sancheti / https://github.com/psancheti6666
"""ASR: the same Parakeet-TDT-0.6B-v3 model exported to ONNX, for Intel Macs
and Linux (onnx-asr / onnxruntime, CPU by default).

Short utterances go straight through recognize(); recordings longer than the
model's comfortable window are split at silences by onnx-asr's bundled
silero VAD and the segment texts are joined — nothing is dropped.
"""

import logging
import os

import numpy as np

log = logging.getLogger("sotto")

# recognize() handles a couple of minutes fine; beyond that, switch to VAD
# segmentation so multi-minute dictations are transcribed in full.
_VAD_THRESHOLD_S = 120.0


def _set_hub_offline(value: bool):
    """Flip huggingface_hub's offline switch AFTER import: the library bakes
    HF_HUB_OFFLINE into a module constant the moment it is first imported
    (constants.py), and that first import happens inside the load call — so
    toggling only the env var after a failed offline attempt changes nothing
    and the online retry would fail identically (#64 review). No-op when the
    hub was never imported."""
    try:
        import sys
        mod = sys.modules.get("huggingface_hub.constants")
        if mod is not None:
            mod.HF_HUB_OFFLINE = value
    except Exception:
        pass


def _load_offline_first(onnx_asr, model_id, quantization):
    """Offline-first model load. After first-run the model is fully cached,
    but a plain load still asks the HF Hub for the current revision on EVERY
    launch — extra seconds, HF_TOKEN warning noise, a broken 100%-local
    promise, and on flaky networks a HANG before the app even starts (all
    observed in the VM round). So: try with HF_HUB_OFFLINE=1 (cache only),
    then fall back online when the cache can't satisfy it (true first run /
    cleared or corrupt cache). A user-set HF_HUB_OFFLINE is respected — the
    default is injected only when the variable is absent — and the injected
    value never outlives the load: children (the updater's relaunch of the
    NEXT Sotto) must not inherit it."""
    kw = dict(quantization=quantization or None,
              providers=["CPUExecutionProvider"])
    if os.environ.get("HF_HUB_OFFLINE") is not None:
        return onnx_asr.load_model(model_id, **kw)
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        try:
            return onnx_asr.load_model(model_id, **kw)
        except Exception as e:
            log.info("offline ASR load failed (%s) — fetching online", e)
            _set_hub_offline(False)  # the env flip below is baked in by now
            os.environ.pop("HF_HUB_OFFLINE", None)
            return onnx_asr.load_model(model_id, **kw)
    finally:
        os.environ.pop("HF_HUB_OFFLINE", None)


class OnnxParakeetASR:
    def __init__(self, model_id: str = "nemo-parakeet-tdt-0.6b-v3",
                 sample_rate: int = 16000, quantization: str = ""):
        import onnx_asr
        self._onnx_asr = onnx_asr
        self.sample_rate = sample_rate
        # CPU provider explicitly: it's the target hardware (Intel Mac / Linux),
        # and onnxruntime's auto-picked CoreML provider fails to load this model.
        self.model = _load_offline_first(onnx_asr, model_id, quantization)
        self._vad_model = None  # built lazily on the first long recording

    def transcribe(self, audio: np.ndarray) -> str:
        sr = self.sample_rate
        if audio.size < sr * 0.2:  # <200 ms: nothing useful
            return ""
        audio = np.ascontiguousarray(audio, dtype=np.float32)
        if audio.size <= int(_VAD_THRESHOLD_S * sr):
            return self.model.recognize(audio, sample_rate=sr).strip()
        if self._vad_model is None:
            vad = self._onnx_asr.load_vad("silero")
            self._vad_model = self.model.with_vad(vad)
        segments = self._vad_model.recognize(audio, sample_rate=sr)
        return " ".join(s.text.strip() for s in segments if s.text.strip())
