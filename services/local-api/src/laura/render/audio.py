"""Audio overlay descriptors used by render/export paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AudioOverlay:
    """An audio-only clip positioned on a sequence timeline.

    Frames are integer and end-exclusive, matching Laura's timeline invariant.
    ``asset_in_frame`` is the source offset inside the audio asset.
    """

    path: Path
    seq_in_frame: int
    seq_out_frame_exclusive: int
    asset_in_frame: int = 0
    gain_percent: int = 100
    fade_in_frames: int = 0
    fade_out_frames: int = 0
    mix_mode: str = "mix"
    ducking_percent: int = 100
