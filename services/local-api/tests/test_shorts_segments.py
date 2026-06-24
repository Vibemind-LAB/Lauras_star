"""Unit tests for :mod:`laura.analysis.shorts_segments` (pure, no IO, no ffmpeg).

TDD — all 12 cases from the architect contract:

1.  both_boundaries_legal
2.  speaker_turn_tagging
3.  duration_filter_excludes_too_short
4.  duration_filter_excludes_too_long
5.  multiple_windows_from_one_start
6.  transcript_safe_by_construction
7.  boundaries_only_on_legal_frames
8.  empty_when_fewer_than_two_legal_frames
9.  total_frames_clamp
10. max_candidates_cap_is_deterministic
11. invalid_duration_bounds_raise
12. end_exclusive_and_positive_duration
"""

from __future__ import annotations

import pytest

from laura.analysis.editorial import Word, _covering_word
from laura.analysis.shorts_segments import (
    DEFAULT_MAX_DURATION_S,
    DEFAULT_MIN_DURATION_S,
    _duration_bounds_frames,
    generate_candidates,
    legal_boundary_frames,
)
from laura.analysis.shorts_types import BoundaryKind, ShortCandidate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FPS_30 = (30, 1)  # rate_num=30, rate_den=1


def _words_covering(start: int, end: int) -> list[Word]:
    """One word that spans [start, end) — useful to verify transcript-safety."""
    return [Word(start_frame=start, end_frame=end)]


def _boundary_frame_set(pairs: list[tuple[int, BoundaryKind]]) -> set[int]:
    return {f for f, _ in pairs}


# ---------------------------------------------------------------------------
# legal_boundary_frames
# ---------------------------------------------------------------------------


def test_legal_boundary_frames_sentence_end_only() -> None:
    result = legal_boundary_frames({100, 200}, set())
    frames = [f for f, _ in result]
    kinds = {f: k for f, k in result}
    assert sorted(frames) == [100, 200]
    assert kinds[100] == "sentence_end"
    assert kinds[200] == "sentence_end"


def test_legal_boundary_frames_speaker_turn_only() -> None:
    result = legal_boundary_frames(set(), {50, 150})
    kinds = {f: k for f, k in result}
    assert kinds[50] == "speaker_turn"
    assert kinds[150] == "speaker_turn"


def test_legal_boundary_frames_sorted_ascending() -> None:
    result = legal_boundary_frames({300, 100}, {200})
    frames = [f for f, _ in result]
    assert frames == sorted(frames)


# Test 2 – speaker_turn_tagging:
# A frame in BOTH sets is tagged 'speaker_turn' (stronger seam wins).
def test_speaker_turn_tagging_wins_over_sentence_end() -> None:
    shared_frame = 500
    result = legal_boundary_frames({shared_frame, 200}, {shared_frame})
    kinds = {f: k for f, k in result}
    # The shared frame must be 'speaker_turn', not 'sentence_end'
    assert kinds[shared_frame] == "speaker_turn"
    # 200 is sentence_end only
    assert kinds[200] == "sentence_end"


# ---------------------------------------------------------------------------
# _duration_bounds_frames
# ---------------------------------------------------------------------------


def test_duration_bounds_frames_30fps() -> None:
    min_f, max_f = _duration_bounds_frames(15.0, 60.0, rate_num=30, rate_den=1)
    assert min_f == 450   # round(15 * 30/1)
    assert max_f == 1800  # round(60 * 30/1)


def test_duration_bounds_frames_non_integer_fps() -> None:
    # 24000/1001 ≈ 23.976 fps — NTSC drop-frame rate
    min_f, max_f = _duration_bounds_frames(10.0, 20.0, rate_num=24000, rate_den=1001)
    assert min_f == round(10.0 * 24000 / 1001)
    assert max_f == round(20.0 * 24000 / 1001)


# Test 11 – invalid_duration_bounds_raise
def test_duration_bounds_raises_min_greater_than_max() -> None:
    with pytest.raises(ValueError):
        _duration_bounds_frames(60.0, 15.0, rate_num=30, rate_den=1)


def test_duration_bounds_raises_zero_min() -> None:
    with pytest.raises(ValueError):
        _duration_bounds_frames(0.0, 60.0, rate_num=30, rate_den=1)


def test_duration_bounds_raises_negative_min() -> None:
    with pytest.raises(ValueError):
        _duration_bounds_frames(-1.0, 60.0, rate_num=30, rate_den=1)


def test_duration_bounds_raises_zero_rate_den() -> None:
    with pytest.raises(ValueError):
        _duration_bounds_frames(15.0, 60.0, rate_num=30, rate_den=0)


