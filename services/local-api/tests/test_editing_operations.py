"""Golden tests for rough-cut operations — deterministic timeline deltas."""

from __future__ import annotations

from laura.editing.operations import (
    EditClip,
    append_clip,
    delete_range,
    insert_clip,
    lift_range,
    ordered,
)


def _clip(asset: str, s_in: int, s_out: int) -> EditClip:
    return EditClip(asset, s_in, s_out, 0, 0)


def _three_back_to_back() -> list[EditClip]:
    clips: list[EditClip] = []
    for i in range(3):
        clips = append_clip(clips, _clip("a", i * 100, i * 100 + 10))
    return clips  # seq 0..10,10..20,20..30 ; src 0..10,100..110,200..210


def test_append_places_sequentially() -> None:
    clips = append_clip([], _clip("a", 0, 30))
    clips = append_clip(clips, _clip("a", 100, 130))
    assert (clips[0].seq_in_frame, clips[0].seq_out_frame_exclusive) == (0, 30)
    assert (clips[1].seq_in_frame, clips[1].seq_out_frame_exclusive) == (30, 60)
    assert (clips[1].src_in_frame, clips[1].src_out_frame_exclusive) == (100, 130)


def test_delete_ripples_closes_gap() -> None:
    out = ordered(delete_range(_three_back_to_back(), 10, 20))
    assert len(out) == 2
    assert (out[0].seq_in_frame, out[0].seq_out_frame_exclusive) == (0, 10)
    assert (out[1].seq_in_frame, out[1].seq_out_frame_exclusive) == (10, 20)
    assert (out[1].src_in_frame, out[1].src_out_frame_exclusive) == (200, 210)


def test_lift_leaves_gap() -> None:
    out = ordered(lift_range(_three_back_to_back(), 10, 20))
    assert len(out) == 2
    assert (out[0].seq_in_frame, out[0].seq_out_frame_exclusive) == (0, 10)
    assert (out[1].seq_in_frame, out[1].seq_out_frame_exclusive) == (20, 30)


def test_delete_within_clip_trims_source() -> None:
    clips = append_clip([], _clip("a", 0, 30))  # seq 0..30, src 0..30
    out = ordered(delete_range(clips, 10, 20))
    assert len(out) == 2
    assert (out[0].seq_in_frame, out[0].seq_out_frame_exclusive) == (0, 10)
    assert (out[0].src_in_frame, out[0].src_out_frame_exclusive) == (0, 10)
    assert (out[1].seq_in_frame, out[1].seq_out_frame_exclusive) == (10, 20)
    assert (out[1].src_in_frame, out[1].src_out_frame_exclusive) == (20, 30)


def test_insert_ripples_right() -> None:
    clips: list[EditClip] = []
    for i in range(2):
        clips = append_clip(clips, _clip("a", i * 100, i * 100 + 10))  # 0..10, 10..20
    out = ordered(insert_clip(clips, _clip("b", 0, 5), 10))
    assert len(out) == 3
    assert (out[0].seq_in_frame, out[0].seq_out_frame_exclusive) == (0, 10)
    assert (out[1].asset_id, out[1].seq_in_frame, out[1].seq_out_frame_exclusive) == ("b", 10, 15)
    assert (out[2].seq_in_frame, out[2].seq_out_frame_exclusive) == (15, 25)


def test_editclip_row_roundtrip() -> None:
    clip = EditClip("a", 1, 2, 3, 4, lane=1, speaker_id="s",
                    origin_word_start_id="w1", origin_word_end_id="w2")
    assert EditClip.from_row(clip.to_row()) == clip
