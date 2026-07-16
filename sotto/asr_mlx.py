# Created by Pratik Sancheti / https://github.com/psancheti6666
"""ASR: NVIDIA Parakeet-TDT-0.6B-v3 via parakeet-mlx (Apple Silicon, ~0.5 s/utterance).

Feeds the in-memory capture buffer straight into the model's log-mel frontend
(get_logmel → generate), skipping parakeet-mlx's file loader and its ffmpeg
dependency. Long recordings are transcribed in overlapping chunks and merged
with the library's own alignment helpers — the same algorithm its file-based
transcribe() uses — so multi-minute dictations are captured in full.
"""

import mlx.core as mx
import numpy as np
from parakeet_mlx.alignment import (
    merge_longest_common_subsequence,
    merge_longest_contiguous,
    sentences_to_result,
    tokens_to_sentences,
)
from parakeet_mlx.audio import get_logmel
from parakeet_mlx.parakeet import DecodingConfig


class ParakeetASR:
    def __init__(self, model_id: str = "mlx-community/parakeet-tdt-0.6b-v3",
                 sample_rate: int = 16000):
        from parakeet_mlx import from_pretrained
        self.model = from_pretrained(model_id)
        assert self.model.preprocessor_config.sample_rate == sample_rate, (
            f"model expects {self.model.preprocessor_config.sample_rate} Hz, got {sample_rate}"
        )

    def transcribe(self, audio: np.ndarray,
                   chunk_duration: float = 120.0,
                   overlap_duration: float = 15.0) -> str:
        cfg = self.model.preprocessor_config
        sr = cfg.sample_rate
        if audio.size < sr * 0.2:  # <200 ms: nothing useful
            return ""
        try:
            return self._transcribe(audio, chunk_duration, overlap_duration)
        finally:
            # MLX keeps freed inference buffers pooled at the high-water mark
            # (~2 GB for a minute of audio) — return them to the OS; only the
            # ~1.2 GB of weights stay resident. Re-allocating on the next
            # dictation costs milliseconds.
            mx.clear_cache()

    def _transcribe(self, audio: np.ndarray,
                    chunk_duration: float, overlap_duration: float) -> str:
        cfg = self.model.preprocessor_config
        sr = cfg.sample_rate
        data = mx.array(audio.astype(np.float32))

        if audio.size <= int(chunk_duration * sr):
            mel = get_logmel(data, cfg)
            return self.model.generate(mel)[0].text.strip()

        decoding = DecodingConfig()
        chunk_samples = int(chunk_duration * sr)
        overlap_samples = int(overlap_duration * sr)
        all_tokens = []
        for start in range(0, len(data), chunk_samples - overlap_samples):
            end = min(start + chunk_samples, len(data))
            if end - start < cfg.hop_length:
                break
            chunk_mel = get_logmel(data[start:end], cfg)
            chunk_result = self.model.generate(chunk_mel, decoding_config=decoding)[0]
            chunk_offset = start / sr
            for sentence in chunk_result.sentences:
                for token in sentence.tokens:
                    token.start += chunk_offset
                    token.end = token.start + token.duration
            if all_tokens:
                try:
                    all_tokens = merge_longest_contiguous(
                        all_tokens, chunk_result.tokens,
                        overlap_duration=overlap_duration)
                except RuntimeError:
                    all_tokens = merge_longest_common_subsequence(
                        all_tokens, chunk_result.tokens,
                        overlap_duration=overlap_duration)
            else:
                all_tokens = chunk_result.tokens

        result = sentences_to_result(tokens_to_sentences(all_tokens, decoding.sentence))
        return result.text.strip()
