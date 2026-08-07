"""Gate-S confirmation service + endpoint (Task GS4): confirm_scene_selection is the ONLY
writer of scene_selection.confirmed_utc — chat and HTTP both land here.

Fixtures mirror tests/test_script_gate.py's project+asset+session+board seeding and
tests/test_production_tools_scene_gate.py's SceneCandidate construction (kept local per this
repo's self-contained-test-file convention). ``run_production_resume`` is monkeypatched by
NAME on ``laura.api.short_creator`` (same module it lives in — the bare call inside
``confirm_scene_selection`` looks it up via the module namespace at call time, so patching the
module attribute works even though caller and callee share a file) so these tests never touch
a real agent team.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from laura.api.short_creator import confirm_scene_selection
from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.jobs.runner import enqueue
from laura.short_creator.board import Board
from laura.short_creator.board_models import BoardMeta, SceneCandidate, SceneSelection
from laura.short_creator.production_orchestrator import board_root_for

_NOW = "2026-08-06T00:00:00Z"
_TOKEN = "test-token"
_H = {"X-Laura-Token": _TOKEN}


def _candidate(n: int, *, recommended: bool = False) -> SceneCandidate:
    return SceneCandidate(
        scene_number=n, src_start_frame=0, src_end_frame_exclusive=100,
        thumb_frame=50, description="d", transcript_snippet="t",
        rationale="r", recommended=recommended,
    )


def _seed(
    db: Database,
    tmp_path: Path,
    *,
    session_id: str = "sess-1",
    scene_gate: bool = True,
    with_proposal: bool = True,
    confirmed: bool = False,
    selected: list[int] | None = None,
) -> str:
    """Project + asset + production session + a board (``scene_gate`` per arg) with a proposed
    scene_selection (candidates 1-5; 2/4/5 recommended — mirrors the router prompt's own
    example recommendation). ``with_proposal=False`` creates the board without ever saving a
    scene_selection artifact (the "gate on, nothing proposed yet" case). Returns ``asset_id``.
    """
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(tmp_path / "ws" / "proj"),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a", source_path="/tmp/a.mp4",
    )
    repos.create_production_session(
        db, session_id=session_id, asset_id=asset["id"], created_utc=_NOW,
    )
    root = board_root_for(db, asset["id"], session_id)
    meta = BoardMeta(
        session_id=session_id, asset_id=asset["id"], created_utc=_NOW,
        task="t", target_seconds=30.0, scene_gate=scene_gate,
    )
    board = Board.create(root, meta)
    if with_proposal:
        board.save(
            "scene_selection",
            SceneSelection(
                candidates=[_candidate(n, recommended=n in (2, 4, 5)) for n in (1, 2, 3, 4, 5)],
                selected_scene_numbers=selected or ([2, 4] if confirmed else []),
                confirmed_utc=_NOW if confirmed else None,
            ),
        )
    return str(asset["id"])


def _db(tmp_path: Path) -> Database:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db: Database = SqliteDatabase(settings.db_path)
    db.migrate()
    return db


def _seed_job(db: Database, session_id: str, *, status: str = "queued") -> str:
    """A production.run job attached to *session_id* via latest_job_id, in the given status.
    Mirrors test_chat_executor.py's own ``_seed_job`` (enqueue() always inserts 'queued', so a
    non-'queued' target status is set afterwards)."""
    job_id = enqueue(db, queue="production", kind="production.run", payload={}, max_attempts=1)
    repos.set_production_session_job(db, session_id, job_id)
    if status != "queued":
        with db.transaction() as conn:
            conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    return job_id


def _fake_resume(monkeypatch: Any, *, job_id: str = "job-42") -> list[str]:
    """Patches run_production_resume with a fake that records every session_id it was called
    with (rather than touching a real agent team) and returns a job_id."""
    calls: list[str] = []

    def _fake(db: Database, session_id: str) -> dict[str, Any]:
        calls.append(session_id)
        return {"session_id": session_id, "job_id": job_id, "warnings": []}

    monkeypatch.setattr("laura.api.short_creator.run_production_resume", _fake)
    return calls


# --- confirm_scene_selection: happy path + guards -------------------------------------------


def test_confirm_happy_path_enqueues_resume(tmp_path: Path, monkeypatch: Any) -> None:
    calls = _fake_resume(monkeypatch)
    db = _db(tmp_path)
    asset_id = _seed(db, tmp_path)

    out = confirm_scene_selection(db, "sess-1", [2, 5])

    board = Board.open(board_root_for(db, asset_id, "sess-1"))
    sel = board.load("scene_selection")
    assert isinstance(sel, SceneSelection)
    assert sel.selected_scene_numbers == [2, 5] and sel.confirmed_utc is not None
    assert out["job_id"]  # resume enqueued
    assert calls == ["sess-1"]


def test_confirm_dedupes_and_sorts_scene_numbers(tmp_path: Path, monkeypatch: Any) -> None:
    _fake_resume(monkeypatch)
    db = _db(tmp_path)
    asset_id = _seed(db, tmp_path)

    out = confirm_scene_selection(db, "sess-1", [5, 2, 5, 2])

    assert out["selected"] == [2, 5]
    board = Board.open(board_root_for(db, asset_id, "sess-1"))
    sel = board.load("scene_selection")
    assert isinstance(sel, SceneSelection)
    assert sel.selected_scene_numbers == [2, 5]


def test_confirm_rejects_stray_scene(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed(db, tmp_path)

    with pytest.raises(HTTPException) as exc:
        confirm_scene_selection(db, "sess-1", [99])
    assert exc.value.status_code == 422


def test_confirm_rejects_empty(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed(db, tmp_path)

    with pytest.raises(HTTPException) as exc:
        confirm_scene_selection(db, "sess-1", [])
    assert exc.value.status_code == 422


def test_reconfirm_same_set_is_noop_but_heals_a_resume(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """No board write on re-confirm (version untouched — a real second write would bump it
    and invalidate the whole downstream chain for nothing), but the already_current branch
    still calls run_production_resume to HEAL a resume that may never have actually started
    (e.g. a prior confirm's stamp landed, then run_production_resume raised) — the busy guard
    above already proved no job is in flight, so a second call here is safe: cheap no-op on a
    finished board, real healing on a still-parked one (controller decision, 2026-08-06 — no
    rollback machinery, heal forward instead)."""
    calls = _fake_resume(monkeypatch)
    db = _db(tmp_path)
    asset_id = _seed(db, tmp_path)

    confirm_scene_selection(db, "sess-1", [2])
    board = Board.open(board_root_for(db, asset_id, "sess-1"))
    v1 = board.load("scene_selection").version  # type: ignore[union-attr]

    out = confirm_scene_selection(db, "sess-1", [2])

    assert out.get("already_current") is True
    assert out["job_id"] == "job-42", "the heal call's job_id must reach the caller"
    assert board.load("scene_selection").version == v1  # type: ignore[union-attr]
    assert calls == ["sess-1", "sess-1"], "the second call is the healing resume, not a skip"


def test_confirm_busy_returns_409(tmp_path: Path, monkeypatch: Any) -> None:
    calls = _fake_resume(monkeypatch)
    db = _db(tmp_path)
    asset_id = _seed(db, tmp_path)
    _seed_job(db, "sess-1", status="running")

    with pytest.raises(HTTPException) as exc:
        confirm_scene_selection(db, "sess-1", [2, 5])
    assert exc.value.status_code == 409

    assert calls == [], "a busy run must never get a second concurrent resume"
    board = Board.open(board_root_for(db, asset_id, "sess-1"))
    sel = board.load("scene_selection")
    assert isinstance(sel, SceneSelection)
    assert sel.confirmed_utc is None and sel.selected_scene_numbers == [], (
        "the selection must be untouched by a refused confirm"
    )


def test_confirm_busy_terminal_job_does_not_block(tmp_path: Path, monkeypatch: Any) -> None:
    """Positive control mirroring the executor's own I2 test: a TERMINAL latest job
    (succeeded) must not trip the busy guard — only queued/running blocks a confirm."""
    calls = _fake_resume(monkeypatch)
    db = _db(tmp_path)
    _seed(db, tmp_path)
    _seed_job(db, "sess-1", status="succeeded")

    out = confirm_scene_selection(db, "sess-1", [2])

    assert calls == ["sess-1"]
    assert out["job_id"]


def test_confirm_404s_on_unknown_session(tmp_path: Path) -> None:
    db = _db(tmp_path)

    with pytest.raises(HTTPException) as exc:
        confirm_scene_selection(db, "nope", [2])
    assert exc.value.status_code == 404


def test_confirm_409s_when_gate_disabled(tmp_path: Path) -> None:
    """An old-style board (scene_gate off, or never turned on) must refuse cleanly rather than
    let a scene pick land on a chain that was never gated."""
    db = _db(tmp_path)
    _seed(db, tmp_path, scene_gate=False, with_proposal=False)

    with pytest.raises(HTTPException) as exc:
        confirm_scene_selection(db, "sess-1", [2])
    assert exc.value.status_code == 409


def test_confirm_409s_without_a_proposal_yet(tmp_path: Path) -> None:
    """Gate on, but the team has not written a scene_selection artifact yet — nothing to
    confirm against."""
    db = _db(tmp_path)
    _seed(db, tmp_path, with_proposal=False)

    with pytest.raises(HTTPException) as exc:
        confirm_scene_selection(db, "sess-1", [2])
    assert exc.value.status_code == 409


# --- HTTP endpoint: wiring smoke test ---------------------------------------------------------


def test_confirm_endpoint_returns_202_with_selection(tmp_path: Path, monkeypatch: Any) -> None:
    from laura.main import create_app

    _fake_resume(monkeypatch)
    settings = Settings(workspace_root=tmp_path / "ws", token=_TOKEN, start_runner=False)
    app = create_app(settings)
    client = TestClient(app)
    db: Database = app.state.db
    _seed(db, tmp_path)

    r = client.post(
        "/production/sess-1/scene-selection:confirm",
        json={"scene_numbers": [2, 5]},
        headers=_H,
    )

    assert r.status_code == 202, r.text
    body = r.json()
    assert body["selected"] == [2, 5]
    assert body["job_id"]
