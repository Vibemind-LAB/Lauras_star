"""SMPTE timecode <-> integer frame index.

NDF formats as ``HH:MM:SS:FF`` (':' before frames); drop-frame as ``HH:MM:SS;FF``
(';' before frames). Drop-frame drops ``drop_count`` frame NUMBERS at the start of
every minute except every tenth minute. See docs/03-time-model.md.
"""

from __future__ import annotations

import re

from .framerate import FrameRate

_TC_RE = re.compile(
    r"^(?P<neg>-)?(?P<hh>\d{2,}):(?P<mm>\d{2}):(?P<ss>\d{2})(?P<sep>[:;.])(?P<ff>\d{2,})$"
)


def frames_to_timecode(frame_index: int, fr: FrameRate) -> str:
    """Format an absolute frame index as a SMPTE timecode string."""
    if frame_index < 0:
        return "-" + frames_to_timecode(-frame_index, fr)

    nominal = fr.nominal
    if fr.drop_frame:
        drop = fr.drop_count
        full_min = nominal * 60               # nominal frames per minute
        frames_per_10min = nominal * 600 - 9 * drop
        frames_per_drop_min = full_min - drop
        blocks, rem = divmod(frame_index, frames_per_10min)
        added = blocks * 9 * drop
        if rem >= full_min:
            added += ((rem - full_min) // frames_per_drop_min + 1) * drop
        n = frame_index + added
        sep = ";"
    else:
        n = frame_index
        sep = ":"

    ff = n % nominal
    total_seconds = n // nominal
    ss = total_seconds % 60
    mm = (total_seconds // 60) % 60
    hh = total_seconds // 3600
    return f"{hh:02d}:{mm:02d}:{ss:02d}{sep}{ff:02d}"


def timecode_to_frames(tc: str, fr: FrameRate) -> int:
    """Parse a SMPTE timecode string into an absolute frame index."""
    m = _TC_RE.match(tc.strip())
    if not m:
        raise ValueError(f"invalid timecode: {tc!r}")
    neg = m.group("neg") is not None
    hh, mm, ss = int(m.group("hh")), int(m.group("mm")), int(m.group("ss"))
    ff = int(m.group("ff"))

    nominal = fr.nominal
    if ff >= nominal:
        raise ValueError(f"frame field {ff} exceeds nominal rate {nominal}")
    if mm > 59 or ss > 59:
        raise ValueError(f"minutes/seconds out of range in {tc!r}")

    total_minutes = hh * 60 + mm
    n = (total_minutes * 60 + ss) * nominal + ff
    if fr.drop_frame:
        dropping_minutes = total_minutes - total_minutes // 10
        n -= fr.drop_count * dropping_minutes
    return -n if neg else n
