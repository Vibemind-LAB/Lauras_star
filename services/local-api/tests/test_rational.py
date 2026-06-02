"""Tests for RationalTime: rescale, ordering across rates, arithmetic."""

from __future__ import annotations

import pytest

from laura.timebase import RationalTime, Rounding


def test_rescale_simple() -> None:
    # 1 frame at 30fps == 2 frames at 60fps
    assert RationalTime(1, 30).rescale_to(60, 1) == RationalTime(2, 60)


def test_rescale_samples_to_2997_frames() -> None:
    # 48000 samples (1s) projected to 29.97 fps -> 30 frames (29.97 rounds to 30)
    one_second = RationalTime(48000, 48000)
    assert one_second.rescale_to(30000, 1001).value == 30


def test_rescale_rounding_modes() -> None:
    # 47000 samples at 48000 -> frames at 30fps = 29.375
    t = RationalTime(47000, 48000)
    assert t.rescale_to(30, 1, Rounding.FLOOR).value == 29
    assert t.rescale_to(30, 1, Rounding.CEIL).value == 30
    assert t.rescale_to(30, 1, Rounding.HALF_EVEN).value == 29


def test_to_seconds_is_display_only() -> None:
    assert RationalTime(30, 30).to_seconds() == 1.0
    assert RationalTime(48048, 48000).to_seconds() == pytest.approx(1.001)


def test_equality_across_rates() -> None:
    assert RationalTime(30, 30) == RationalTime(60, 60)        # both 1 second
    assert RationalTime(1, 24) != RationalTime(1, 30)
    assert hash(RationalTime(30, 30)) == hash(RationalTime(60, 60))


def test_ordering_across_rates() -> None:
    assert RationalTime(1, 30) < RationalTime(1, 24)          # 1/30s < 1/24s
    assert RationalTime(2, 24) > RationalTime(1, 24)
    assert RationalTime(1, 25) <= RationalTime(1, 25)


def test_same_rate_arithmetic() -> None:
    assert RationalTime(10, 30) + RationalTime(5, 30) == RationalTime(15, 30)
    assert RationalTime(10, 30) - RationalTime(5, 30) == RationalTime(5, 30)


def test_cross_rate_arithmetic_rejected() -> None:
    with pytest.raises(ValueError):
        _ = RationalTime(1, 30) + RationalTime(1, 24)


def test_validation() -> None:
    with pytest.raises(ValueError):
        RationalTime(1, 0)
    with pytest.raises(TypeError):
        RationalTime(True, 30)  # bool is not an acceptable int value
