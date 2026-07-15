"""Tests for render.captions_source.timeline_caption_words and
render.captions.group_caption_lines.

DB setup mirrors the pattern in tests/test_delete_words_op.py and
tests/test_timeline_captions.py: SqliteDatabase + repos helpers.
"""

from __future__ import annotations

from pathlib import Path

from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.render.captions import group_caption_lines
from laura.render.captions_source import timeline_caption_words

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(tmp_path / "laura.db")
    db.migrate()
    return db


# ---------------------------------------------------------------------------
# group_caption_lines — pure, no DB
# ---------------------------------------------------------------------------


def test_group_lines_word_cap() -> None:
    """5 words at max_words_per_line=4 → two lines [4, 1]."""
    words = [(f"w{i}", i * 10, i * 10 + 8) for i in range(5)]
    lines = group_caption_lines(words, max_words_per_line=4, max_gap_frames=999)
    assert len(lines) == 2
    assert len(lines[0]) == 4
    assert len(lines[1]) == 1
    # All words preserved in order.
    flat = [w for line in lines for w in line]
    assert flat == words


def test_group_lines_gap_break() -> None:
    """A large gap forces a break even when well below the word cap."""
    # words at frames [0,5), [5,10), then a 50-frame gap, then [60,65)
    words: list[tuple[str, int, int]] = [
        ("A", 0, 5),
        ("B", 5, 10),
        ("C", 60, 65),
    ]
    lines = group_caption_lines(words, max_words_per_line=4, max_gap_frames=15)
    assert len(lines) == 2
    assert lines[0] == [("A", 0, 5), ("B", 5, 10)]
    assert lines[1] == [("C", 60, 65)]


def test_group_lines_empty() -> None:
    assert group_caption_lines([]) == []


def test_group_lines_char_budget() -> None:
    """Long words break the line early: rendered width, not word count, overflows.

    'Dashboard zeigt 59 maßgeschneiderte' is 4 words but ~36 chars — at ASS
    fontsize 72 that renders wider than the 1080px frame (live finding on
    export d25eacb3). With the 30-char budget the group must split before
    'maßgeschneiderte'.
    """
    words = [
        ("Dashboard", 0, 5),
        ("zeigt", 5, 10),
        ("59", 10, 15),
        ("maßgeschneiderte", 15, 20),
        ("Templates.", 20, 25),
    ]
    lines = group_caption_lines(words, max_words_per_line=4, max_gap_frames=999)
    for line in lines:
        text = " ".join(w[0] for w in line)
        assert len(text) <= 30, f"line too wide: {text!r}"
    flat = [w for line in lines for w in line]
    assert flat == words


def test_group_lines_oversized_single_word() -> None:
    """A single word longer than the budget still gets its own line (never dropped)."""
    words = [("kurz", 0, 5), ("Donaudampfschifffahrtsgesellschaft", 5, 10)]
    lines = group_caption_lines(words, max_words_per_line=4, max_gap_frames=999)
    flat = [w for line in lines for w in line]
    assert flat == words
    assert [w[0] for w in lines[-1]] == ["Donaudampfschifffahrtsgesellschaft"]


# ---------------------------------------------------------------------------
# timeline_caption_words — requires DB
# ---------------------------------------------------------------------------


