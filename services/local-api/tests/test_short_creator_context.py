"""Describer + Transcript-Analyst tools (Iteration 4b).

``transcript_window`` (real, over ``get_latest_analysis_run`` + ``get_transcript``) and
``describe_moment`` (injectable VLM backend + frame extractor, graceful when no
model). The window-filter is pure and tested exhaustively; the VLM describe path
is exercised with a fake backend so no Ollama/ffmpeg is needed.
"""

from __future__ import annotations

from typing import Any

import pytest

from laura.db.database import Database
from laura.short_creator import context, describe

# --- _segments_in_window: pure overlap filter -----------------------------------------------


def _seg(start: int, end: int, text: str) -> dict[str, Any]:
    return {"start_frame": start, "end_frame": end, "text": text}


def test_segments_in_window_selects_overlapping() -> None:
    # center=100, window=50 -> window is [50, 150].
    segs = [
        _seg(0, 30, "before"),  # ends below the window low -> excluded
        _seg(90, 110, "overlap lo"),  # included
        _seg(100, 120, "on center"),  # included
        _seg(140, 160, "overlap hi"),  # starts at 140 <= 150 -> included
        _seg(200, 220, "after"),  # starts above the window high -> excluded
    ]
    got = context._segments_in_window(segs, center_frame=100, window_frames=50)
    assert [s["text"] for s in got] == ["overlap lo", "on center", "overlap hi"]


def test_segments_in_window_excludes_outside() -> None:
    segs = [_seg(0, 10, "a"), _seg(1000, 1010, "b")]
    got = context._segments_in_window(segs, center_frame=500, window_frames=100)
    assert got == []


def test_segments_in_window_end_frame_is_exclusive_at_boundary() -> None:
    # center=100, window=50 -> lo=50. A segment with end_frame == lo covers only up to 49,
    # so it is OUTSIDE the window (end_frame is end-exclusive).
    assert context._segments_in_window([_seg(0, 50, "ends at lo")], 100, 50) == []
    # ...but end_frame == lo + 1 covers frame 50 -> included.
    assert context._segments_in_window([_seg(0, 51, "reaches lo")], 100, 50)[0]["text"] == (
        "reaches lo"
    )


def test_segments_in_window_skips_rows_without_frames() -> None:
    segs: list[dict[str, Any]] = [{"text": "no frames"}, _seg(100, 110, "ok")]
    got = context._segments_in_window(segs, center_frame=105, window_frames=50)
    assert [s["text"] for s in got] == ["ok"]


def test_frame_rate_prefers_asset_rate_then_project_sequence_rate(db: Database) -> None:
    from laura.db import repos

    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db,
        project_id=str(project["id"]),
        type="video",
        display_name="v",
        source_path="/tmp/v.mp4",
    )
    # Freshly created asset has no probed rate -> falls back to the project's SEQUENCE rate
    # (projects store sequence_rate_num, not rate_num — live-run finding).
    assert context._frame_rate(db, asset) == (30, 1)
    # A probed asset rate wins.
    assert context._frame_rate(db, {**asset, "rate_num": 25, "rate_den": 1}) == (25, 1)


def test_group_segments_into_blocks_summarizable_chunks() -> None:
    segs = [_seg(i * 100, (i + 1) * 100, f"satz {i}") for i in range(8)]
    blocks = context._group_segments_into_blocks(segs, blocks=4)
    assert len(blocks) == 4
    assert blocks[0]["start_frame"] == 0 and blocks[0]["end_frame"] == 200
    assert blocks[0]["text"] == "satz 0 satz 1"
    assert blocks[-1]["text"] == "satz 6 satz 7"


def test_group_segments_into_blocks_fewer_segments_than_blocks() -> None:
    segs = [_seg(0, 10, "a"), _seg(10, 20, "b")]
    blocks = context._group_segments_into_blocks(segs, blocks=8)
    assert len(blocks) == 2  # never emits empty blocks


def test_transcript_overview_no_run_is_graceful(db: Database) -> None:
    out = context.transcript_overview(db, "no-such-asset")
    assert out["ok"] is False
    assert out["blocks"] == []


def test_transcript_window_no_run_is_graceful(db: Database) -> None:
    out = context.transcript_window(db, "no-such-asset", 100)
    assert out["ok"] is False
    assert out["segments"] == []
    assert out["text"] == ""


# --- scene relevance: transcript per scene + topic ranking (Slice 1) -------------------------


def _clip(seq_in: int, seq_out: int, src_in: int, src_out: int) -> dict[str, Any]:
    return {
        "lane": 0,
        "seq_in_frame": seq_in,
        "seq_out_frame_exclusive": seq_out,
        "src_in_frame": src_in,
        "src_out_frame_exclusive": src_out,
    }


