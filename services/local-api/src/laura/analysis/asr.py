"""ASR via faster-whisper (optional extra: ``[asr]``).

Returns segments + word timestamps in SECONDS; all sample/frame mapping happens in
``mapping.py`` through the time core. faster-whisper provides word timestamps directly;
WhisperX (extra ``[align]``) is a future refinement pass for tighter alignment.
"""

from __future__ import annotations

import os
from pathlib import Path

from .types import SegmentResult, WordResult

DEFAULT_MODEL = "base"


def faster_whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        return False
    return True


def _run(
    audio_path: Path | str, model_size: str, language: str | None, device: str
) -> list[SegmentResult]:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device=device, compute_type="int8")
    segments, _info = model.transcribe(str(audio_path), word_timestamps=True, language=language)

    results: list[SegmentResult] = []
    for seg in segments:  # encoding (and any GPU load) happens lazily while iterating
        words = [
            WordResult(
                text=w.word,
                start_sec=float(w.start),
                end_sec=float(w.end),
                confidence=getattr(w, "probability", None),
            )
            for w in (seg.words or [])
        ]
        results.append(
            SegmentResult(
                text=seg.text.strip(),
                start_sec=float(seg.start),
                end_sec=float(seg.end),
                confidence=getattr(seg, "avg_logprob", None),
                words=words,
            )
        )
    return results


def transcribe(
    audio_path: Path | str,
    *,
    model_size: str = DEFAULT_MODEL,
    language: str | None = None,
    device: str | None = None,
) -> list[SegmentResult]:
    """Transcribe an audio file (lazy faster-whisper; raises if ``[asr]`` is absent).

    ``device`` defaults to ``LAURA_ASR_DEVICE`` or ``"auto"``. A CUDA load failure
    (e.g. missing cuBLAS on a half-configured GPU host) transparently falls back to CPU
    so the pipeline runs everywhere.
    """
    chosen = device or os.environ.get("LAURA_ASR_DEVICE") or "auto"
    try:
        return _run(audio_path, model_size, language, chosen)
    except Exception:  # noqa: BLE001 - GPU libraries may be missing; retry on CPU
        if chosen == "cpu":
            raise
        return _run(audio_path, model_size, language, "cpu")
