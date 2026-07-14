"""POST /assets/{asset_id}/production — creates a v2 production session + enqueues production.run.

Mirrors test_shorts_render_api.py's app-factory + token-header pattern. The job is NOT drained
here (no real LLM/orchestrator call) — we assert the session row and the queued job's kind +
payload instead. ``_autoshort_available`` is monkeypatched so tests never depend on whether the
optional 'autoshort' extra is actually installed (see test_short_creator_api.py).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app

_TOKEN = "test-token"
_H = {"X-Laura-Token": _TOKEN}


def _app(tmp_path: Path) -> tuple[TestClient, SqliteDatabase]:
    settings = Settings(workspace_root=tmp_path / "ws", token=_TOKEN, start_runner=False)
    app = create_app(settings)
    db: SqliteDatabase = app.state.db
    return TestClient(app), db


def _seed_asset(db: SqliteDatabase) -> tuple[str, str]:
    """Project + one video asset. Returns (asset_id, project_id)."""
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="a", source_path="/tmp/a.mp4",
    )
    return asset["id"], project["id"]


def test_create_production_enqueues_job_and_creates_session(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client, db = _app(tmp_path)
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    asset_id, _project_id = _seed_asset(db)

    r = client.post(
        f"/assets/{asset_id}/production",
        json={"task": "Make a 30s recap", "target_seconds": 45},
        headers=_H,
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["session_id"]
    assert body["job_id"]

    # Session row exists.
    session = repos.get_production_session(db, body["session_id"])
    assert session is not None
    assert session["asset_id"] == asset_id

    # Job row exists, correct kind, payload carries session_id + the rest of the request.
    job = repos.get_job(db, body["job_id"])
    assert job is not None
    assert job["kind"] == "production.run"
    payload = json.loads(job["payload_json"])
    assert payload == {
        "asset_id": asset_id,
        "session_id": body["session_id"],
        "task": "Make a 30s recap",
        "target_seconds": 45,
    }


def test_create_production_unknown_asset_404(tmp_path: Path) -> None:
    client, _db = _app(tmp_path)
    r = client.post("/assets/does-not-exist/production", json={"task": "recap"}, headers=_H)
    assert r.status_code == 404, r.text


def test_create_production_empty_task_422(tmp_path: Path, monkeypatch: Any) -> None:
    client, db = _app(tmp_path)
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    asset_id, _project_id = _seed_asset(db)

    r = client.post(f"/assets/{asset_id}/production", json={"task": ""}, headers=_H)
    assert r.status_code == 422, r.text


def test_create_production_session_row_has_correct_asset_id(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client, db = _app(tmp_path)
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    asset_id, _project_id = _seed_asset(db)

    r = client.post(f"/assets/{asset_id}/production", json={"task": "recap"}, headers=_H)
    assert r.status_code == 202, r.text
    session_id = r.json()["session_id"]

    session = repos.get_production_session(db, session_id)
    assert session is not None
    assert session["session_id"] == session_id
    assert session["asset_id"] == asset_id
    assert session["created_utc"]
