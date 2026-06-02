"""Map analysis results (in seconds) to DB-ready rows (samples + frames).

This is the precision-critical, ML-free core: word/segment boundaries become canonical
sample indices (at the asset's audio sample rate) and projected frame indices via the
time core, with In-points floored and Out-points ceiled so no audio is clipped
(docs/03-time-model.md).
"""

from __future__ import annotations

from typing import Any

from ..timebase import snap_in_to_frame, snap_out_to_frame
from .types import SegmentResult


def _sample(sec: float, sample_rate: int) -> int:
    return round(sec * sample_rate)


def map_segment(
    seg: SegmentResult,
    audio_sample_rate: int,
    rate_num: int,
    rate_den: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return ``(segment_row, word_rows)`` with sample + frame fields filled in."""
    start_sample = _sample(seg.start_sec, audio_sample_rate)
    end_sample = _sample(seg.end_sec, audio_sample_rate)
    seg_row: dict[str, Any] = {
        "start_sample": start_sample,
        "end_sample": end_sample,
        "start_frame": snap_in_to_frame(start_sample, audio_sample_rate, rate_num, rate_den),
        "end_frame": snap_out_to_frame(end_sample, audio_sample_rate, rate_num, rate_den),
        "text": seg.text,
        "confidence": seg.confidence,
        "speaker_label": seg.speaker_label,
    }

    word_rows: list[dict[str, Any]] = []
    for idx, word in enumerate(seg.words):
        ws = _sample(word.start_sec, audio_sample_rate)
        we = _sample(word.end_sec, audio_sample_rate)
        word_rows.append(
            {
                "idx": idx,
                "start_sample": ws,
                "end_sample": we,
                "start_frame": snap_in_to_frame(ws, audio_sample_rate, rate_num, rate_den),
                "end_frame": snap_out_to_frame(we, audio_sample_rate, rate_num, rate_den),
                "text": word.text,
                "confidence": word.confidence,
                "is_punctuation": word.is_punctuation,
            }
        )
    return seg_row, word_rows
