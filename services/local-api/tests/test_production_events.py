"""GET /production/{sid}/events — the run NDJSON as a pollable stream (spec 2026-08-03).

The agent events have been on disk since v2; nothing served them, so the UI could only say
"running…". Cursor is a 0-based line index; unparsable lines are skipped but counted.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.main import create_app

_TOKEN = "test-token"
_H = {"X-Laura-Token": _TOKEN}


def _app(tmp_path: Path) -> tuple[TestClient, Any, Settings]:
    settings = Settings(workspace_root=tmp_path / "ws", token=_TOKEN, start_runner=False)
    app = create_app(settings)
    return TestClient(app), app.state.db, settings


def _seed(db: Any, settings: Settings, tmp_path: Path) -> str:
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(settings.workspace_root / "project-x"),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="a.mp4", source_path=str(tmp_path / "a.mp4"),
    )
    repos.create_production_session(
        db, session_id="sess1", asset_id=str(asset["id"]),
        created_utc="2026-08-03T10:00:00Z",
    )
    return str(asset["id"])


def _write_run_log(
    db: Any, asset_id: str, lines: list[dict[str, Any] | str]
) -> None:
    # board_root_for is the authority on the layout — derive the runs dir from it rather than
    # re-guessing the project path by hand, so the fixture can never drift from what the
    # endpoint itself resolves.
    from laura.short_creator.production_orchestrator import board_root_for

    runs = board_root_for(db, asset_id, "sess1").parent / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(
        line if isinstance(line, str) else json.dumps(line) for line in lines
    )
    (runs / "20260803T100000Z.ndjson").write_text(payload, encoding="utf-8")


def test_events_cursor_and_done(tmp_path: Path) -> None:
    client, db, settings = _app(tmp_path)
    asset_id = _seed(db, settings, tmp_path)
    _write_run_log(db, asset_id, [
        {"type": "meta", "session_id": "sess1"},
        {"type": "agent", "agent": "vision_reviewer", "text": "scene 1 ok"},
        "NOT-JSON",
        {"type": "done", "ok": True},
    ])

    first = client.get("/production/sess1/events?after=0", headers=_H).json()
    assert [e["type"] for e in first["events"]] == ["meta", "agent", "done"]
    assert first["next"] == 4, "cursor counts the unparsable line too"
    assert first["done"] is True

    tail = client.get("/production/sess1/events?after=4", headers=_H).json()
    assert tail == {"events": [], "next": 4, "done": True}


def test_events_before_any_run_is_empty_not_500(tmp_path: Path) -> None:
    client, db, settings = _app(tmp_path)
    _seed(db, settings, tmp_path)
    body = client.get("/production/sess1/events?after=0", headers=_H).json()
    assert body == {"events": [], "next": 0, "done": False}


def test_events_unknown_session_404(tmp_path: Path) -> None:
    client, _db, _settings = _app(tmp_path)
    assert client.get("/production/nope/events", headers=_H).status_code == 404
