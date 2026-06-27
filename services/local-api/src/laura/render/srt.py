"""Pure SRT (SubRip Subtitle) builder for sequence-transcript export.

No file I/O, no ffmpeg calls — pure string builder.

Invariants (CLAUDE.md §"Nicht verhandelbare Invarianten"):
- All timing state is kept in integer frames.
- SRT millisecond timecodes are produced *only* at the format edge.
- Ranges are end-exclusive (``seq_out_frame_exclusive``).
- Internal calculation is always NDF (no drop-frame).
"""

from __future__ import annotations

from typing import Any


def _frame_to_srt_time(frame: int, rate_num: int, rate_den: int) -> str:
    """Convert an integer frame index to SRT time ``HH:MM:SS,mmm``.

    SRT uses milliseconds (1/1000 s) with a comma separator.

    The conversion uses pure integer arithmetic to avoid float drift:
    ``total_ms = frame * rate_den * 1000 // rate_num``  (floor — consistent with
    how the ASS helper rounds centiseconds).

    Args:
        frame:    Non-negative integer frame index (0-based).
        rate_num: Frame-rate numerator (e.g. 30 for 30 fps, 30000 for 29.97).
        rate_den: Frame-rate denominator (e.g. 1 for 30 fps, 1001 for 29.97).
    """
    if rate_num <= 0 or rate_den <= 0:
        raise ValueError(f"invalid frame rate {rate_num}/{rate_den}")
    # total milliseconds — integer floor division avoids float drift.
    total_ms = frame * rate_den * 1000 // rate_num

    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60

    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def sequence_transcript_to_srt(
    segments: list[dict[str, Any]],
    rate_num: int,
    rate_den: int,
) -> str:
    """Render a list of sequence transcript blocks as a valid SRT string.

    Each segment must carry:
    - ``seq_in_frame`` (int): start frame in sequence space (inclusive).
    - ``seq_out_frame_exclusive`` (int): end frame in sequence space (exclusive).
    - ``text`` (str): caption text for the segment.

    Segments whose ``seq_in_frame >= seq_out_frame_exclusive`` or whose ``text``
    is empty/blank are silently skipped (no entry emitted).

    Returns an empty string when *segments* is empty or all are skipped.

    The SRT index counter is 1-based and continuous (no gaps); the format is::

        <index>
        HH:MM:SS,mmm --> HH:MM:SS,mmm
        <text>
        <blank line>

    Args:
        segments: Sequence transcript blocks (each a dict with the keys above).
        rate_num: Frame-rate numerator.
        rate_den: Frame-rate denominator.
    """
    lines: list[str] = []
    idx = 0
    for seg in segments:
        in_frame = int(seg["seq_in_frame"])
        out_frame = int(seg["seq_out_frame_exclusive"])
        text = str(seg.get("text", "")).strip()

        if in_frame >= out_frame or not text:
            continue

        idx += 1
        start_ts = _frame_to_srt_time(in_frame, rate_num, rate_den)
        end_ts = _frame_to_srt_time(out_frame, rate_num, rate_den)

        lines.append(str(idx))
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(text)
        lines.append("")  # blank separator line

    return "\n".join(lines)
