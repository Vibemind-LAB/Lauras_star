"""Reel duration cap: deterministic tail-trim of the rendered clip list to the first N seconds,
so a reel can satisfy a platform's max-duration limit. Frame-exact, end-exclusive."""

from __future__ import annotations

from pathlib import Path

from laura.render.handlers import cap_clips_to_frames, snap_budget_to_word_boundary

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


# --- snap_budget_to_word_boundary: end a capped reel on a complete word, not mid-word -----------


def test_snap_picks_latest_word_end_at_or_below_budget() -> None:
    # Words end at 50, 92, 130 (seq frames); budget 100 lands between 92 and 130 -> snap to 92.
    assert snap_budget_to_word_boundary(100, 200, [50, 92, 130]) == 92


def test_snap_keeps_budget_when_a_word_ends_exactly_on_it() -> None:
    # An exact word boundary at the budget is honoured (end-exclusive, <=).
    assert snap_budget_to_word_boundary(100, 200, [40, 100, 160]) == 100


def test_snap_noop_without_transcript_matches_plain_cap() -> None:
    # No word boundary in (0, budget] -> return the budget unchanged so the tail-trim is identical
    # to cap_clips_to_frames (B-roll / music with no words must not be snapped).
    assert snap_budget_to_word_boundary(100, 200, []) == 100
    assert snap_budget_to_word_boundary(100, 200, [130, 180]) == 100  # all words past budget


def test_snap_noop_when_cut_already_fits() -> None:
    # total <= budget -> nothing is cut, so no snap (every word would otherwise pull the end in).
    assert snap_budget_to_word_boundary(200, 150, [40, 90]) == 200


def test_snap_noop_on_non_positive_budget() -> None:
    assert snap_budget_to_word_boundary(0, 200, [40, 90]) == 0
    assert snap_budget_to_word_boundary(-5, 200, [40, 90]) == -5


def test_snap_ignores_non_positive_word_ends() -> None:
    # A degenerate 0/negative word-end is not a valid cut point.
    assert snap_budget_to_word_boundary(100, 200, [0, -3, 70]) == 70


def test_snap_composes_with_cap_to_end_on_a_word() -> None:
    # End-to-end: snap the budget, then tail-trim — the reel ends exactly on the word boundary.
    clips = [(P, 0, 200)]
    snapped = snap_budget_to_word_boundary(100, 200, [50, 92, 130])
    out = cap_clips_to_frames(clips, snapped)
    assert out == [(P, 0, 92)]