def _seed_db(db: SqliteDatabase) -> tuple[str, str, str]:
    """Create project + asset + analysis_run + segment + words + timeline + clip.

    Returns (timeline_id, asset_id, run_id).

    Clip covers source frames [10, 30).
    seq_in_frame = 100 (so source frame 10 maps to seq frame 100).

    Words inserted:
      idx=0  start_frame=10  end_frame=20  text="Hallo"   is_punctuation=False  INSIDE
      idx=1  start_frame=20  end_frame=22  text=","      is_punctuation=True   INSIDE (punct)
      idx=2  start_frame=30  end_frame=40  text="draussen" is_punctuation=False  OUTSIDE (=src_out)
    """
    project = repos.create_project(
        db, name="TestProj", rate_num=30, rate_den=1,
        drop_frame=False, workspace_root="/tmp/test"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="test.mp4", source_path="/tmp/test.mp4"
    )
    run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="t", config={}
    )
    repos.insert_segment_with_words(
        db,
        asset_id=asset["id"],
        run_id=run["id"],
        speaker_id=None,
        segment={
            "start_sample": 0,
            "end_sample": 96000,
            "start_frame": 10,
            "end_frame": 40,
            "text": "Hallo, draussen",
            "confidence": 0.99,
        },
        words=[
            {
                "idx": 0,
                "start_sample": 0,
                "end_sample": 48000,
                "start_frame": 10,
                "end_frame": 20,
                "text": "Hallo",
                "confidence": 0.99,
                "is_punctuation": False,
            },
            {
                "idx": 1,
                "start_sample": 48000,
                "end_sample": 52800,
                "start_frame": 20,
                "end_frame": 22,
                "text": ",",
                "confidence": 0.99,
                "is_punctuation": True,
            },
            {
                "idx": 2,
                "start_sample": 52800,
                "end_sample": 96000,
                "start_frame": 30,
                "end_frame": 40,
                "text": "draussen",
                "confidence": 0.99,
                "is_punctuation": False,
            },
        ],
    )
    timeline = repos.create_timeline(
        db, project_id=project["id"], name="RC", kind="rough_cut"
    )
    repos.add_timeline_clip(
        db,
        timeline_id=timeline["id"],
        asset_id=asset["id"],
        src_in_frame=10,
        src_out_frame_exclusive=30,
        seq_in_frame=100,
        seq_out_frame_exclusive=120,
    )
    return timeline["id"], asset["id"], run["id"]


def test_caption_words_sequence_mapping(tmp_path: Path) -> None:
    """Returned words have correct sequence frames and punctuation is merged."""
    db = _make_db(tmp_path)
    timeline_id, _asset_id, _run_id = _seed_db(db)

    words = timeline_caption_words(db, timeline_id)

    # Only "Hallo," should be present:
    #   seq_start = seq_in_frame + (src_start - src_in) = 100 + (10 - 10) = 100
    #   src_end_clamped = min(20, 30) = 20
    #   seq_end = 100 + (20 - 10) = 110
    # Then "," merges: seq_end = max(110, 100 + (min(22,30) - 10)) = max(110, 112) = 112
    # "draussen" has start_frame=30 which is NOT < src_out_frame_exclusive=30 → excluded.
    assert len(words) == 1, f"expected 1 word token, got {words!r}"
    text, seq_start, seq_end = words[0]
    assert text == "Hallo,"
    assert seq_start == 100
    assert seq_end == 112


def test_caption_words_out_of_range_dropped(tmp_path: Path) -> None:
    """The word whose start_frame equals src_out_frame_exclusive is excluded."""
    db = _make_db(tmp_path)
    timeline_id, _asset_id, _run_id = _seed_db(db)
    words = timeline_caption_words(db, timeline_id)
    texts = [w[0] for w in words]
    assert "draussen" not in texts


def test_caption_words_empty_timeline(tmp_path: Path) -> None:
    """A timeline with no clips returns an empty list."""
    db = _make_db(tmp_path)
    project = repos.create_project(
        db, name="Empty", rate_num=30, rate_den=1,
        drop_frame=False, workspace_root="/tmp/empty"
    )
    timeline = repos.create_timeline(
        db, project_id=project["id"], name="Empty", kind="rough_cut"
    )
    assert timeline_caption_words(db, timeline["id"]) == []


def test_caption_words_no_transcript_run(tmp_path: Path) -> None:
    """A clip whose asset has no analysis run is silently skipped."""
    db = _make_db(tmp_path)
    project = repos.create_project(
        db, name="NoRun", rate_num=30, rate_den=1,
        drop_frame=False, workspace_root="/tmp/norun"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="b.mp4", source_path="/tmp/b.mp4"
    )
    timeline = repos.create_timeline(
        db, project_id=project["id"], name="NR", kind="rough_cut"
    )
    repos.add_timeline_clip(
        db,
        timeline_id=timeline["id"],
        asset_id=asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=30,
        seq_in_frame=0,
        seq_out_frame_exclusive=30,
    )
    # No analysis run created → should return empty list, not raise.
    assert timeline_caption_words(db, timeline["id"]) == []
