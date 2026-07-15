"""ASR: the same Parakeet-TDT-0.6B-v3 model exported to ONNX, for Intel Macs
and Linux (onnx-asr / onnxruntime, CPU by default).

Short utterances go straight through recognize(); recordings longer than the
model's comfortable window are split at silences by onnx-asr's bundled
silero VAD and the segment texts are joined — nothing is dropped.
"""

import numpy as np

# recognize() handles a couple of minutes fine; beyond that, switch to VAD
# segmentation so multi-minute dictations are transcribed in full.
_VAD_THRESHOLD_S = 120.0


class OnnxParakeetASR:
    def __init__(self, model_id: str = "nemo-parakeet-tdt-0.6b-v3",
                 sample_rate: int = 16000, quantization: str = ""):
        import onnx_asr
        self._onnx_asr = onnx_asr
        self.sample_rate = sample_rate
        # CPU provider explicitly: it's the target hardware (Intel Mac / Linux),
        # and onnxruntime's auto-picked CoreML provider fails to load this model.
        self.model = onnx_asr.load_model(model_id, quantization=quantization or None,
                                         providers=["CPUExecutionProvider"])
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
