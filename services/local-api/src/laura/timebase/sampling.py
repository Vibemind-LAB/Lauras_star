"""Audio sample <-> video frame projection and word snapping.

Audio/alignment is canonical in SAMPLES; frame positions are a PROJECTION for the
UI and must not be written back to the sample source (ADR-0005). Word In-points
snap with FLOOR, Out-points (exclusive) snap with CEIL, so a clipped word never
loses leading/trailing audio.
"""

from __future__ import annotations

from .rounding import Rounding, div_round


def sample_to_frame(
    sample: int,
    sample_rate: int,
    rate_num: int,
    rate_den: int,
    rounding: Rounding = Rounding.HALF_EVEN,
) -> int:
    """Project an audio sample index onto a frame index at ``rate_num/rate_den``."""
    if sample_rate <= 0 or rate_num <= 0 or rate_den <= 0:
        raise ValueError("rates must be positive")
    # frame = sample/sample_rate * rate_num/rate_den
    return div_round(sample * rate_num, sample_rate * rate_den, rounding)


def frame_to_sample(
    frame: int,
    sample_rate: int,
    rate_num: int,
    rate_den: int,
    rounding: Rounding = Rounding.HALF_EVEN,
) -> int:
    """Project a frame index back onto an audio sample index."""
    if sample_rate <= 0 or rate_num <= 0 or rate_den <= 0:
        raise ValueError("rates must be positive")
    # sample = frame * (rate_den/rate_num) * sample_rate
    return div_round(frame * rate_den * sample_rate, rate_num, rounding)


def snap_in_to_frame(sample: int, sample_rate: int, rate_num: int, rate_den: int) -> int:
    """Snap a word/segment In-point (inclusive) to a frame using FLOOR."""
    return sample_to_frame(sample, sample_rate, rate_num, rate_den, Rounding.FLOOR)


def snap_out_to_frame(sample: int, sample_rate: int, rate_num: int, rate_den: int) -> int:
    """Snap a word/segment Out-point (exclusive) to a frame using CEIL."""
    return sample_to_frame(sample, sample_rate, rate_num, rate_den, Rounding.CEIL)
