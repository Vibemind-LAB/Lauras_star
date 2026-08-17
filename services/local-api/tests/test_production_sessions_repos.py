"""Tests for production_sessions DB layer (agentic short-creator).

Covers:
- schema_version() >= 32 after migrate()
- create_production_session + get_production_session roundtrip
- get_production_session for unknown session_id → None
- list_production_sessions filtered by asset_id and sorted newest-first
- duplicate create_production_session (same session_id) → sqlite3.IntegrityError
- cascade deletion: deleting media_assets row cascades to production_sessions
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase


def _db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    # Foreign keys are enabled by default in SqliteDatabase.connect()
    return db


def _seed_project_and_asset(db: SqliteDatabase, tmp_path: Path, asset_id: str) -> tuple[str, str]:
    """Seed a project and media_assets row. Returns (project_id, asset_id)."""
    workspace = tmp_path / "ws" / "project"
    workspace.mkdir(parents=True, exist_ok=True)

    project = repos.create_project(
        db,
        name="test_project",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(workspace),
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="test.mp4",
        source_path=str(workspace / "test.mp4"),
    )
    return project["id"], asset["id"]


# ---------------------------------------------------------------------------
# Migration / schema version
# ---------------------------------------------------------------------------


def test_schema_version_includes_resumable_visual_selection(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert db.schema_version() >= 36


# ---------------------------------------------------------------------------
# Create + get roundtrip
# ---------------------------------------------------------------------------


def test_create_and_get_production_session(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _, asset_id = _seed_project_and_asset(db, tmp_path, "asset_1")

    repos.create_production_session(
        db, session_id="sess_001", asset_id=asset_id, created_utc="2026-07-14T10:00:00.000Z"
    )

    session = repos.get_production_session(db, "sess_001")
    assert session is not None
    assert session["session_id"] == "sess_001"
    assert session["asset_id"] == asset_id
    assert session["created_utc"] == "2026-07-14T10:00:00.000Z"


def test_production_session_persists_brief_conversation_and_updated_time(
    tmp_path: Path,
) -> None:
    """Catches losing the resumable session's brief or chat link on restart."""
    db = _db(tmp_path)
    _, asset_id = _seed_project_and_asset(db, tmp_path, "asset_1")
    repos.create_conversation(
        db,
        conversation_id="conversation-1",
        created_utc="2026-08-17T08:00:00+00:00",
    )

    repos.create_production_session(
        db,
        session_id="session-1",
        asset_id=asset_id,
        created_utc="2026-08-17T08:00:00+00:00",
        brief_text="Baue den Rough Cut weiter",
    )
    repos.link_production_session_conversation(
        db,
        "session-1",
        "conversation-1",
        updated_utc="2026-08-17T08:01:00+00:00",
    )

    session = repos.get_production_session(db, "session-1")
    assert session is not None
    assert session["brief_text"] == "Baue den Rough Cut weiter"
    assert session["conversation_id"] == "conversation-1"
    assert session["updated_utc"] == "2026-08-17T08:01:00+00:00"


def test_list_production_sessions_by_updated_includes_all_assets(tmp_path: Path) -> None:
    """Catches the resume list being scoped to one asset or sorted by creation time."""
    db = _db(tmp_path)
    _, first_asset_id = _seed_project_and_asset(db, tmp_path, "asset_1")
    _, second_asset_id = _seed_project_and_asset(db, tmp_path, "asset_2")
    repos.create_production_session(
        db,
        session_id="older-created-but-recently-edited",
        asset_id=first_asset_id,
        created_utc="2026-08-17T08:00:00+00:00",
    )
    repos.create_production_session(
        db,
        session_id="newer-created",
        asset_id=second_asset_id,
        created_utc="2026-08-17T09:00:00+00:00",
    )
    repos.touch_production_session(
        db,
        "older-created-but-recently-edited",
        "2026-08-17T10:00:00+00:00",
    )

    rows = repos.list_production_sessions_by_updated(db)

    assert [row["session_id"] for row in rows] == [
        "older-created-but-recently-edited",
        "newer-created",
    ]