def test_duration_bounds_raises_negative_rate_den() -> None:
    with pytest.raises(ValueError):
        _duration_bounds_frames(15.0, 60.0, rate_num=30, rate_den=-1)


# ---------------------------------------------------------------------------
# generate_candidates — core contract
# ---------------------------------------------------------------------------


# Test 1 – both_boundaries_legal
def test_both_boundaries_legal() -> None:
    """30fps, frames {100, 1000}: span = 900 frames = 30s, inside [15, 60]."""
    sentence_frames = {100, 1000}
    speaker_frames: set[int] = set()
    result = generate_candidates(
        words=[],
        sentence_frames=sentence_frames,
        speaker_frames=speaker_frames,
        rate_num=30,
        rate_den=1,
        min_duration_s=15.0,
        max_duration_s=60.0,
    )
    assert len(result) == 1
    c = result[0]
    assert c.start_frame == 100
    assert c.end_frame_exclusive == 1000
    assert c.start_boundary == "sentence_end"
    assert c.end_boundary == "sentence_end"


# Test 2 – speaker_turn tagging on a candidate
def test_speaker_turn_tagging_on_candidate() -> None:
    """Frame 100 is in both sets -> tagged speaker_turn as start boundary."""
    shared = 100
    end = 1000  # 900 frames at 30fps = 30s, in range
    result = generate_candidates(
        words=[],
        sentence_frames={shared, end},
        speaker_frames={shared},
        rate_num=30,
        rate_den=1,
        min_duration_s=15.0,
        max_duration_s=60.0,
    )
    assert len(result) == 1
    assert result[0].start_boundary == "speaker_turn"
    assert result[0].end_boundary == "sentence_end"


def test_speaker_turn_tagging_on_end_boundary() -> None:
    """Frame 1000 is in both sets -> tagged speaker_turn as end boundary."""
    start = 100
    shared_end = 1000
    result = generate_candidates(
        words=[],
        sentence_frames={start, shared_end},
        speaker_frames={shared_end},
        rate_num=30,
        rate_den=1,
        min_duration_s=15.0,
        max_duration_s=60.0,
    )
    assert len(result) == 1
    assert result[0].start_boundary == "sentence_end"
    assert result[0].end_boundary == "speaker_turn"


# Test 3 – duration_filter_excludes_too_short
def test_duration_filter_excludes_too_short() -> None:
    """Two frames 10 frames apart at 30fps (~0.33s) < 15s minimum."""
    result = generate_candidates(
        words=[],
        sentence_frames={0, 10},
        speaker_frames=set(),
        rate_num=30,
        rate_den=1,
        min_duration_s=15.0,
        max_duration_s=60.0,
    )
    assert result == []


# Test 4 – duration_filter_excludes_too_long
def test_duration_filter_excludes_too_long() -> None:
    """Two frames 2400 frames apart at 30fps (80s) > 60s maximum."""
    result = generate_candidates(
        words=[],
        sentence_frames={0, 2400},
        speaker_frames=set(),
        rate_num=30,
        rate_den=1,
        min_duration_s=15.0,
        max_duration_s=60.0,
    )
    assert result == []


# Test 5 – multiple_windows_from_one_start
def test_multiple_windows_from_one_start() -> None:
    """legal frames {0, 600, 1200, 1500} at 30fps:
    from start 0: 600 frames (20s OK), 1200 frames (40s OK), 1500 frames (50s OK).
    All three are within [15s, 60s] = [450, 1800] frames.
    From start 600: 1200 (600f=20s OK), 1500 (900f=30s OK).
    From start 1200: 1500 (300f=10s < 15s -> excluded).
    """
    legal_f = {0, 600, 1200, 1500}
    result = generate_candidates(
        words=[],
        sentence_frames=legal_f,
        speaker_frames=set(),
        rate_num=30,
        rate_den=1,
        min_duration_s=15.0,
        max_duration_s=60.0,
    )
    # All spans must be within [15, 60]s = [450, 1800] frames at 30fps
    for c in result:
        span_s = c.duration_frames / 30.0
        assert 15.0 <= span_s <= 60.0, f"Out-of-range span: {c}"

    # Must be sorted by (start_frame, end_frame_exclusive)
    pairs = [(c.start_frame, c.end_frame_exclusive) for c in result]
    assert pairs == sorted(pairs)

    # Check the specific expected candidates from start 0
    starts = [c for c in result if c.start_frame == 0]
    end_frames = sorted(c.end_frame_exclusive for c in starts)
    assert end_frames == [600, 1200, 1500]


