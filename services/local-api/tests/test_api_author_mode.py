"""Author mode: an external session gets a board with both gates forced and no team job;
the team chat path and the author write path refuse each other's sessions."""
from __future__ import annotations

from pathlib import Path
from typing import Any

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


# --- approve_production_script (Task 9: shared Gate-B approve service) -------------------------


def _board_with_script(db: Database, tmp_path: Path) -> tuple[str, Board]:
    """Author session + a minimal approved-able script written to its board."""
    asset_id = _seed_asset(db, tmp_path)
    session_id = _create_author_production_session(
        db, asset_id, task="t", target_seconds=30.0, format="insta", language="German",
    )
    from laura.short_creator.board import Board
    from laura.short_creator.board_models import Script, ScriptLine
    from laura.short_creator.production_orchestrator import board_root_for

    board = Board.open(board_root_for(db, asset_id, session_id))
    board.save("script", Script(
        language="German",
        lines=[ScriptLine(chapter=1, scene_number=1, text="Willkommen bei Laura.")],
    ))
    return session_id, board


def test_approve_stamps_and_enqueues_resume(tmp_path: Path, monkeypatch: Any) -> None:
    db = _db(tmp_path)
    session_id, board = _board_with_script(db, tmp_path)
    import laura.api.short_creator as api_sc
    calls: list[str] = []

    def _fake_resume(db_: Database, sid: str) -> dict[str, Any]:
        calls.append(sid)
        return {"job_id": "j1"}

    monkeypatch.setattr(api_sc, "run_production_resume", _fake_resume)
    out = api_sc.approve_production_script(db, session_id)
    assert out["outcome"] == "resumed" and out["job_id"] == "j1"
    assert calls == [session_id]
    assert board.meta().script_approved_utc is not None


def test_approve_without_script_409s(tmp_path: Path) -> None:
    db = _db(tmp_path)
    asset_id = _seed_asset(db, tmp_path)
    session_id = _create_author_production_session(
        db, asset_id, task="t", target_seconds=30.0, format="insta", language="German",
    )
    import laura.api.short_creator as api_sc
    with pytest.raises(HTTPException) as exc:
        api_sc.approve_production_script(db, session_id)
    assert exc.value.status_code == 409


def test_failed_resume_rolls_back_fresh_stamp(tmp_path: Path, monkeypatch: Any) -> None:
    db = _db(tmp_path)
    session_id, board = _board_with_script(db, tmp_path)
    import laura.api.short_creator as api_sc

    def boom(db_: Database, sid: str) -> dict[str, Any]:
        raise HTTPException(500, "queue down")

    monkeypatch.setattr(api_sc, "run_production_resume", boom)
    with pytest.raises(HTTPException):
        api_sc.approve_production_script(db, session_id)
    assert board.meta().script_approved_utc is None  # compensating rollback
