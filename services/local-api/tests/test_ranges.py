"""Tests for end-exclusive FrameRange math and MediaRange speed mapping."""

from __future__ import annotations

import pytest

from laura.timebase import FrameRange, MediaRange


def test_length_and_empty() -> None:
    assert FrameRange(10, 25).length == 15
    assert FrameRange(5, 5).is_empty is True


def test_contains_end_exclusive() -> None:
    r = FrameRange(10, 20)
    assert r.contains(10) is True
    assert r.contains(19) is True
    assert r.contains(20) is False        # end is EXCLUSIVE


def test_overlaps_and_intersection() -> None:
    a = FrameRange(0, 10)
    b = FrameRange(5, 15)
    c = FrameRange(10, 20)                 # touches a at 10 but does not overlap
    assert a.overlaps(b) is True
    assert a.overlaps(c) is False
    assert a.intersection(b) == FrameRange(5, 10)
    assert a.intersection(c) is None


def test_shifted() -> None:
    assert FrameRange(10, 20).shifted(-5) == FrameRange(5, 15)


def test_subtract_two_pieces() -> None:
    assert FrameRange(0, 100).subtract(FrameRange(40, 60)) == [
        FrameRange(0, 40),
        FrameRange(60, 100),
    ]


def test_subtract_edges_and_disjoint() -> None:
    assert FrameRange(0, 100).subtract(FrameRange(0, 30)) == [FrameRange(30, 100)]
    assert FrameRange(0, 100).subtract(FrameRange(70, 100)) == [FrameRange(0, 70)]
    assert FrameRange(0, 100).subtract(FrameRange(200, 300)) == [FrameRange(0, 100)]
    assert FrameRange(0, 100).subtract(FrameRange(0, 100)) == []


def test_invalid_range() -> None:
    with pytest.raises(ValueError):
        FrameRange(10, 5)


def test_media_range_speed() -> None:
    def mr(speed_num: int = 1, speed_den: int = 1) -> MediaRange:
        return MediaRange(
            src_in_frame=0,
            src_out_frame_exclusive=100,
            seq_in_frame=0,
            seq_out_frame_exclusive=0,
            src_rate_num=30,
            src_rate_den=1,
            speed_num=speed_num,
            speed_den=speed_den,
        )

    assert mr().expected_seq_length() == 100              # normal: 100 -> 100
    assert mr(speed_num=2, speed_den=1).expected_seq_length() == 50   # 2x  -> 50
    assert mr(speed_num=1, speed_den=2).expected_seq_length() == 200  # 0.5x -> 200


def test_media_range_validation() -> None:
    with pytest.raises(ValueError):
        MediaRange(
            src_in_frame=10,
            src_out_frame_exclusive=5,
            seq_in_frame=0,
            seq_out_frame_exclusive=0,
            src_rate_num=30,
        )
