"""Laura timebase — frame-/sample-accurate time core.

The single most important module in the system (docs/03-time-model.md, ADR-0005).
All editorial state is integer frames; audio is integer samples; rates are rational.
"""

from __future__ import annotations

from .framerate import (
    ALL_PRESETS,
    FPS_23_976,
    FPS_24,
    FPS_25,
    FPS_29_97_DF,
    FPS_29_97_NDF,
    FPS_30,
    FPS_50,
    FPS_59_94_DF,
    FPS_59_94_NDF,
    FPS_60,
    FrameRate,
)
from .ranges import FrameRange, MediaRange
from .rational import RationalTime
from .rounding import Rounding, div_round
from .sampling import (
    frame_to_sample,
    sample_to_frame,
    snap_in_to_frame,
    snap_out_to_frame,
)
from .timecode import frames_to_timecode, timecode_to_frames

__all__ = [
    "ALL_PRESETS",
    "FPS_23_976",
    "FPS_24",
    "FPS_25",
    "FPS_29_97_DF",
    "FPS_29_97_NDF",
    "FPS_30",
    "FPS_50",
    "FPS_59_94_DF",
    "FPS_59_94_NDF",
    "FPS_60",
    "FrameRange",
    "FrameRate",
    "MediaRange",
    "RationalTime",
    "Rounding",
    "div_round",
    "frame_to_sample",
    "frames_to_timecode",
    "sample_to_frame",
    "snap_in_to_frame",
    "snap_out_to_frame",
    "timecode_to_frames",
]
