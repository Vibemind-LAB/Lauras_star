"""Forced word alignment via WhisperX (optional extra: ``[align]``).

Refines the word-level timestamps produced by ASR using a wav2vec2 forced-alignment
model — tighter boundaries than Whisper's own. Optional and CPU-capable; degrades
gracefully when the extra is absent (callers check :func:`whisperx_available`).
"""

from __future__ import annotations

import wave
from pathlib import Path

from .device import torch_device
from .types import SegmentResult, WordResult


def whisperx_available() -> bool:
    try:
        import whisperx  # noqa: F401
    except Exception:
        return False
    return True


def _load_audio(audio_path: Path | str) -> object:
    """Mono float32 numpy at the file's rate, via stdlib ``wave`` (no torchcodec/FFmpeg)."""
    import numpy as np

    with wave.open(str(audio_path), "rb") as wav:
        channels = wav.getnchannels()
        raw = wav.readframes(wav.getnframes())
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data


def align_words(
    audio_path: Path | str,
    segments: list[SegmentResult],
    *,
    language: str = "en",
    device: str | None = None,
) -> list[SegmentResult]:
    """Return ``segments`` with word timestamps refined by forced alignment.

    Segment text/timing is preserved; only the per-word boundaries are replaced. A
    segment that fails to align keeps its original words. Raises (via lazy import) if
    ``[align]`` is absent."""
    import whisperx

    if not segments:
        return segments
    dev = torch_device(device)
    audio = _load_audio(audio_path)
    model_a, metadata = whisperx.load_align_model(language_code=language, device=dev)
    asr_input = [{"text": s.text, "start": s.start_sec, "end": s.end_sec} for s in segments]
    result = whisperx.align(
        asr_input, model_a, metadata, audio, device=dev, return_char_alignments=False
    )

    aligned_segments = result.get("segments", [])
    if len(aligned_segments) != len(segments):
        return segments  # alignment changed the segmentation -> keep the originals

    out: list[SegmentResult] = []
    for src, aligned in zip(segments, aligned_segments, strict=False):
        words = [
            WordResult(
                text=str(w["word"]),
                start_sec=float(w["start"]),
                end_sec=float(w["end"]),
                confidence=float(w["score"]) if w.get("score") is not None else None,
            )
            for w in aligned.get("words", [])
            if w.get("start") is not None and w.get("end") is not None
        ]
        out.append(
            SegmentResult(
                text=src.text, start_sec=src.start_sec, end_sec=src.end_sec,
                confidence=src.confidence, words=words or src.words,
                speaker_label=src.speaker_label,
            )
        )
    return out
