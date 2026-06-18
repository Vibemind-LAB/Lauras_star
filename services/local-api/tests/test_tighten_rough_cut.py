"""Auto-cut tightening: drop dead-air from the auto-rough-cut so the user lands on a pacier
cut with zero clicks. Transcript-gated and a strict no-op without dead-air, so non-speech
footage (B-roll, music, silent clips) is never trimmed. Opt out via ``LAURA_AUTO_TIGHTEN=0``.
"""

from __future__ import annotations

from typing import Any

import pytest

from laura.db import repos
from laura.db.database import Database
from laura.scenes.build import autobuild_asset_edit_ready, speech_keep_ranges

# --------------------------------------------------------------------------- pure function

def _w(start: int, end: int) -> dict[str, Any]:
    return {"start_frame": start, "end_frame": end, "speaker": None}


def test_keep_ranges_no_words_is_unchanged() -> None:
    # Safety: a clip with no transcript words is never trimmed.
    assert speech_keep_ranges(0, 100, [], pad=10, min_gap=30) == [(0, 100)]


def test_keep_ranges_trims_leading_and_trailing_dead_air() -> None:
    # One word in the middle; long silence either side gets trimmed to the padded word.
    assert speech_keep_ranges(0, 100, [_w(40, 60)], pad=10, min_gap=30) == [(30, 70)]


def test_keep_ranges_keeps_short_lead_in() -> None:
    # A lead-in shorter than min_gap is kept (never shave a fraction off the head).
    assert speech_keep_ranges(0, 100, [_w(35, 60)], pad=10, min_gap=30) == [(0, 70)]


def test_keep_ranges_splits_on_large_internal_gap() -> None:
    ranges = speech_keep_ranges(0, 200, [_w(10, 20), _w(160, 170)], pad=10, min_gap=30)
    assert ranges == [(0, 30), (150, 200)]


def test_keep_ranges_merges_small_internal_gap() -> None:
    # Gap below min_gap is preserved (no choppy micro-cuts): a single kept range.
    assert speech_keep_ranges(0, 200, [_w(10, 20), _w(60, 70)], pad=10, min_gap=30) == [(0, 80)]


def test_keep_ranges_are_integer_end_exclusive_and_in_bounds() -> None:
    ranges = speech_keep_ranges(5, 95, [_w(40, 50)], pad=7, min_gap=20)
    for a, b in ranges:
        assert isinstance(a, int) and isinstance(b, int)
        assert b > a  # no zero-length clips
        assert 5 <= a < b <= 95  # stays within the clip, end-exclusive


# --------------------------------------------------------------------------- integration

def _seed(db: Database) -> tuple[str, str, str]:
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a", source_path="/tmp/a.mp4"
    )
    run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="t", config={})
    return str(project["id"]), str(asset["id"]), str(run["id"])


def _shot(src_in: int, src_out: int, *, keep: bool = True) -> dict[str, Any]:
    return {
        "src_in_frame": src_in, "src_out_frame_exclusive": src_out, "method": "adaptive",
        "confidence": None, "thumbnail_path": None, "black_ratio": None, "static_score": None,
        "phash": None, "blur_score": None, "keep": keep, "drop_reason": None,
    }


def _seed_words(db: Database, asset_id: str, run_id: str, spans: list[tuple[int, int]]) -> None:
    """Insert one transcript segment whose words occupy ``spans`` (source frames)."""
    lo = min(s for s, _ in spans)
    hi = max(e for _, e in spans)
    words = [
        {
            "idx": i, "start_sample": s * 1000, "end_sample": e * 1000,
            "start_frame": s, "end_frame": e, "text": f"w{i}", "confidence": None,
            "is_punctuation": False,
        }
        for i, (s, e) in enumerate(spans)
    ]
    repos.insert_segment_with_words(
        db, asset_id=asset_id, run_id=run_id, speaker_id=None,
        segment={
            "start_sample": lo * 1000, "end_sample": hi * 1000,
            "start_frame": lo, "end_frame": hi, "text": "seg", "confidence": None,
        },
        words=words,
    )


