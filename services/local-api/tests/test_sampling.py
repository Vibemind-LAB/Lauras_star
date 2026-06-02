"""Tests for sample<->frame projection and word snapping."""

from __future__ import annotations

from laura.timebase import (
    frame_to_sample,
    sample_to_frame,
    snap_in_to_frame,
    snap_out_to_frame,
)


def test_sample_to_frame_integer_rate() -> None:
    assert sample_to_frame(48000, 48000, 30, 1) == 30      # 1 second -> 30 frames
    assert sample_to_frame(0, 48000, 30, 1) == 0


def test_sample_to_frame_2997() -> None:
    # 1 second of audio projected to 29.97 fps rounds to 30 frames
    assert sample_to_frame(48000, 48000, 30000, 1001) == 30


def test_frame_to_sample_roundtrip_region() -> None:
    # 30 frames at 29.97 ~ 1.001 s -> 48048 samples
    assert frame_to_sample(30, 48000, 30000, 1001) == 48048


def test_word_snapping_floor_ceil() -> None:
    # 47000 samples @ 48000 / 30fps = 29.375 frames
    assert sample_to_frame(47000, 48000, 30, 1) == 29       # default half-even
    assert snap_in_to_frame(47000, 48000, 30, 1) == 29      # In  -> floor
    assert snap_out_to_frame(47000, 48000, 30, 1) == 30     # Out -> ceil (keep the word whole)


def test_snapping_on_exact_frame_boundary() -> None:
    # 1600 samples @ 48000 / 30fps = exactly 1.0 frame -> floor == ceil
    assert snap_in_to_frame(1600, 48000, 30, 1) == 1
    assert snap_out_to_frame(1600, 48000, 30, 1) == 1
