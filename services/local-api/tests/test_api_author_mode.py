"""Author mode: an external session gets a board with both gates forced and no team job;
the team chat path and the author write path refuse each other's sessions."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from laura.api.short_creator import (
    _create_author_production_session,
    confirm_scene_selection,
    run_production_follow_up,
)
from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.short_creator.authoring import call_production_tool
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


# --- end-to-end author flow (C1+C2+C3, 2026-08-19 final review) -------------------------------

_FPS = 30
_SCENE_FRAMES = 300  # 300 frames @ 30fps = 10.0s — enough material to clear the script budget
# gate below (roughly a 15-word chapter budget at the German TTS rate) without tuning to a
# hair's-breadth margin.
_SCENE_TEXT = (
    "willkommen beim dashboard heute zeigen wir dir gemeinsam die wichtigsten funktionen"
)


def _seed_asset_with_rough_cut(db: Database, tmp_path: Path) -> str:
    """Like ``_seed_asset``, but with a real one-scene rough cut + succeeded transcript behind
    it (mirrors ``tests/test_production_tools_write.py``'s ``_seed_scene``) — the author flow's
    propose/review/storyline/script steps all resolve scene 1 through the rough cut and its
    transcript, not through the bare asset row ``_seed_asset`` returns."""
    project = repos.create_project(
        db, name="p", rate_num=_FPS, rate_den=1, drop_frame=False,
        workspace_root=str(tmp_path / "ws" / "proj"),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a",
        source_path=str(tmp_path / "input.mp4"),
    )
    run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="t", config={})
    repos.start_analysis_run(db, run["id"])
    repos.insert_segment_with_words(
        db, asset_id=asset["id"], run_id=run["id"], speaker_id=None,
        segment={
            "start_sample": 0,
            "end_sample": 192_000,
            "start_frame": 0,
            "end_frame": _SCENE_FRAMES,
            "text": _SCENE_TEXT,
            "confidence": 1.0,
        },
        words=[],
    )
    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})
    timeline = repos.create_timeline(
        db, project_id=project["id"], name="Rough Cut", kind="rough_cut",
        created_from=asset["id"],
    )
    repos.add_timeline_clip(
        db, timeline_id=timeline["id"], asset_id=asset["id"],
        src_in_frame=0, src_out_frame_exclusive=_SCENE_FRAMES,
        seq_in_frame=0, seq_out_frame_exclusive=_SCENE_FRAMES,
        lane=0, role="base",
    )
    repos.replace_scenes(db, project["id"], timeline["id"], [(0, _SCENE_FRAMES)])
    return str(asset["id"])


def test_author_flow_end_to_end(tmp_path: Path, monkeypatch: Any) -> None:
    """Regression net for the whole author arc: propose -> confirm (no team job, default
    reviews materialized) -> storyline -> script -> approve, entirely through the author
    dispatch/endpoints, without the team ever running.

    Covers C1 (confirm must not enqueue a team resume on an author board) and C2+C3 (Gate-S
    confirm materializes default full-scene SceneReviews so the deterministic chain — resume
    point, save_storyline's review check, the script budget guard — has something real to walk
    past even though review_scene, a team-only tool, never runs here)."""
    db = _db(tmp_path)
    asset_id = _seed_asset_with_rough_cut(db, tmp_path)
    session_id = _create_author_production_session(
        db, asset_id, task="dashboard tour", target_seconds=20.0,
        format="insta", language="German",
    )

    proposed = call_production_tool(
        db, session_id, "propose_scene_selection",
        candidates=[{
            "scene_number": 1, "rationale": "hook", "recommended": True,
            "description": "dashboard", "transcript_snippet": "willkommen beim dashboard",
        }],
    )
    assert proposed["ok"] is True, proposed

    session_before = repos.get_production_session(db, session_id)
    assert session_before is not None
    latest_job_before = session_before.get("latest_job_id")

    confirmed = confirm_scene_selection(db, session_id, [1])
    assert confirmed["job_id"] is None  # C1: no team resume enqueued from confirm

    session_after = repos.get_production_session(db, session_id)
    assert session_after is not None
    assert session_after.get("latest_job_id") == latest_job_before  # unchanged: no job at all

    board = Board.open(board_root_for(db, asset_id, session_id))
    reviews = board.scene_reviews()
    assert [r.scene_number for r in reviews] == [1]  # C2+C3: default review materialized
    assert reviews[0].degraded is True
    assert reviews[0].best_window.duration_s == pytest.approx(_SCENE_FRAMES / _FPS)

    # Re-confirming (idempotent-heal branch) must not duplicate the review.
    reconfirmed = confirm_scene_selection(db, session_id, [1])
    assert reconfirmed.get("already_current") is True
    assert len(Board.open(board_root_for(db, asset_id, session_id)).scene_reviews()) == 1

    storyline_out = call_production_tool(
        db, session_id, "save_storyline",
        red_thread="a quick tour", chapters=[{
            "chapter": 1, "role": "hook", "message": "look at this",
            "scene_numbers": [1], "target_seconds": 8.0,
        }],
    )
    assert storyline_out["ok"] is True, storyline_out

    script_out = call_production_tool(
        db, session_id, "save_script_chapter",
        chapter=1, lines=[{"scene_number": 1, "text": _SCENE_TEXT.capitalize() + "."}],
    )
    assert script_out["ok"] is True, script_out

    import laura.api.short_creator as api_sc

    resume_calls: list[str] = []

    def _fake_resume(db_: Database, sid: str) -> dict[str, Any]:
        resume_calls.append(sid)
        return {"job_id": "j-approve"}

    monkeypatch.setattr(api_sc, "run_production_resume", _fake_resume)
    approved = api_sc.approve_production_script(db, session_id)
    assert approved["outcome"] == "resumed"
    assert resume_calls == [session_id]


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
