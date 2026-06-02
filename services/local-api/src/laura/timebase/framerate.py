"""FrameRate — rational frame rate with drop-frame awareness.

Drop-frame affects only NUMBERING, never physical frame duration (ADR-0005).
Internal arithmetic always uses the nominal integer rate; drop-frame is applied
only when formatting/parsing a timecode string.
"""

from __future__ import annotations

from dataclasses import dataclass

from .rounding import Rounding, div_round


@dataclass(frozen=True)
class FrameRate:
    """A frame rate of ``rate_num/rate_den`` fps, optionally drop-frame numbered."""

    rate_num: int
    rate_den: int = 1
    drop_frame: bool = False

    def __post_init__(self) -> None:
        if self.rate_num <= 0 or self.rate_den <= 0:
            raise ValueError("rate_num and rate_den must be positive")
        if self.drop_frame and not self.supports_drop_frame:
            raise ValueError(
                f"drop_frame is only valid for 29.97/59.94-style rates, got "
                f"{self.rate_num}/{self.rate_den}"
            )

    @property
    def nominal(self) -> int:
        """Nominal integer rate used for timecode math (e.g. 30 for 30000/1001)."""
        return div_round(self.rate_num, self.rate_den, Rounding.HALF_UP)

    @property
    def supports_drop_frame(self) -> bool:
        """True for rates whose nominal is a multiple of 30 over a 1001 denom."""
        return self.rate_den == 1001 and self.nominal % 30 == 0

    @property
    def drop_count(self) -> int:
        """Frames dropped per dropping minute (2 at 29.97, 4 at 59.94)."""
        return 2 * (self.nominal // 30)

    @property
    def fps(self) -> float:
        """Float fps — DISPLAY only."""
        return self.rate_num / self.rate_den

    def with_drop_frame(self, drop: bool) -> FrameRate:
        return FrameRate(self.rate_num, self.rate_den, drop)


# Canonical presets (docs/03-time-model.md). NDF by default; DF variants explicit.
FPS_23_976 = FrameRate(24000, 1001)
FPS_24 = FrameRate(24, 1)
FPS_25 = FrameRate(25, 1)
FPS_29_97_NDF = FrameRate(30000, 1001, drop_frame=False)
FPS_29_97_DF = FrameRate(30000, 1001, drop_frame=True)
FPS_30 = FrameRate(30, 1)
FPS_50 = FrameRate(50, 1)
FPS_59_94_NDF = FrameRate(60000, 1001, drop_frame=False)
FPS_59_94_DF = FrameRate(60000, 1001, drop_frame=True)
FPS_60 = FrameRate(60, 1)

ALL_PRESETS: tuple[FrameRate, ...] = (
    FPS_23_976,
    FPS_24,
    FPS_25,
    FPS_29_97_NDF,
    FPS_29_97_DF,
    FPS_30,
    FPS_50,
    FPS_59_94_NDF,
    FPS_59_94_DF,
    FPS_60,
)
