"""Frame ranges (half-open) and source<->sequence media ranges.

All ranges are END-EXCLUSIVE: ``[start, end_exclusive)``. This is invariant across
the whole system (ADR-0005) and makes length/cut/overlap math off-by-one free.
"""

from __future__ import annotations

from dataclasses import dataclass

from .rounding import Rounding, div_round


@dataclass(frozen=True)
class FrameRange:
    """Half-open frame range ``[start, end_exclusive)`` on a single domain."""

    start: int
    end_exclusive: int

    def __post_init__(self) -> None:
        if self.end_exclusive < self.start:
            raise ValueError("end_exclusive must be >= start")

    @property
    def length(self) -> int:
        return self.end_exclusive - self.start

    @property
    def is_empty(self) -> bool:
        return self.length == 0

    def contains(self, frame: int) -> bool:
        return self.start <= frame < self.end_exclusive

    def overlaps(self, other: FrameRange) -> bool:
        return self.start < other.end_exclusive and other.start < self.end_exclusive

    def intersection(self, other: FrameRange) -> FrameRange | None:
        s = max(self.start, other.start)
        e = min(self.end_exclusive, other.end_exclusive)
        return FrameRange(s, e) if s < e else None

    def shifted(self, delta: int) -> FrameRange:
        return FrameRange(self.start + delta, self.end_exclusive + delta)

    def subtract(self, cut: FrameRange) -> list[FrameRange]:
        """Return this range with ``cut`` removed (0, 1, or 2 pieces). Lift semantics."""
        if not self.overlaps(cut):
            return [self]
        pieces: list[FrameRange] = []
        if self.start < cut.start:
            pieces.append(FrameRange(self.start, cut.start))
        if cut.end_exclusive < self.end_exclusive:
            pieces.append(FrameRange(cut.end_exclusive, self.end_exclusive))
        return pieces


@dataclass(frozen=True)
class MediaRange:
    """Maps a source clip span to a sequence span, with optional speed change.

    ``speed_num/speed_den`` is the playback-rate multiplier: 2/1 plays the source
    twice as fast, so the sequence span is half the source span.
    """

    src_in_frame: int
    src_out_frame_exclusive: int
    seq_in_frame: int
    seq_out_frame_exclusive: int
    src_rate_num: int
    src_rate_den: int = 1
    src_timecode_start: str | None = None
    speed_num: int = 1
    speed_den: int = 1

    def __post_init__(self) -> None:
        if self.src_out_frame_exclusive < self.src_in_frame:
            raise ValueError("source range end must be >= start")
        if self.seq_out_frame_exclusive < self.seq_in_frame:
            raise ValueError("sequence range end must be >= start")
        if self.speed_num <= 0 or self.speed_den <= 0:
            raise ValueError("speed must be positive")
        if self.src_rate_num <= 0 or self.src_rate_den <= 0:
            raise ValueError("src rate must be positive")

    @property
    def src_range(self) -> FrameRange:
        return FrameRange(self.src_in_frame, self.src_out_frame_exclusive)

    @property
    def seq_range(self) -> FrameRange:
        return FrameRange(self.seq_in_frame, self.seq_out_frame_exclusive)

    def expected_seq_length(self, rounding: Rounding = Rounding.HALF_EVEN) -> int:
        """Sequence length implied by source length and speed (frames are in the
        same nominal cadence as the sequence; source already proxied to CFR)."""
        return retimed_seq_length(
            self.src_range.length, self.speed_num, self.speed_den, rounding
        )


def retimed_seq_length(
    src_length: int,
    speed_num: int,
    speed_den: int,
    rounding: Rounding = Rounding.HALF_EVEN,
) -> int:
    """Sequence frames a source span of ``src_length`` occupies at ``speed_num/speed_den``.

    ``2/1`` (2x faster) halves the span; ``1/2`` (half speed) doubles it; ``1/1`` is identity.
    Deterministic integer rounding (ADR-0005).
    """
    if speed_num <= 0 or speed_den <= 0:
        raise ValueError("speed must be positive")
    return div_round(src_length * speed_den, speed_num, rounding)
