"""Tests for shorts_candidates DB layer (S5a).

Covers:
- schema_version() == 30 after migrate()
- replace_shorts_candidates + list_shorts_candidates round-trip (2 candidates)
- replace-not-accumulate (second call replaces, not appends)
- get_short_candidate for existing + missing id
"""
from __future__ import annotations

from pathlib import Path

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase


def _db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    return db


# ---------------------------------------------------------------------------
# Migration / schema version
# ---------------------------------------------------------------------------

def test_schema_version_is_30_after_migrate(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert db.schema_version() == 30


# ---------------------------------------------------------------------------
# Round-trip: insert 2 candidates, read back
# ---------------------------------------------------------------------------

_CANDIDATE_A = {
    "start_frame": 0,
    "end_frame_exclusive": 750,
    "start_boundary": "speaker_turn",
    "end_boundary": "sentence_end",
    "score": 1.23,
    "rejected": False,
    "reject_reason": None,
    "score_breakdown": {"transcript_safety": 1.0, "audio_silence_at_boundaries": 0.23},
    "qa_passed": True,
    "qa_issues": [],
}

_CANDIDATE_B = {
    "start_frame": 900,
    "end_frame_exclusive": 1800,
    "start_boundary": "sentence_end",
    "end_boundary": "sentence_end",
    "score": -999.0,
    "rejected": True,
    "reject_reason": "word_interruption",
    "score_breakdown": {"transcript_safety": 0.0},
    "qa_passed": False,
    "qa_issues": ["start_on_black", "mid_word_cut"],
}


def test_roundtrip_two_candidates(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repos.replace_shorts_candidates(db, "proj1", "asset1", "tl1", [_CANDIDATE_A, _CANDIDATE_B])

    rows = repos.list_shorts_candidates(db, "tl1")
    assert len(rows) == 2

    a, b = rows
    # order
    assert a["order_index"] == 0
    assert b["order_index"] == 1

    # integer frames preserved
    assert a["start_frame"] == 0
    assert a["end_frame_exclusive"] == 750
    assert b["start_frame"] == 900
    assert b["end_frame_exclusive"] == 1800

    # boundary strings
    assert a["start_boundary"] == "speaker_turn"
    assert a["end_boundary"] == "sentence_end"

    # score
    assert abs(a["score"] - 1.23) < 1e-9
    assert abs(b["score"] - (-999.0)) < 1e-9

    # bool fields
    assert a["rejected"] is False
    assert b["rejected"] is True
    assert a["qa_passed"] is True
    assert b["qa_passed"] is False

    # reject reason
    assert a["reject_reason"] is None
    assert b["reject_reason"] == "word_interruption"

    # JSON-decoded dict
    assert isinstance(a["score_breakdown"], dict)
    assert a["score_breakdown"]["transcript_safety"] == 1.0

    # JSON-decoded list
    assert a["qa_issues"] == []
    assert b["qa_issues"] == ["start_on_black", "mid_word_cut"]


# ---------------------------------------------------------------------------
# Replace-not-accumulate
# ---------------------------------------------------------------------------

def test_replace_does_not_accumulate(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repos.replace_shorts_candidates(db, "proj1", "asset1", "tl1", [_CANDIDATE_A, _CANDIDATE_B])
    # Second call with only one candidate
    repos.replace_shorts_candidates(db, "proj1", "asset1", "tl1", [_CANDIDATE_A])
    rows = repos.list_shorts_candidates(db, "tl1")
    assert len(rows) == 1
    assert rows[0]["order_index"] == 0
    assert rows[0]["start_frame"] == 0


# ---------------------------------------------------------------------------
# get_short_candidate
# ---------------------------------------------------------------------------

def test_get_short_candidate_found(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repos.replace_shorts_candidates(db, "proj1", "asset1", "tl1", [_CANDIDATE_A])
    rows = repos.list_shorts_candidates(db, "tl1")
    cid = rows[0]["id"]

    row = repos.get_short_candidate(db, cid)
    assert row is not None
    assert row["id"] == cid
    assert row["start_frame"] == 0
    assert row["rejected"] is False
    assert isinstance(row["score_breakdown"], dict)
    assert row["qa_issues"] == []


def test_get_short_candidate_missing(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert repos.get_short_candidate(db, "does-not-exist") is None
