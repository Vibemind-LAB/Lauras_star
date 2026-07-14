"""Tests for production_sessions DB layer (agentic short-creator).

Covers:
- schema_version() >= 32 after migrate()
- create_production_session + get_production_session roundtrip
- get_production_session for unknown session_id → None
- list_production_sessions filtered by asset_id and sorted newest-first
- duplicate create_production_session (same session_id) → sqlite3.IntegrityError
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
    return db


# ---------------------------------------------------------------------------
# Migration / schema version
# ---------------------------------------------------------------------------


def test_schema_version_is_32_after_migrate(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert db.schema_version() >= 32


# ---------------------------------------------------------------------------
# Create + get roundtrip
# ---------------------------------------------------------------------------


def test_create_and_get_production_session(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repos.create_production_session(
        db, session_id="sess_001", asset_id="asset_1", created_utc="2026-07-14T10:00:00.000Z"
    )

    session = repos.get_production_session(db, "sess_001")
    assert session is not None
    assert session["session_id"] == "sess_001"
    assert session["asset_id"] == "asset_1"
    assert session["created_utc"] == "2026-07-14T10:00:00.000Z"


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
    # Insert 3 sessions for asset_1, with different timestamps
    repos.create_production_session(
        db, session_id="sess_001", asset_id="asset_1", created_utc="2026-07-14T08:00:00.000Z"
    )
    repos.create_production_session(
        db, session_id="sess_002", asset_id="asset_1", created_utc="2026-07-14T10:00:00.000Z"
    )
    repos.create_production_session(
        db, session_id="sess_003", asset_id="asset_1", created_utc="2026-07-14T09:00:00.000Z"
    )

    # Insert 1 session for asset_2 (should not appear)
    repos.create_production_session(
        db, session_id="sess_004", asset_id="asset_2", created_utc="2026-07-14T11:00:00.000Z"
    )

    sessions = repos.list_production_sessions(db, "asset_1")
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
    repos.create_production_session(
        db, session_id="sess_001", asset_id="asset_1", created_utc="2026-07-14T10:00:00.000Z"
    )

    # Try to insert again with same session_id (PK collision)
    with pytest.raises(sqlite3.IntegrityError):
        repos.create_production_session(
            db, session_id="sess_001", asset_id="asset_1", created_utc="2026-07-14T10:00:00.000Z"
        )
