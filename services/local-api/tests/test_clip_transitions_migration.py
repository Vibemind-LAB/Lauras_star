"""Plan A / Task 1 — migration 0023 adds transition fields to timeline_clips."""

from __future__ import annotations

from laura.db.database import Database


def test_timeline_clips_has_transition_columns(db: Database) -> None:
    with db.connection() as conn:
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(timeline_clips)").fetchall()
        }
    assert {"transition_after_kind", "transition_after_frames"} <= cols


def test_clip_transition_defaults_to_hard(db: Database) -> None:
    """A newly inserted clip defaults to a hard cut (no transition)."""
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='timeline_clips'"
        ).fetchall()
    ddl = rows[0]["sql"]
    assert "transition_after_kind" in ddl and "'hard'" in ddl
    assert "transition_after_frames" in ddl