def _clips(db: Database, project_id: str, asset_id: str) -> list[tuple[int, int]]:
    tl = repos.get_or_create_asset_rough_cut(db, project_id, asset_id)
    return [
        (c["src_in_frame"], c["src_out_frame_exclusive"])
        for c in repos.list_timeline_clips(db, tl["id"])
    ]


def _run_autobuild(db: Database) -> tuple[str, str]:
    project_id, asset_id, run_id = _seed(db)
    repos.insert_shots(db, asset_id=asset_id, run_id=run_id, shots=[_shot(0, 300)])
    # speech only at the very start and end -> ~240 frames of dead-air in the middle.
    _seed_words(db, asset_id, run_id, [(0, 30), (270, 300)])
    autobuild_asset_edit_ready(db, project_id=project_id, asset_id=asset_id, run_id=run_id)
    return project_id, asset_id


def test_autobuild_tightens_dead_air(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAURA_AUTO_TIGHTEN", raising=False)  # default on
    monkeypatch.setenv("LAURA_TIGHTEN_PAD_MS", "300")  # 9 frames @30fps
    monkeypatch.setenv("LAURA_TIGHTEN_MIN_GAP_MS", "900")  # 27 frames @30fps
    project_id, asset_id = _run_autobuild(db)
    clips = _clips(db, project_id, asset_id)
    # The single 300-frame shot is split into two short speech ranges; dead-air is gone.
    assert clips == [(0, 39), (261, 300)]
    assert sum(b - a for a, b in clips) == 78  # 222 frames trimmed
    # Sequence positions stay gapless and contiguous after trimming (timeline invariant).
    tl = repos.get_or_create_asset_rough_cut(db, project_id, asset_id)
    seq = [
        (c["seq_in_frame"], c["seq_out_frame_exclusive"])
        for c in repos.list_timeline_clips(db, tl["id"])
    ]
    assert seq == [(0, 39), (39, 78)]


def test_autobuild_leaves_non_speech_footage_untouched(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LAURA_AUTO_TIGHTEN", raising=False)
    monkeypatch.setenv("LAURA_TIGHTEN_PAD_MS", "300")
    monkeypatch.setenv("LAURA_TIGHTEN_MIN_GAP_MS", "900")
    project_id, asset_id, run_id = _seed(db)
    # Shot A has speech at its head; shot B is pure B-roll (no words).
    repos.insert_shots(db, asset_id=asset_id, run_id=run_id, shots=[_shot(0, 300), _shot(300, 600)])
    _seed_words(db, asset_id, run_id, [(0, 30)])
    autobuild_asset_edit_ready(db, project_id=project_id, asset_id=asset_id, run_id=run_id)
    clips = _clips(db, project_id, asset_id)
    # B-roll shot (300, 600) survives whole; shot A is trimmed to its padded speech head.
    assert (300, 600) in clips
    assert (0, 39) in clips


def test_autobuild_no_transcript_is_full_cut(db: Database) -> None:
    project_id, asset_id, run_id = _seed(db)
    repos.insert_shots(db, asset_id=asset_id, run_id=run_id, shots=[_shot(0, 300)])
    autobuild_asset_edit_ready(db, project_id=project_id, asset_id=asset_id, run_id=run_id)
    assert _clips(db, project_id, asset_id) == [(0, 300)]


def test_autobuild_tighten_opt_out(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAURA_AUTO_TIGHTEN", "0")
    project_id, asset_id, run_id = _seed(db)
    repos.insert_shots(db, asset_id=asset_id, run_id=run_id, shots=[_shot(0, 300)])
    _seed_words(db, asset_id, run_id, [(0, 30), (270, 300)])
    autobuild_asset_edit_ready(db, project_id=project_id, asset_id=asset_id, run_id=run_id)
    # Opt-out: the full shot stays as one clip, dead-air included.
    assert _clips(db, project_id, asset_id) == [(0, 300)]
