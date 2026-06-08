# services/local-api/tests/test_scene_grouping.py
from __future__ import annotations

from laura.scenes.grouping import group_into_scenes


def _clip(seq_in: int, seq_out: int) -> dict:
    return {"seq_in_frame": seq_in, "seq_out_frame_exclusive": seq_out}


def _w(start: int, end: int, speaker: str | None) -> dict:
    return {"start_frame": start, "end_frame": end, "speaker": speaker}


def test_empty_clips_returns_empty() -> None:
    assert group_into_scenes([], [], gap_frames=45) == []


def test_single_clip_one_scene() -> None:
    assert group_into_scenes([_clip(0, 30)], [[_w(0, 10, "A")]], gap_frames=45) == [(0, 30)]


def test_speaker_change_breaks() -> None:
    clips = [_clip(0, 30), _clip(30, 60)]
    words = [[_w(0, 10, "A")], [_w(31, 40, "B")]]
    assert group_into_scenes(clips, words, gap_frames=1000) == [(0, 30), (30, 60)]


def test_small_gap_same_speaker_keeps_together() -> None:
    clips = [_clip(0, 30), _clip(30, 60)]
    words = [[_w(0, 28, "A")], [_w(31, 50, "A")]]  # gap = 3 < 45
    assert group_into_scenes(clips, words, gap_frames=45) == [(0, 60)]


def test_large_gap_same_speaker_breaks() -> None:
    clips = [_clip(0, 30), _clip(30, 60)]
    words = [[_w(0, 10, "A")], [_w(58, 60, "A")]]  # gap = 48 >= 45
    assert group_into_scenes(clips, words, gap_frames=45) == [(0, 30), (30, 60)]


def test_no_transcript_anywhere_one_scene_per_clip() -> None:
    clips = [_clip(0, 30), _clip(30, 60), _clip(60, 90)]
    words = [[], [], []]
    assert group_into_scenes(clips, words, gap_frames=45) == [(0, 30), (30, 60), (60, 90)]


def test_partial_empty_clip_keeps_together() -> None:
    clips = [_clip(0, 30), _clip(30, 60)]
    words = [[_w(0, 10, "A")], []]  # one side has words -> no break from emptiness
    assert group_into_scenes(clips, words, gap_frames=45) == [(0, 60)]
