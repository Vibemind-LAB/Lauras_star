"""Reel duration cap: deterministic tail-trim of the rendered clip list to the first N seconds,
so a reel can satisfy a platform's max-duration limit. Frame-exact, end-exclusive."""

from __future__ import annotations

from pathlib import Path

from laura.render.handlers import cap_clips_to_frames

P = Path("/v.mp4")


def test_cap_noop_when_clips_fit_within_budget() -> None:
    clips = [(P, 0, 50), (P, 100, 130)]  # 50 + 30 = 80 frames total
    assert cap_clips_to_frames(clips, 200) == clips


def test_cap_noop_on_zero_or_negative_budget() -> None:
    clips = [(P, 0, 50)]
    assert cap_clips_to_frames(clips, 0) == clips
    assert cap_clips_to_frames(clips, -10) == clips


def test_cap_exact_boundary_keeps_whole_clips() -> None:
    clips = [(P, 0, 50), (P, 100, 130), (P, 200, 260)]
    # Budget 80 == first two clips exactly -> drop the third, no mid-clip trim.
    assert cap_clips_to_frames(clips, 80) == [(P, 0, 50), (P, 100, 130)]


def test_cap_trims_the_boundary_clip_and_drops_the_rest() -> None:
    clips = [(P, 0, 50), (P, 100, 130), (P, 200, 260)]
    # Budget 60: keep clip 1 (50), then 10 more frames of clip 2 (100..110), drop clip 3.
    out = cap_clips_to_frames(clips, 60)
    assert out == [(P, 0, 50), (P, 100, 110)]
    assert sum(b - a for _p, a, b in out) == 60  # exact budget


def test_cap_within_first_clip() -> None:
    clips = [(P, 10, 100), (P, 200, 260)]
    out = cap_clips_to_frames(clips, 30)
    assert out == [(P, 10, 40)]
    for _p, a, b in out:
        assert isinstance(a, int) and isinstance(b, int) and b > a


def test_cap_empty_clips() -> None:
    assert cap_clips_to_frames([], 100) == []
