"""ASR via faster-whisper (optional extra: ``[asr]``).

Returns segments + word timestamps in SECONDS; all sample/frame mapping happens in
``mapping.py`` through the time core. faster-whisper provides word timestamps directly;
WhisperX (extra ``[align]``) is a future refinement pass for tighter alignment.
"""

from __future__ import annotations

from pathlib import Path

from .types import SegmentResult, WordResult

DEFAULT_MODEL = "base"


def faster_whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        return False
    return True


def transcribe(
    audio_path: Path | str,
    *,
    model_size: str = DEFAULT_MODEL,
    language: str | None = None,
) -> list[SegmentResult]:
    """Transcribe an audio file. Raises (via lazy import) if ``[asr]`` is absent."""
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="auto", compute_type="int8")
    segments, _info = model.transcribe(str(audio_path), word_timestamps=True, language=language)

    results: list[SegmentResult] = []
    for seg in segments:
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
