"""ASR backend selection.

Apple Silicon runs Parakeet-TDT-0.6B-v3 via MLX (asr_mlx); Intel Macs and
Linux run the SAME model exported to ONNX (asr_onnx). Both expose
transcribe(np.float32 mono 16 kHz) -> str and must be constructed and used on
one thread (MLX requires it; ONNX doesn't care, so the shared worker is fine).
"""

import logging

from .platform import IS_APPLE_SILICON

log = logging.getLogger("sotto")


def make_asr(cfg):
    backend = cfg.asr_backend
    if backend == "auto":
        backend = "mlx" if IS_APPLE_SILICON else "onnx"
    if backend == "mlx":
        log.info("loading ASR model %s (MLX)…", cfg.asr_model)
        from .asr_mlx import ParakeetASR
        return ParakeetASR(cfg.asr_model, cfg.sample_rate)
    log.info("loading ASR model %s (ONNX)…", cfg.onnx_model)
    from .asr_onnx import OnnxParakeetASR
    return OnnxParakeetASR(cfg.onnx_model, cfg.sample_rate, cfg.onnx_quantization)
