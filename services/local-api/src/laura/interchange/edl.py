"""CMX3600 EDL writer (deterministic).

Linear, single-channel cut list. Source and record timecodes come from the time core
so drop-frame is handled correctly. Structurally limited (no multi-lane, effects, or
speaker metadata) — the preflight in ``validate.py`` reports what is dropped.

**L/J split edits (3b).** Plain CMX3600 has no single "split edit" event: picture and sound are
separate channels. The honest representation is PARALLEL events — a ``V`` (video) event on the
visual cut and a separate ``A`` (audio) event whose record timecode sits on the OFFSET sound cut.
When ``audio_clips`` are supplied this writer emits exactly that (the standard EDL idiom for L/J
cuts); with no accepted split it stays the byte-for-byte ``V``-only list Laura emits today.
"""

from __future__ import annotations

from ..timebase import FrameRate, frames_to_timecode
from .timeline import Clip, Timeline

_REEL = "AX"  # auxiliary reel for clips without an assigned tape name


def _event(
    index: int, channel: str, clip: Clip, fr: FrameRate
) -> list[str]:
    src_in = frames_to_timecode(clip.src_in_frame, fr)
    src_out = frames_to_timecode(clip.src_out_frame_exclusive, fr)
    rec_in = frames_to_timecode(clip.seq_in_frame, fr)
    rec_out = frames_to_timecode(clip.seq_out_frame_exclusive, fr)
    return [
        f"{index:03d}  {_REEL:<8} {channel:<5} C        {src_in} {src_out} {rec_in} {rec_out}",
        f"* FROM CLIP NAME: {clip.name}",
        "",
    ]


def timeline_to_edl(timeline: Timeline, audio_clips: list[Clip] | None = None) -> str:
    """Emit a CMX3600 EDL.

    Without ``audio_clips`` this is the byte-for-byte ``V``-only list Laura emits today. With them
    (an accepted L/J split) it emits parallel ``V`` picture events and ``A`` audio events on the
    offset sound cut — the closest faithful CMX3600 form for a split edit (see module docstring).
    """
    fr = FrameRate(timeline.rate_num, timeline.rate_den, timeline.drop_frame)
    fcm = "DROP FRAME" if timeline.drop_frame else "NON-DROP FRAME"
    lines: list[str] = [f"TITLE: {timeline.name}", f"FCM: {fcm}", ""]

    index = 1
    for clip in timeline.ordered():
        lines.extend(_event(index, "V", clip, fr))
        index += 1
    # Parallel audio events on the offset sound cut — only when an L/J split is accepted, so the
    # no-split EDL stays the single-channel V-only list Laura produces today (purely additive).
    for clip in sorted(audio_clips or [], key=lambda c: c.seq_in_frame):
        lines.extend(_event(index, "A", clip, fr))
        index += 1
    return "\n".join(lines).rstrip() + "\n"