# Test 6 – transcript_safe_by_construction
def test_transcript_safe_by_construction() -> None:
    """No boundary severs a word. We place words between boundaries to verify."""
    # Words that sit BETWEEN the boundary frames (not spanning any boundary)
    words = [
        Word(start_frame=110, end_frame=200),   # between boundary 100 and 1000
        Word(start_frame=500, end_frame=700),   # between boundary 100 and 1000
        Word(start_frame=800, end_frame=990),   # between boundary 100 and 1000
    ]
    result = generate_candidates(
        words=words,
        sentence_frames={100, 1000},
        speaker_frames=set(),
        rate_num=30,
        rate_den=1,
        min_duration_s=15.0,
        max_duration_s=60.0,
    )
    assert len(result) >= 1
    for c in result:
        assert _covering_word(c.start_frame, words) is None, (
            f"start_frame={c.start_frame} severs a word"
        )
        assert _covering_word(c.end_frame_exclusive, words) is None, (
            f"end_frame_exclusive={c.end_frame_exclusive} severs a word"
        )


# Test 7 – boundaries_only_on_legal_frames
def test_boundaries_only_on_legal_frames() -> None:
    sentence_frames = {0, 600, 1200}
    speaker_frames = {900}
    legal_set = sentence_frames | speaker_frames
    result = generate_candidates(
        words=[],
        sentence_frames=sentence_frames,
        speaker_frames=speaker_frames,
        rate_num=30,
        rate_den=1,
        min_duration_s=15.0,
        max_duration_s=60.0,
    )
    for c in result:
        assert c.start_frame in legal_set, f"start_frame={c.start_frame} not in legal set"
        assert c.end_frame_exclusive in legal_set, (
            f"end_frame_exclusive={c.end_frame_exclusive} not in legal set"
        )


# Test 8 – empty_when_fewer_than_two_legal_frames
def test_empty_when_fewer_than_two_legal_frames_one_sentence() -> None:
    result = generate_candidates(
        words=[],
        sentence_frames={500},
        speaker_frames=set(),
        rate_num=30,
        rate_den=1,
    )
    assert result == []


def test_empty_when_fewer_than_two_legal_frames_empty() -> None:
    result = generate_candidates(
        words=[],
        sentence_frames=set(),
        speaker_frames=set(),
        rate_num=30,
        rate_den=1,
    )
    assert result == []


# Test 9 – total_frames_clamp
def test_total_frames_clamp() -> None:
    """End-exclusive boundary at exactly total_frames is a LEGAL end boundary.

    The invariant (mirroring shorts_qa.py and Laura's editorial layer):
    - START frames must satisfy ``< total_frames``; a short cannot begin at or after
      the asset end.
    - END frames (end-exclusive) may equal ``total_frames``; that is the canonical legal
      way to close a short on the asset's last content frame.
    - Frames strictly BEYOND total_frames (> total_frames) are excluded for both roles.
    """
    # Case A: frame 1001 == total_frames=1001 -> legal END boundary, not a legal START.
    # Result: candidate [100, 1001) with 901 frames at 30fps ≈ 30s, inside [15, 60].
    result_end_at_total = generate_candidates(
        words=[],
        sentence_frames={100, 1001},
        speaker_frames=set(),
        rate_num=30,
        rate_den=1,
        total_frames=1001,
        min_duration_s=15.0,
        max_duration_s=60.0,
    )
    assert len(result_end_at_total) == 1, (
        "frame == total_frames must be a legal end boundary"
    )
    assert result_end_at_total[0].start_frame == 100
    assert result_end_at_total[0].end_frame_exclusive == 1001

    # Case B: frame 1002 > total_frames=1001 -> out of range for both roles, excluded.
    result_beyond = generate_candidates(
        words=[],
        sentence_frames={100, 1002},
        speaker_frames=set(),
        rate_num=30,
        rate_den=1,
        total_frames=1001,
        min_duration_s=15.0,
        max_duration_s=60.0,
    )
    # 1002 is beyond the asset — only {100} remains -> fewer than 2 legal frames -> []
    assert result_beyond == [], (
        "frame > total_frames must be excluded entirely"
    )

    # Case C: a START frame at total_frames is not eligible (cannot begin past asset end).
    # Set boundaries at {1001, 2001} with total_frames=1001: 1001 is not a legal start,
    # so no valid pair exists.
    result_start_at_total = generate_candidates(
        words=[],
        sentence_frames={1001, 2001},
        speaker_frames=set(),
        rate_num=30,
        rate_den=1,
        total_frames=1001,
        min_duration_s=15.0,
        max_duration_s=60.0,
    )
    assert result_start_at_total == [], (
        "frame == total_frames must NOT be a legal start boundary"
    )


