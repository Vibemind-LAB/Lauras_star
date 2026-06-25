"""Tests for render.captions_source.candidate_caption_words.

candidate_caption_words maps an asset's transcript words for one
``[start_frame, end_frame_exclusive)`` SOURCE range to CLIP-LOCAL frames (frame 0 ==
start_frame), filters to the range, and merges punctuation into the preceding word.

DB setup mirrors tests/test_captions_source.py (SqliteDatabase + repos helpers).
"""

from __future__ import annotations

from pathlib import Path

from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.render.captions_source import candidate_caption_words


def _make_db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(tmp_path / "laura.db")
    db.migrate()
    return db


def _seed(db: SqliteDatabase) -> tuple[str, str]:
    """Project + asset + analysis_run + one segment with words. Returns (asset_id, run_id).

    Words (source frames, end-exclusive):
      idx=0  [10, 20)  "Hallo"     normal     INSIDE  [10, 60)
      idx=1  [20, 22)  ","         punct      INSIDE
      idx=2  [25, 35)  "Welt"      normal     INSIDE  (end clamps to 60-... see test)
      idx=3  [70, 80)  "draussen"  normal     OUTSIDE (>= 60)
      idx=4  [5,  9)   "vor"       normal     OUTSIDE (< 10, start before range)
    """
    project = repos.create_project(
        db, name="P", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="a.mp4", source_path="/tmp/a.mp4",
    )
    run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="t", config={})
    repos.insert_segment_with_words(
        db,
        asset_id=asset["id"],
        run_id=run["id"],
        speaker_id=None,
        segment={
            "start_sample": 0, "end_sample": 256000,
            "start_frame": 0, "end_frame": 80,
            "text": "vor Hallo, Welt draussen", "confidence": 1.0,
        },
        words=[
            {"idx": 0, "start_sample": 0, "end_sample": 1, "start_frame": 10,
             "end_frame": 20, "text": "Hallo", "confidence": 1.0, "is_punctuation": False},
            {"idx": 1, "start_sample": 0, "end_sample": 1, "start_frame": 20,
             "end_frame": 22, "text": ",", "confidence": 1.0, "is_punctuation": True},
            {"idx": 2, "start_sample": 0, "end_sample": 1, "start_frame": 25,
             "end_frame": 35, "text": "Welt", "confidence": 1.0, "is_punctuation": False},
            {"idx": 3, "start_sample": 0, "end_sample": 1, "start_frame": 70,
             "end_frame": 80, "text": "draussen", "confidence": 1.0, "is_punctuation": False},
            {"idx": 4, "start_sample": 0, "end_sample": 1, "start_frame": 5,
             "end_frame": 9, "text": "vor", "confidence": 1.0, "is_punctuation": False},
        ],
    )
    # finish the run so it is "succeeded" (not required by the function, but realistic).
    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})
    return asset["id"], run["id"]


def test_offsets_to_clip_local_and_merges_punct(tmp_path: Path) -> None:
    """Words map to clip-local frames; ',' merges into 'Hallo'; in-range only; sorted."""
    db = _make_db(tmp_path)
    asset_id, run_id = _seed(db)

    # Candidate source range [10, 60): duration 50.
    words = candidate_caption_words(db, asset_id, run_id, 10, 60)

    # "Hallo" : local_start = 10-10 = 0 ; local_end = min(20,60)-10 = 10
    #   then "," (src [20,22)) merges → text "Hallo,", end = max(10, 22-10=12) = 12
    # "Welt"  : local_start = 25-10 = 15 ; local_end = min(35,60)-10 = 25
    # "draussen" (start 70) and "vor" (start 5) are out of [10,60) → excluded.
    assert words == [("Hallo,", 0, 12), ("Welt", 15, 25)]


def test_end_clamped_to_clip_duration(tmp_path: Path) -> None:
    """A word whose source end exceeds the range end clamps to the clip duration."""
    db = _make_db(tmp_path)
    asset_id, run_id = _seed(db)

    # Range [10, 30): duration 20. "Welt" src=[25,35) starts inside (25 < 30),
    # local_start = 15, local_end = min(35,30)-10 = 20 (clamped to duration).
    words = candidate_caption_words(db, asset_id, run_id, 10, 30)
    assert ("Welt", 15, 20) in words
    # No local frame may exceed the duration (20).
    for _text, start, end in words:
        assert 0 <= start <= 20
        assert 0 <= end <= 20


def test_empty_when_no_words_in_range(tmp_path: Path) -> None:
    """A range that contains no word starts returns []."""
    db = _make_db(tmp_path)
    asset_id, run_id = _seed(db)
    assert candidate_caption_words(db, asset_id, run_id, 40, 65) == []


def test_zero_or_negative_duration_returns_empty(tmp_path: Path) -> None:
    """A non-positive duration range returns [] (no division/negative frames)."""
    db = _make_db(tmp_path)
    asset_id, run_id = _seed(db)
    assert candidate_caption_words(db, asset_id, run_id, 30, 30) == []
    assert candidate_caption_words(db, asset_id, run_id, 30, 10) == []


def test_leading_punctuation_dropped(tmp_path: Path) -> None:
    """Punctuation with no preceding kept word in-range is dropped silently."""
    db = _make_db(tmp_path)
    asset_id, run_id = _seed(db)
    # Range [20, 26): only the "," (start 20) and "Welt" (start 25) qualify;
    # "," has no preceding word in-range → dropped, "Welt" survives.
    words = candidate_caption_words(db, asset_id, run_id, 20, 26)
    assert [w[0] for w in words] == ["Welt"]
