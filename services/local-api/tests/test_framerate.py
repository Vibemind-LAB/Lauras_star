"""Tests for FrameRate nominal/drop-frame semantics."""

from __future__ import annotations

import pytest

from laura.timebase import (
    FPS_23_976,
    FPS_29_97_DF,
    FPS_29_97_NDF,
    FPS_30,
    FPS_59_94_DF,
    FrameRate,
)


def test_nominal_rates() -> None:
    assert FPS_30.nominal == 30
    assert FPS_29_97_NDF.nominal == 30
    assert FPS_59_94_DF.nominal == 60
    assert FPS_23_976.nominal == 24


def test_drop_frame_support() -> None:
    assert FPS_29_97_NDF.supports_drop_frame is True
    assert FPS_59_94_DF.supports_drop_frame is True
    assert FPS_30.supports_drop_frame is False
    assert FPS_23_976.supports_drop_frame is False  # 24 is not a multiple of 30


def test_drop_count() -> None:
    assert FPS_29_97_DF.drop_count == 2
    assert FPS_59_94_DF.drop_count == 4


def test_invalid_drop_frame_rejected() -> None:
    with pytest.raises(ValueError):
        FrameRate(24, 1, drop_frame=True)          # 24p cannot be drop-frame
    with pytest.raises(ValueError):
        FrameRate(24000, 1001, drop_frame=True)    # 23.976 cannot be drop-frame


def test_invalid_rate_rejected() -> None:
    with pytest.raises(ValueError):
        FrameRate(0, 1)
    with pytest.raises(ValueError):
        FrameRate(30, 0)