def test_scene_src_ranges_maps_seq_to_source_across_clips() -> None:
    # Rough cut: clip A seq[0,100) = src[50,150); clip B seq[100,200) = src[300,400).
    clips = [_clip(0, 100, 50, 150), _clip(100, 200, 300, 400)]
    # Scene spans seq[80,120): 20 frames from A's tail + 20 from B's head.
    ranges = context._scene_src_ranges(clips, seq_in=80, seq_out_exclusive=120)
    assert ranges == [(130, 150), (300, 320)]


def test_segments_in_ranges_end_exclusive_overlap() -> None:
    segs = [
        _seg(100, 130, "ends at lo"),  # end-exclusive: touches [130,150) but covers only ..129
        _seg(100, 140, "in a"),  # overlaps [130,150)
        _seg(150, 160, "between"),  # starts AT a's exclusive end -> outside both
        _seg(305, 330, "in b"),
        _seg(400, 420, "after"),  # starts AT b's exclusive end -> outside
    ]
    got = context._segments_in_ranges(segs, [(130, 150), (300, 400)])
    assert [s["text"] for s in got] == ["in a", "in b"]


def test_rank_texts_by_topic_overlap() -> None:
    texts = [
        (1, "wir zeigen die agenten und das dashboard"),
        (2, "hier geht es um business daten und agenten"),
        (3, "intro musik ohne inhalt"),
    ]
    ranked = context._rank_texts("Agenten für Business Daten", texts)
    assert [n for n, _score, _snip in ranked[:2]] == [2, 1]
    assert ranked[0][1] > ranked[1][1] > 0.0


def test_rank_scenes_by_topic_graceful_without_scenes(db: Database) -> None:
    from laura.db import repos

    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db,
        project_id=str(project["id"]),
        type="video",
        display_name="v",
        source_path="/tmp/v.mp4",
    )
    out = context.rank_scenes_by_topic(db, str(asset["id"]), "irgendwas")
    assert out["ok"] is False


# --- voice alignment: cuts must not clip words ----------------------------------------------


def _word(start: int, end: int, text: str) -> dict[str, Any]:
    return {"start_frame": start, "end_frame": end, "text": text}


def test_voice_alignment_clean_cut_is_aligned() -> None:
    words = [_word(100, 120, "hallo"), _word(125, 160, "welt"), _word(300, 320, "danach")]
    out = context._voice_alignment(words, start_frame=90, end_frame_exclusive=200)
    assert out["aligned"] is True
    assert out["clipped_words"] == []
    assert out["lead_in_frames"] == 10  # 100 - 90
    assert out["tail_frames"] == 40  # 200 - 160


def test_voice_alignment_detects_clipped_words_at_both_cuts() -> None:
    words = [
        _word(80, 110, "anfang"),  # spans the start cut (100)
        _word(120, 150, "mitte"),
        _word(190, 230, "ende"),  # spans the end cut (200)
    ]
    out = context._voice_alignment(words, start_frame=100, end_frame_exclusive=200)
    assert out["aligned"] is False
    assert out["clipped_words"] == ["anfang", "ende"]


def test_check_voice_alignment_unknown_candidate_graceful(db: Database) -> None:
    out = context.check_voice_alignment(db, "no-such-candidate")
    assert out["ok"] is False


# --- describe_moment: injectable, graceful --------------------------------------------------


class _FakeBackend:
    def __init__(self, available: bool, text: str) -> None:
        self._available = available
        self._text = text

    def available(self) -> bool:
        return self._available

    def describe(self, frames: list[bytes], prompt: str) -> str:
        return self._text


def test_describe_moment_no_backend_is_graceful(db: Database) -> None:
    out = context.describe_moment(db, "asset", 10, backend=_FakeBackend(False, ""))
    assert out["ok"] is False
    assert out["description"] == ""


def test_describe_moment_no_frame_is_graceful(db: Database) -> None:
    out = context.describe_moment(
        db, "asset", 10, backend=_FakeBackend(True, "x"), extract=lambda _db, _a, _f: []
    )
    assert out["ok"] is False


def test_describe_moment_with_backend_returns_text(db: Database) -> None:
    out = context.describe_moment(
        db,
        "asset",
        10,
        backend=_FakeBackend(True, "a person walking on a beach"),
        extract=lambda _db, _a, _f: [b"jpeg-bytes"],
    )
    assert out["ok"] is True
    assert out["description"] == "a person walking on a beach"


def test_resolve_describe_backend_none_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAURA_VLM_MODEL", raising=False)
    monkeypatch.delenv("LAURA_VLM", raising=False)
    assert describe.resolve_describe_backend() is None
