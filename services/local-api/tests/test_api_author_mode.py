"""Author mode: an external session gets a board with both gates forced and no team job;
the team chat path and the author write path refuse each other's sessions."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from laura.api.short_creator import (
    _create_author_production_session,
    run_production_follow_up,
)
from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.short_creator.board import Board
from laura.short_creator.production_orchestrator import board_root_for


def _db(tmp_path: Path) -> Database:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db: Database = SqliteDatabase(settings.db_path)
    db.migrate()
    return db


def _seed_asset(db: Database, tmp_path: Path) -> str:
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(tmp_path / "ws" / "proj"),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a",
        source_path=str(tmp_path / "input.mp4"),
    )
    return str(asset["id"])


def test_author_create_builds_board_with_gates_and_no_job(tmp_path: Path) -> None:
    db = _db(tmp_path)
    asset_id = _seed_asset(db, tmp_path)

    session_id = _create_author_production_session(
        db, asset_id, task="ui tour short", target_seconds=35.0,
        format="insta", language="English",
    )

    session = repos.get_production_session(db, session_id)
    assert session is not None
    assert session.get("latest_job_id") in (None, "")  # no team job enqueued
    board = Board.open(board_root_for(db, asset_id, session_id))
    meta = board.meta()
    assert meta.author == "external"
    assert meta.scene_gate is True and meta.script_gate is True
    assert meta.task == "ui tour short"


def test_follow_up_refuses_author_sessions(tmp_path: Path) -> None:
    db = _db(tmp_path)
    asset_id = _seed_asset(db, tmp_path)
    session_id = _create_author_production_session(
        db, asset_id, task="t", target_seconds=30.0, format="insta", language="German",
    )

    with pytest.raises(HTTPException) as exc:
        run_production_follow_up(db, session_id, "make it punchier")
    assert exc.value.status_code == 409
    assert "author session" in str(exc.value.detail)
