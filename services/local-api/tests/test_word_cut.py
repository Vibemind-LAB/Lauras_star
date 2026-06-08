from __future__ import annotations

from laura.editing.operations import EditClip
from laura.editing.word_cut import map_asset_range_to_seq


def _clip(asset: str, src_in: int, src_out: int, seq_in: int) -> EditClip:
    return EditClip(
        asset_id=asset,
        src_in_frame=src_in,
        src_out_frame_exclusive=src_out,
        seq_in_frame=seq_in,
        seq_out_frame_exclusive=seq_in + (src_out - src_in),
    )


def test_maps_word_range_within_single_clip() -> None:
    clips = [_clip("a", 100, 200, 0)]  # asset 100..200 -> seq 0..100
    assert map_asset_range_to_seq(clips, asset_id="a", src_lo=120, src_hi=140) == (20, 40)


def test_returns_none_when_asset_absent() -> None:
    clips = [_clip("a", 100, 200, 0)]
    assert map_asset_range_to_seq(clips, asset_id="b", src_lo=120, src_hi=140) is None


def test_spans_two_adjacent_clips() -> None:
    clips = [_clip("a", 100, 150, 0), _clip("a", 150, 200, 50)]  # seq 0..50, 50..100
    assert map_asset_range_to_seq(clips, asset_id="a", src_lo=140, src_hi=160) == (40, 60)
