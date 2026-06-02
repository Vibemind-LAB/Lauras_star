"""CMX3600 EDL writer (deterministic).

Linear, single-channel cut list. Source and record timecodes come from the time core
so drop-frame is handled correctly. Structurally limited (no multi-lane, effects, or
speaker metadata) — the preflight in ``validate.py`` reports what is dropped.
"""

from __future__ import annotations

from ..timebase import FrameRate, frames_to_timecode
from .timeline import Timeline

_REEL = "AX"  # auxiliary reel for clips without an assigned tape name


def timeline_to_edl(timeline: Timeline) -> str:
    fr = FrameRate(timeline.rate_num, timeline.rate_den, timeline.drop_frame)
    fcm = "DROP FRAME" if timeline.drop_frame else "NON-DROP FRAME"
    lines: list[str] = [f"TITLE: {timeline.name}", f"FCM: {fcm}", ""]

    for i, clip in enumerate(timeline.ordered(), start=1):
        src_in = frames_to_timecode(clip.src_in_frame, fr)
        src_out = frames_to_timecode(clip.src_out_frame_exclusive, fr)
        rec_in = frames_to_timecode(clip.seq_in_frame, fr)
        rec_out = frames_to_timecode(clip.seq_out_frame_exclusive, fr)
        lines.append(
            f"{i:03d}  {_REEL:<8} V     C        {src_in} {src_out} {rec_in} {rec_out}"
        )
        lines.append(f"* FROM CLIP NAME: {clip.name}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
