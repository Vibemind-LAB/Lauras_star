"""Speaker diarization via pyannote.audio (optional extra: ``[diarize]``).

Needs a Hugging Face token (env ``HF_TOKEN``) to download the pretrained pipeline.
``assign_speakers`` is a pure function (no ML) and is unit-tested independently.
"""

from __future__ import annotations

import os
from pathlib import Path

from .types import SegmentResult, SpeakerTurn


def pyannote_available() -> bool:
    try:
        import pyannote.audio  # noqa: F401
    except Exception:
        return False
    return True


def diarize(audio_path: Path | str) -> list[SpeakerTurn]:
    """Run diarization. Raises (via lazy import) if ``[diarize]`` is absent."""
    from pyannote.audio import Pipeline

    token = os.environ.get("HF_TOKEN")
    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", use_auth_token=token)
    annotation = pipeline(str(audio_path))

    turns: list[SpeakerTurn] = []
    for segment, _track, label in annotation.itertracks(yield_label=True):
        turns.append(SpeakerTurn(start_sec=float(segment.start), end_sec=float(segment.end),
                                 label=str(label)))
    return turns


def assign_speakers(segments: list[SegmentResult], turns: list[SpeakerTurn]) -> None:
    """Assign each segment the speaker whose turn overlaps it most (in place)."""
    if not turns:
        return
    for seg in segments:
        best_label: str | None = None
        best_overlap = 0.0
        for turn in turns:
            overlap = min(seg.end_sec, turn.end_sec) - max(seg.start_sec, turn.start_sec)
            if overlap > best_overlap:
                best_overlap = overlap
                best_label = turn.label
        seg.speaker_label = best_label
