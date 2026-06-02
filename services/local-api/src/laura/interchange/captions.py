"""SRT and WebVTT subtitle generation from transcript segments.

Deterministic, dependency-light, millisecond-precise. Segment dicts carry
``start_frame``/``end_frame``/``text``/``speaker_label``; times are derived from the
frame positions via exact integer math.
"""

from __future__ import annotations

import textwrap
from typing import Any

from ..timebase import Rounding, div_round


def _frame_to_ms(frame: int, rate_num: int, rate_den: int) -> int:
    # ms = frame * (rate_den/rate_num) * 1000
    return div_round(frame * rate_den * 1000, rate_num, Rounding.HALF_EVEN)


def _clock(ms: int, sep: str) -> str:
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


MAX_LINE = 42  # characters per subtitle line (readability cap)


def _wrap(text: str, width: int = MAX_LINE) -> str:
    wrapped = textwrap.wrap(text, width) or [text]
    return "\n".join(wrapped)


def join_words(words: list[dict[str, Any]]) -> str:
    """Join transcript words into caption text (no space before punctuation)."""
    out = ""
    for word in words:
        token = str(word["text"])
        if out and not word.get("is_punctuation"):
            out += " "
        out += token
    return out


def _line(seg: dict[str, Any]) -> str:
    text = str(seg["text"]).strip()
    label = seg.get("speaker_label")
    return _wrap(f"{label}: {text}" if label else text)


def segments_to_srt(segments: list[dict[str, Any]], rate_num: int, rate_den: int) -> str:
    out: list[str] = []
    for i, seg in enumerate(segments, start=1):
        start = _clock(_frame_to_ms(seg["start_frame"], rate_num, rate_den), ",")
        end = _clock(_frame_to_ms(seg["end_frame"], rate_num, rate_den), ",")
        out += [str(i), f"{start} --> {end}", _line(seg), ""]
    return "\n".join(out).rstrip() + "\n"


def segments_to_vtt(segments: list[dict[str, Any]], rate_num: int, rate_den: int) -> str:
    out: list[str] = ["WEBVTT", ""]
    for seg in segments:
        start = _clock(_frame_to_ms(seg["start_frame"], rate_num, rate_den), ".")
        end = _clock(_frame_to_ms(seg["end_frame"], rate_num, rate_den), ".")
        out += [f"{start} --> {end}", _line(seg), ""]
    return "\n".join(out).rstrip() + "\n"
