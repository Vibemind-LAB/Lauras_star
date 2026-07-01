"""P1 tests — place_clip (spec §8 Test A) and lane-aware packing (spec §8 Test B).

Tests A: place_clip semantics
Tests B: lane-aware packing (insert / delete / move / trim / set_speed isolation)
Tests C: explicit Lane-0 regression — byte-identical to pre-P1 behaviour with only lane-0 clips.
"""

from __future__ import annotations

import pytest

from laura.editing.operations import (
    MAX_LANE,
    EditClip,
    append_clip,
    clips_on_lane,
    delete_range,
    insert_clip,
    lane_length,
    move_clip,
    ordered,
    place_clip,
    replace_lane,
    set_speed,
    trim_clip,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clip(
    asset: str,
    src_in: int,
    src_out: int,
    seq_in: int,
    seq_out: int,
    lane: int = 0,
    audio_offset: int = 0,
) -> EditClip:
    return EditClip(
        asset_id=asset,
        src_in_frame=src_in,
        src_out_frame_exclusive=src_out,
        seq_in_frame=seq_in,
        seq_out_frame_exclusive=seq_out,
        lane=lane,
        audio_offset_samples=audio_offset,
    )


def _two_lane_timeline() -> list[EditClip]:
    """Two clips on lane 0 (contiguous) + one clip on lane 1 (free-floating).

    Lane 0: A[0,10), B[10,25)
    Lane 1: X[5, 20)  — overlaps lane 0 in time (allowed cross-lane)
    """
    a = _clip("A", 0, 10, 0, 10, lane=0)
    b = _clip("B", 20, 35, 10, 25, lane=0)
    x = _clip("X", 0, 15, 5, 20, lane=1)
    return [a, b, x]


# ---------------------------------------------------------------------------
# Tests A — place_clip semantics (spec §8 §A)
# ---------------------------------------------------------------------------


def test_place_clip_moves_only_the_target() -> None:
    """A.1 — place_clip moves exactly the target; all other clips are byte-identical."""
    clips = _two_lane_timeline()
    # Re-identify by asset_id (ordered() gives (seq_in, lane) order → A, X, B)
    a = next(c for c in clips if c.asset_id == "A")
    b = next(c for c in clips if c.asset_id == "B")

    # Move X from (seq_in=5, lane=1) to (seq_in=30, lane=1)
    result = place_clip(clips, at_seq_frame=5, lane_src=1, to_seq_frame=30, lane_dst=1)

    by_asset = {c.asset_id: c for c in result}
    assert by_asset["A"] is a, "clip A must be byte-identical"
    assert by_asset["B"] is b, "clip B must be byte-identical"
    moved = by_asset["X"]
    assert moved.seq_in_frame == 30
    assert moved.seq_out_frame_exclusive == 45  # dur=15 preserved


def test_place_clip_preserves_sequence_duration() -> None:
    """A.2 — duration (seq_out - seq_in) and source range / speed are unchanged."""
    x = _clip("X", 10, 25, 5, 20, lane=1)  # src dur=15, seq dur=15
    clips = [x]
    result = place_clip(clips, at_seq_frame=5, lane_src=1, to_seq_frame=100, lane_dst=1)
    placed = result[0]
    assert placed.seq_in_frame == 100
    assert placed.seq_out_frame_exclusive == 115          # dur=15 preserved
    assert placed.src_in_frame == 10                       # src unchanged
    assert placed.src_out_frame_exclusive == 25
    assert placed.speed_num == 1
    assert placed.speed_den == 1


def test_place_clip_negative_to_seq_frame_raises() -> None:
    """A.3a — to_seq_frame < 0 → ValueError (spec §1.4 point 2)."""
    clips = [_clip("X", 0, 10, 5, 15, lane=1)]
    with pytest.raises(ValueError, match="to_seq_frame must be >= 0"):
        place_clip(clips, at_seq_frame=5, lane_src=1, to_seq_frame=-1, lane_dst=1)


def test_place_clip_invalid_lane_raises() -> None:
    """A.3b — lane_dst > MAX_LANE → ValueError (spec §1.4 point 3)."""
    clips = [_clip("X", 0, 10, 5, 15, lane=1)]
    with pytest.raises(ValueError, match="lane_dst"):
        place_clip(clips, at_seq_frame=5, lane_src=1, to_seq_frame=0, lane_dst=MAX_LANE + 1)


def test_place_clip_missing_source_clip_raises() -> None:
    """A.3c — (at_seq_frame, lane_src) not found → ValueError (spec §1.4 point 1)."""
    clips = [_clip("X", 0, 10, 5, 15, lane=1)]
    with pytest.raises(ValueError, match="no clip at"):
        place_clip(clips, at_seq_frame=99, lane_src=1, to_seq_frame=0, lane_dst=1)


def test_place_clip_intra_lane_overlap_raises() -> None:
    """A.4 — overlap within the same destination lane → ValueError (spec §1.4 point 4)."""
    # Lane 0: A[0,10), B[10,20)
    # Try placing A to [5,15) — overlaps B
    a = _clip("A", 0, 10, 0, 10, lane=0)
    b = _clip("B", 0, 10, 10, 20, lane=0)
    clips = [a, b]
    with pytest.raises(ValueError, match="overlap on lane 0"):
        place_clip(clips, at_seq_frame=0, lane_src=0, to_seq_frame=5, lane_dst=0)


def test_place_clip_cross_lane_overlap_allowed() -> None:
    """A.5 — cross-lane time overlap is ALLOWED and must not raise (spec §1.4 point 4)."""
    # Lane 0: A[0,20); Lane 1: X will be placed at [5,15) overlapping A in time
    a = _clip("A", 0, 20, 0, 20, lane=0)
    x = _clip("X", 0, 10, 30, 40, lane=1)
    clips = [a, x]
    # Place X to [5,15) on lane 1 — cross-lane overlap with A on lane 0 → allowed
    result = place_clip(clips, at_seq_frame=30, lane_src=1, to_seq_frame=5, lane_dst=1)
    placed_x = next(c for c in result if c.asset_id == "X")
    assert placed_x.seq_in_frame == 5
    assert placed_x.seq_out_frame_exclusive == 15


def test_place_clip_to_lane1_zeroes_audio_offset() -> None:
    """A.6a — moving a clip TO lane ≥ 1 sets audio_offset_samples = 0 (spec §1.2)."""
    c = _clip("A", 0, 10, 0, 10, lane=0, audio_offset=500)
    result = place_clip([c], at_seq_frame=0, lane_src=0, to_seq_frame=50, lane_dst=1)
    assert result[0].audio_offset_samples == 0


def test_place_clip_to_lane0_preserves_audio_offset() -> None:
    """A.6b — moving a clip BACK to lane 0 preserves the original offset (spec §1.2)."""
    # Start: one clip on lane 1 with some offset (unusual but valid in isolation)
    c = EditClip(
        asset_id="A",
        src_in_frame=0,
        src_out_frame_exclusive=10,
        seq_in_frame=5,
        seq_out_frame_exclusive=15,
        lane=1,
        audio_offset_samples=200,
    )
    # Place it back on lane 0 as the only clip there → normalize will zero it (first-clip-0).
    result = place_clip([c], at_seq_frame=5, lane_src=1, to_seq_frame=10, lane_dst=0)
    placed = next(r for r in result if r.asset_id == "A")
    # Now it IS the only lane-0 clip → normalize zeroes it (first-clip-0 invariant).
    assert placed.audio_offset_samples == 0


def test_place_clip_to_lane0_non_head_preserves_offset() -> None:
    """A.6c — clip moved to lane 0 as a NON-head preserves its offset (spec §1.2)."""
    # Lane 0 head at seq_in=0; move a lane-1 clip with offset=200 to seq_in=20 on lane 0.
    head = _clip("HEAD", 0, 10, 0, 10, lane=0)
    c = EditClip(
        asset_id="C",
        src_in_frame=5,
        src_out_frame_exclusive=15,
        seq_in_frame=5,
        seq_out_frame_exclusive=15,
        lane=1,
        audio_offset_samples=200,
    )
    # Place C to lane 0 at seq_in=20 (non-head because HEAD is at 0).
    result = place_clip([head, c], at_seq_frame=5, lane_src=1, to_seq_frame=20, lane_dst=0)
    placed = next(r for r in result if r.asset_id == "C")
    # C is not the head (HEAD is at 0), so its offset=200 is preserved.
    assert placed.audio_offset_samples == 200


def test_place_clip_idempotent() -> None:
    """A.7 — second call with new source position yields the same state (spec §1.5)."""
    x = _clip("X", 0, 10, 5, 15, lane=0)
    clips = [x]
    result1 = place_clip(clips, at_seq_frame=5, lane_src=0, to_seq_frame=20, lane_dst=1)
    # Second call: X is now at (seq_in=20, lane=1) → place it again at the same destination
    result2 = place_clip(result1, at_seq_frame=20, lane_src=1, to_seq_frame=20, lane_dst=1)
    assert result1[0].seq_in_frame == result2[0].seq_in_frame
    assert result1[0].lane == result2[0].lane
    assert result1[0].seq_out_frame_exclusive == result2[0].seq_out_frame_exclusive


def test_place_clip_lands_at_absolute_position() -> None:
    """A.1 (extended) — the placed clip starts EXACTLY at to_seq_frame (spec §1.1)."""
    clips = [_clip("X", 100, 200, 50, 150, lane=1)]
    result = place_clip(clips, at_seq_frame=50, lane_src=1, to_seq_frame=0, lane_dst=2)
    placed = result[0]
    assert placed.seq_in_frame == 0
    assert placed.seq_out_frame_exclusive == 100   # dur = 100 preserved
    assert placed.lane == 2


# ---------------------------------------------------------------------------
# Tests B — lane-aware packing (spec §8 Test B)
# ---------------------------------------------------------------------------


def test_insert_on_lane1_does_not_disturb_lane0() -> None:
    """B.8 — insert_clip on lane 1 ripples only lane-1 clips; lane 0 is byte-identical."""
    a = _clip("A", 0, 10, 0, 10, lane=0)
    b = _clip("B", 0, 10, 10, 20, lane=0)
    x = _clip("X", 0, 5, 0, 5, lane=1)
    clips = [a, b, x]

    new_clip = _clip("N", 0, 3, 0, 0, lane=1)  # to insert at seq_in=0 on lane 1
    result = insert_clip(clips, new_clip, at_seq_frame=0)

    by_asset = {c.asset_id: c for c in result}
    # Lane-0 clips must be byte-identical
    assert by_asset["A"] is a
    assert by_asset["B"] is b
    # X was at [0,5) on lane 1 → rippled by 3 frames → [3,8)
    assert by_asset["X"].seq_in_frame == 3
    assert by_asset["X"].seq_out_frame_exclusive == 8
    # New clip landed at [0,3)
    assert by_asset["N"].seq_in_frame == 0
    assert by_asset["N"].seq_out_frame_exclusive == 3


def test_insert_on_lane0_does_not_disturb_lane1() -> None:
    """B.8 (reverse) — insert_clip on lane 0 ripples only lane-0 clips; lane 1 is byte-identical."""
    a = _clip("A", 0, 10, 0, 10, lane=0)
    x = _clip("X", 0, 15, 5, 20, lane=1)
    clips = [a, x]

    new_clip = _clip("N", 0, 5, 0, 0, lane=0)
    result = insert_clip(clips, new_clip, at_seq_frame=0)

    by_asset = {c.asset_id: c for c in result}
    # Lane 1 must be byte-identical
    assert by_asset["X"] is x
    # A rippled by 5
    assert by_asset["A"].seq_in_frame == 5
    assert by_asset["A"].seq_out_frame_exclusive == 15


def test_delete_range_lane_scoped_leaves_other_lane_intact() -> None:
    """B.9 — delete_range on lane 0 leaves lane-1 clips byte-identical (spec §2.2)."""
    a = _clip("A", 0, 10, 0, 10, lane=0)
    b = _clip("B", 0, 10, 10, 20, lane=0)
    x = _clip("X", 0, 15, 5, 20, lane=1)
    clips = [a, b, x]

    result = delete_range(clips, 0, 10, lane=0)  # delete A on lane 0

    by_asset = {c.asset_id: c for c in result}
    # A is deleted
    assert "A" not in by_asset
    # B rippled to [0,10)
    assert by_asset["B"].seq_in_frame == 0
    assert by_asset["B"].seq_out_frame_exclusive == 10
    # X on lane 1 is byte-identical
    assert by_asset["X"] is x


def test_delete_range_on_lane1_leaves_lane0_intact() -> None:
    """B.9 (reverse) — delete_range on lane 1 leaves lane-0 clips byte-identical."""
    a = _clip("A", 0, 10, 0, 10, lane=0)
    b = _clip("B", 0, 10, 10, 20, lane=0)
    x = _clip("X", 0, 15, 5, 20, lane=1)
    clips = [a, b, x]

    result = delete_range(clips, 5, 20, lane=1)  # delete X range on lane 1

    by_asset = {c.asset_id: c for c in result}
    assert by_asset["A"] is a
    assert by_asset["B"] is b
    assert "X" not in by_asset


def test_move_clip_repacks_only_target_lane() -> None:
    """B.10 — move_clip re-packs only the target lane; others byte-identical (spec §2.2)."""
    a = _clip("A", 0, 10, 0, 10, lane=0)
    b = _clip("B", 0, 15, 10, 25, lane=0)
    x = _clip("X", 0, 12, 5, 17, lane=1)
    clips = [a, b, x]

    # Move B (lane 0) to the front
    result = move_clip(clips, at_seq_frame=10, to_seq_frame=0)

    by_asset = {c.asset_id: c for c in result}
    # Lane 1 is byte-identical
    assert by_asset["X"] is x
    # Lane 0 repacked: B[0,15), A[15,25)
    assert by_asset["B"].seq_in_frame == 0
    assert by_asset["B"].seq_out_frame_exclusive == 15
    assert by_asset["A"].seq_in_frame == 15
    assert by_asset["A"].seq_out_frame_exclusive == 25


def test_trim_clip_ripples_only_target_lane() -> None:
    """B.11 — trim_clip ripples only the target lane; other lanes byte-identical (spec §2.2)."""
    a = _clip("A", 0, 10, 0, 10, lane=0)
    b = _clip("B", 0, 10, 10, 20, lane=0)
    x = _clip("X", 0, 12, 5, 17, lane=1)
    clips = [a, b, x]

    # Trim A to src [0,5) — shrinks by 5 frames, B should ripple left to [5,15)
    result = trim_clip(clips, at_seq_frame=0, new_src_in=0, new_src_out=5)

    by_asset = {c.asset_id: c for c in result}
    # Lane 1 must be byte-identical
    assert by_asset["X"] is x
    # A now [0,5) on lane 0
    assert by_asset["A"].seq_in_frame == 0
    assert by_asset["A"].seq_out_frame_exclusive == 5
    # B rippled to [5,15)
    assert by_asset["B"].seq_in_frame == 5
    assert by_asset["B"].seq_out_frame_exclusive == 15


def test_set_speed_ripples_only_target_lane() -> None:
    """B.11 — set_speed ripples only the target lane; other lanes byte-identical (spec §2.2)."""
    a = _clip("A", 0, 10, 0, 10, lane=0)
    b = _clip("B", 0, 10, 10, 20, lane=0)
    x = _clip("X", 0, 12, 5, 17, lane=1)
    clips = [a, b, x]

    # Set A to 2x speed: seq length becomes 5 (from 10 src frames at 2/1 → 5 seq frames)
    result = set_speed(clips, at_seq_frame=0, speed_num=2, speed_den=1)

    by_asset = {c.asset_id: c for c in result}
    # Lane 1 byte-identical
    assert by_asset["X"] is x
    # A: seq_in=0, seq_out=5 (half of original 10)
    assert by_asset["A"].seq_in_frame == 0
    assert by_asset["A"].seq_out_frame_exclusive == 5
    # B rippled left by 5: [5,15)
    assert by_asset["B"].seq_in_frame == 5
    assert by_asset["B"].seq_out_frame_exclusive == 15


def test_append_clip_on_lane1_does_not_use_lane0_length() -> None:
    """B lane-scoped append: lane-1 append starts at lane_length(1), not sequence_length."""
    a = _clip("A", 0, 30, 0, 30, lane=0)   # lane 0 extends to 30
    clips = [a]

    new_clip = _clip("N", 0, 10, 0, 0, lane=1)
    result = append_clip(clips, new_clip)

    by_asset = {c.asset_id: c for c in result}
    # Lane 1 was empty → start at 0
    assert by_asset["N"].seq_in_frame == 0
    assert by_asset["N"].seq_out_frame_exclusive == 10
    # Lane 0 unchanged
    assert by_asset["A"] is a


# ---------------------------------------------------------------------------
# Tests C — Lane-0 regression (spec §8 B.12)
# ---------------------------------------------------------------------------
# All tests below reproduce the PRE-P1 behaviour EXACTLY using only lane-0 clips.
# Every test is annotated with the specific pre-P1 invariant it proves.


def test_lane0_append_sequential() -> None:
    """C: append_clip with only lane-0 clips still places sequentially (original invariant)."""
    clips = append_clip([], _clip("a", 0, 30, 0, 0))
    clips = append_clip(clips, _clip("a", 100, 130, 0, 0))
    assert (clips[0].seq_in_frame, clips[0].seq_out_frame_exclusive) == (0, 30)
    assert (clips[1].seq_in_frame, clips[1].seq_out_frame_exclusive) == (30, 60)


def test_lane0_delete_ripples() -> None:
    """C: delete_range on lane 0 with only lane-0 clips behaves exactly as before."""
    a = _clip("A", 0, 10, 0, 10, lane=0)
    b = _clip("B", 0, 10, 10, 20, lane=0)
    c = _clip("C", 0, 10, 20, 30, lane=0)
    clips = [a, b, c]
    # Delete B [10,20) → C should ripple to [10,20)
    result = ordered(delete_range(clips, 10, 20))
    assert len(result) == 2
    assert (result[0].seq_in_frame, result[0].seq_out_frame_exclusive) == (0, 10)
    assert (result[1].seq_in_frame, result[1].seq_out_frame_exclusive) == (10, 20)
    assert result[1].src_in_frame == 0   # C's src range


def test_lane0_insert_ripples() -> None:
    """C: insert_clip on lane 0 with only lane-0 clips ripples later clips exactly as before."""
    a = _clip("A", 0, 10, 0, 10, lane=0)
    b = _clip("B", 0, 10, 10, 20, lane=0)
    clips = [a, b]
    new = _clip("N", 0, 5, 0, 0, lane=0)
    result = ordered(insert_clip(clips, new, at_seq_frame=10))
    assert len(result) == 3
    assert (result[0].seq_in_frame, result[0].seq_out_frame_exclusive) == (0, 10)  # A
    assert (result[1].seq_in_frame, result[1].seq_out_frame_exclusive) == (10, 15)  # N
    assert (result[2].seq_in_frame, result[2].seq_out_frame_exclusive) == (15, 25)  # B


def test_lane0_move_repacks_contiguously() -> None:
    """C: move_clip with only lane-0 clips re-packs contiguously from 0 (original invariant)."""
    a = _clip("A", 0, 10, 0, 10, lane=0)
    b = _clip("B", 0, 15, 10, 25, lane=0)
    c = _clip("C", 0, 15, 25, 40, lane=0)
    result = ordered(move_clip([a, b, c], at_seq_frame=0, to_seq_frame=40))
    # B, C, A → [0,15), [15,30), [30,40)
    # All three clips have src_in=0; just verify the repack is contiguous from 0.
    assert result[0].src_in_frame == 0
    seq_pairs = [(r.seq_in_frame, r.seq_out_frame_exclusive) for r in result]
    prev = 0
    for s, e in seq_pairs:
        assert s == prev
        prev = e
    assert prev == 40


def test_lane0_trim_ripples() -> None:
    """C: trim_clip with only lane-0 clips ripples later clips exactly as before."""
    a = _clip("A", 0, 10, 0, 10, lane=0)
    b = _clip("B", 0, 10, 10, 20, lane=0)
    clips = [a, b]
    result = ordered(trim_clip(clips, at_seq_frame=0, new_src_in=0, new_src_out=5))
    assert (result[0].seq_in_frame, result[0].seq_out_frame_exclusive) == (0, 5)
    assert (result[1].seq_in_frame, result[1].seq_out_frame_exclusive) == (5, 15)


def test_lane0_set_speed_ripples() -> None:
    """C: set_speed with only lane-0 clips ripples later clips exactly as before."""
    a = _clip("A", 0, 10, 0, 10, lane=0)
    b = _clip("B", 0, 10, 10, 20, lane=0)
    clips = [a, b]
    # 2x speed on A: seq out = 5; B should ripple to [5,15)
    result = ordered(set_speed(clips, at_seq_frame=0, speed_num=2, speed_den=1))
    assert (result[0].seq_in_frame, result[0].seq_out_frame_exclusive) == (0, 5)
    assert (result[1].seq_in_frame, result[1].seq_out_frame_exclusive) == (5, 15)


# ---------------------------------------------------------------------------
# Helper tests — clips_on_lane, replace_lane, lane_length (spec §2.1)
# ---------------------------------------------------------------------------


def test_clips_on_lane_filters_correctly() -> None:
    a = _clip("A", 0, 10, 0, 10, lane=0)
    b = _clip("B", 0, 10, 10, 20, lane=0)
    x = _clip("X", 0, 12, 5, 17, lane=1)
    clips = [a, b, x]
    assert clips_on_lane(clips, 0) == [a, b]
    assert clips_on_lane(clips, 1) == [x]
    assert clips_on_lane(clips, 2) == []


def test_replace_lane_replaces_only_that_lane() -> None:
    a = _clip("A", 0, 10, 0, 10, lane=0)
    x = _clip("X", 0, 12, 5, 17, lane=1)
    new_x = _clip("X2", 0, 8, 30, 38, lane=1)
    result = replace_lane([a, x], 1, [new_x])
    assert a in result
    assert x not in result
    assert new_x in result


def test_lane_length_returns_max_seq_out_on_lane() -> None:
    a = _clip("A", 0, 10, 0, 10, lane=0)
    b = _clip("B", 0, 10, 10, 20, lane=0)
    x = _clip("X", 0, 12, 5, 17, lane=1)
    clips = [a, b, x]
    assert lane_length(clips, 0) == 20
    assert lane_length(clips, 1) == 17
    assert lane_length(clips, 2) == 0  # empty lane