def test_deleting_conversation_keeps_session_and_clears_link(tmp_path: Path) -> None:
    """Catches a deleted chat making its resumable production session disappear."""
    db = _db(tmp_path)
    _, asset_id = _seed_project_and_asset(db, tmp_path, "asset_1")
    repos.create_conversation(
        db,
        conversation_id="conversation-1",
        created_utc="2026-08-17T08:00:00+00:00",
    )
    repos.create_production_session(
        db,
        session_id="session-1",
        asset_id=asset_id,
        created_utc="2026-08-17T08:00:00+00:00",
    )
    repos.link_production_session_conversation(
        db,
        "session-1",
        "conversation-1",
        updated_utc="2026-08-17T08:01:00+00:00",
    )

    repos.delete_conversation(db, "conversation-1")

    session = repos.get_production_session(db, "session-1")
    assert session is not None
    assert session["conversation_id"] is None


# ---------------------------------------------------------------------------
# Get unknown session
# ---------------------------------------------------------------------------


def test_get_unknown_production_session(tmp_path: Path) -> None:
    db = _db(tmp_path)
    session = repos.get_production_session(db, "unknown_sess")
    assert session is None


# ---------------------------------------------------------------------------
# List by asset, newest first
# ---------------------------------------------------------------------------


def test_list_production_sessions_by_asset_newest_first(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _, asset_1_id = _seed_project_and_asset(db, tmp_path, "asset_1")
    _, asset_2_id = _seed_project_and_asset(db, tmp_path, "asset_2")

    # Insert 3 sessions for asset_1, with different timestamps
    repos.create_production_session(
        db, session_id="sess_001", asset_id=asset_1_id, created_utc="2026-07-14T08:00:00.000Z"
    )
    repos.create_production_session(
        db, session_id="sess_002", asset_id=asset_1_id, created_utc="2026-07-14T10:00:00.000Z"
    )
    repos.create_production_session(
        db, session_id="sess_003", asset_id=asset_1_id, created_utc="2026-07-14T09:00:00.000Z"
    )

    # Insert 1 session for asset_2 (should not appear)
    repos.create_production_session(
        db, session_id="sess_004", asset_id=asset_2_id, created_utc="2026-07-14T11:00:00.000Z"
    )

    sessions = repos.list_production_sessions(db, asset_1_id)
    assert len(sessions) == 3

    # Newest first: sess_002, sess_003, sess_001
    assert sessions[0]["session_id"] == "sess_002"
    assert sessions[1]["session_id"] == "sess_003"
    assert sessions[2]["session_id"] == "sess_001"


# ---------------------------------------------------------------------------
# Duplicate session_id (PK conflict)
# ---------------------------------------------------------------------------


def test_duplicate_session_id_raises_integrity_error(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _, asset_id = _seed_project_and_asset(db, tmp_path, "asset_1")

    repos.create_production_session(
        db, session_id="sess_001", asset_id=asset_id, created_utc="2026-07-14T10:00:00.000Z"
    )

    # Try to insert again with same session_id (PK collision)
    with pytest.raises(sqlite3.IntegrityError):
        repos.create_production_session(
            db, session_id="sess_001", asset_id=asset_id, created_utc="2026-07-14T10:00:00.000Z"
        )


# ---------------------------------------------------------------------------
# Cascade deletion on media_assets delete
# ---------------------------------------------------------------------------


def test_cascade_delete_production_session_on_asset_delete(tmp_path: Path) -> None:
    """Deleting a media_assets row cascades to production_sessions via FK."""
    db = _db(tmp_path)
    project_id, asset_id = _seed_project_and_asset(db, tmp_path, "asset_to_delete")

    # Create a production session for this asset
    repos.create_production_session(
        db, session_id="sess_001", asset_id=asset_id, created_utc="2026-07-14T10:00:00.000Z"
    )

    # Verify it exists
    session = repos.get_production_session(db, "sess_001")
    assert session is not None

    # Delete the asset (cascades to production_sessions) via transaction
    with db.transaction() as conn:
        conn.execute("DELETE FROM media_assets WHERE id = ?", (asset_id,))

    # Verify the session is now gone
    session = repos.get_production_session(db, "sess_001")
    assert session is None