# Test 10 – max_candidates_cap_is_deterministic
def test_max_candidates_cap_keeps_longest_per_start() -> None:
    """With max_candidates=1 per start, the longest in-range window is kept."""
    # legal frames {0, 600, 1200}: from start 0 -> ends [600(20s), 1200(40s)]
    # max_candidates=1 should keep the longest: end=1200 (40s)
    result = generate_candidates(
        words=[],
        sentence_frames={0, 600, 1200},
        speaker_frames=set(),
        rate_num=30,
        rate_den=1,
        min_duration_s=15.0,
        max_duration_s=60.0,
        max_candidates=1,
    )
    # One per start: start=0 -> longest end; start=600 -> end=1200
    start0 = [c for c in result if c.start_frame == 0]
    assert len(start0) == 1
    assert start0[0].end_frame_exclusive == 1200  # longest from start 0

    start600 = [c for c in result if c.start_frame == 600]
    assert len(start600) == 1
    assert start600[0].end_frame_exclusive == 1200  # only option from start 600


def test_max_candidates_cap_is_stable_across_calls() -> None:
    """Result order and content are stable (deterministic) across repeated calls."""
    kwargs = dict(
        words=[],
        sentence_frames={0, 600, 1200, 1500},
        speaker_frames=set(),
        rate_num=30,
        rate_den=1,
        min_duration_s=15.0,
        max_duration_s=60.0,
        max_candidates=1,
    )
    r1 = generate_candidates(**kwargs)  # type: ignore[arg-type]
    r2 = generate_candidates(**kwargs)  # type: ignore[arg-type]
    assert r1 == r2


# Test 12 – end_exclusive_and_positive_duration
def test_end_exclusive_and_positive_duration() -> None:
    """Every candidate has end_frame_exclusive > start_frame."""
    result = generate_candidates(
        words=[],
        sentence_frames={0, 600, 1200, 1800},
        speaker_frames=set(),
        rate_num=30,
        rate_den=1,
        min_duration_s=15.0,
        max_duration_s=60.0,
    )
    assert len(result) > 0
    for c in result:
        assert c.end_frame_exclusive > c.start_frame
        assert c.duration_frames == c.end_frame_exclusive - c.start_frame
        assert c.duration_frames > 0


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------


def test_defaults_used_when_not_specified() -> None:
    """DEFAULT_MIN_DURATION_S and DEFAULT_MAX_DURATION_S are 15.0 and 60.0."""
    assert DEFAULT_MIN_DURATION_S == 15.0
    assert DEFAULT_MAX_DURATION_S == 60.0


def test_generate_uses_defaults_when_not_specified() -> None:
    """generate_candidates uses DEFAULT_MIN/MAX when not passed."""
    # 30s span (900f at 30fps) is inside default [15, 60]s
    result = generate_candidates(
        words=[],
        sentence_frames={100, 1000},
        speaker_frames=set(),
        rate_num=30,
        rate_den=1,
    )
    assert len(result) == 1


def test_no_candidates_when_only_speaker_frames_too_long() -> None:
    """Speaker-turn frame 3000 from start 0: 100s at 30fps > 60s -> excluded."""
    result = generate_candidates(
        words=[],
        sentence_frames=set(),
        speaker_frames={0, 3000},
        rate_num=30,
        rate_den=1,
        min_duration_s=15.0,
        max_duration_s=60.0,
    )
    assert result == []


def test_mixed_boundary_kinds_sorted() -> None:
    """Result is sorted by (start_frame, end_frame_exclusive)."""
    result = generate_candidates(
        words=[],
        sentence_frames={0, 600, 1200},
        speaker_frames={900},
        rate_num=30,
        rate_den=1,
        min_duration_s=15.0,
        max_duration_s=60.0,
    )
    pairs = [(c.start_frame, c.end_frame_exclusive) for c in result]
    assert pairs == sorted(pairs)


def test_total_frames_none_no_clamp() -> None:
    """When total_frames is None no clamping occurs."""
    result = generate_candidates(
        words=[],
        sentence_frames={0, 1800},
        speaker_frames=set(),
        rate_num=30,
        rate_den=1,
        total_frames=None,
        min_duration_s=15.0,
        max_duration_s=60.0,
    )
    assert len(result) == 1
    assert result[0].end_frame_exclusive == 1800


def test_candidate_is_frozen_dataclass() -> None:
    """ShortCandidate must be frozen (immutable)."""
    c = ShortCandidate(
        start_frame=0,
        end_frame_exclusive=900,
        start_boundary="sentence_end",
        end_boundary="sentence_end",
    )
    with pytest.raises((AttributeError, TypeError)):
        c.start_frame = 1  # type: ignore[misc]
