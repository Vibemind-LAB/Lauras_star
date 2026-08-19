"""Author writes go through the SAME tool closures as the team — authoring.py only
resolves session→board, guards, and dispatches by name. Guard depth (capacity, grounding,
gate arming) is covered by the production_tools suites; here we prove dispatch + mapping."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from laura.api.short_creator import _create_author_production_session
from laura.db import repos
from laura.jobs.runner import enqueue
from laura.short_creator.authoring import call_production_tool

# _db/_seed_asset: same seeding prelude as test_api_author_mode.py — imported via the
# relative package import tests/__init__.py documents (`from ._flaky_http import serve`),
# rather than duplicated, so the two suites can never drift apart on how a session is seeded.
from .test_api_author_mode import _db, _seed_asset


def test_unknown_session_404s(tmp_path: Path) -> None:
    db = _db(tmp_path)
    with pytest.raises(HTTPException) as exc:
        call_production_tool(db, "nope", "save_storyline", red_thread="x", chapters=[])
    assert exc.value.status_code == 404


def test_team_session_refuses_author_writes(tmp_path: Path) -> None:
    db = _db(tmp_path)
    asset_id = _seed_asset(db, tmp_path)
    # team-shaped board: author defaults to "team"
    from datetime import UTC, datetime

    from laura.short_creator.board import Board
    from laura.short_creator.board_models import BoardMeta
    from laura.short_creator.production_orchestrator import board_root_for
    session_id = "team-sess"
    repos.create_production_session(
        db, session_id=session_id, asset_id=asset_id,
        created_utc=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    Board.create(
        board_root_for(db, asset_id, session_id),
        BoardMeta(session_id=session_id, asset_id=asset_id,
                  created_utc="2026-08-19T00:00:00+00:00", task="t", target_seconds=30.0),
    )
    with pytest.raises(HTTPException) as exc:
        call_production_tool(db, session_id, "save_storyline", red_thread="x", chapters=[])
    assert exc.value.status_code == 409
    assert "team session" in str(exc.value.detail)


def test_busy_session_409s(tmp_path: Path) -> None:
    db = _db(tmp_path)
    asset_id = _seed_asset(db, tmp_path)
    session_id = _create_author_production_session(
        db, asset_id, task="t", target_seconds=30.0, format="insta", language="German",
    )
    job_id = enqueue(db, queue="production", kind="production.run", payload={}, max_attempts=1)
    repos.set_production_session_job(db, session_id, job_id)
    with pytest.raises(HTTPException) as exc:
        call_production_tool(db, session_id, "save_storyline", red_thread="x", chapters=[])
    assert exc.value.status_code == 409


def test_tool_level_rejection_maps_to_422(tmp_path: Path) -> None:
    # No rough-cut scenes on the asset → propose_scene_selection's own block reason fires;
    # the dispatch must surface it as 422 with the tool's reason, not swallow it.
    db = _db(tmp_path)
    asset_id = _seed_asset(db, tmp_path)
    session_id = _create_author_production_session(
        db, asset_id, task="t", target_seconds=30.0, format="insta", language="German",
    )
    with pytest.raises(HTTPException) as exc:
        call_production_tool(
            db, session_id, "propose_scene_selection",
            candidates=[{"scene_number": 1, "reason": "hook"}],
        )
    assert exc.value.status_code == 422


def test_unknown_tool_name_404s(tmp_path: Path) -> None:
    db = _db(tmp_path)
    asset_id = _seed_asset(db, tmp_path)
    session_id = _create_author_production_session(
        db, asset_id, task="t", target_seconds=30.0, format="insta", language="German",
    )
    with pytest.raises(HTTPException) as exc:
        call_production_tool(db, session_id, "render_production")  # not author-callable
    assert exc.value.status_code == 404
