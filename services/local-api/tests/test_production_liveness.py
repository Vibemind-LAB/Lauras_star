"""GET /production/{sid} must reveal whether the run is alive.

Live incident: a production run died in its first seconds. GET /production/{sid} kept returning
a serene board — meta "active", a resume point, an artifact count — with no way to tell it from
a run still working. It went unnoticed for 55 minutes. The job row knew the whole time (it was
the authority the eventual diagnosis came from), but the status endpoint never looked at it.

The session now records its job, and the status endpoint reports that job's state and heartbeat.
A hanging run is a running job whose lease has expired; a dead run is a failed job. Both are now
answerable from the one endpoint an operator polls.
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


def _app(tmp_path: Path) -> tuple[TestClient, Any]:
    settings = Settings(workspace_root=tmp_path / "ws", token=_TOKEN, start_runner=False)
    app = create_app(settings)
    return TestClient(app), app.state.db


def _seed_asset(db: Any) -> str:
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a", source_path="/tmp/a.mp4"
    )
    return str(asset["id"])


def _start(client: TestClient, asset_id: str) -> str:
    r = client.post(
        f"/assets/{asset_id}/production",
        json={"task": "a recap", "target_seconds": 30},
        headers=_H,
    )
    assert r.status_code == 202, r.text
    return str(r.json()["session_id"])


def _latest_job_id(db: Any, session_id: str) -> str:
    session = repos.get_production_session(db, session_id)
    assert session is not None
    return str(session["latest_job_id"])


def test_the_session_records_the_job_that_runs_it(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    client, db = _app(tmp_path)
    asset_id = _seed_asset(db)

    session_id = _start(client, asset_id)

    session = repos.get_production_session(db, session_id)
    assert session is not None
    assert session["latest_job_id"], "a session with no job link cannot report liveness"


def test_the_status_endpoint_reports_the_job_state(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    client, db = _app(tmp_path)
    asset_id = _seed_asset(db)
    session_id = _start(client, asset_id)

    body = client.get(f"/production/{session_id}", headers=_H).json()

    assert "job" in body, "the one field the incident needed and did not have"
    assert body["job"]["status"] == "queued"
    assert body["job"]["updated_at"], "the heartbeat an operator watches for progress"


def test_a_failed_job_is_visible_at_the_status_endpoint(tmp_path: Path, monkeypatch: Any) -> None:
    """The incident exactly: the run died, and now the endpoint says so."""
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    client, db = _app(tmp_path)
    asset_id = _seed_asset(db)
    session_id = _start(client, asset_id)
    job_id = _latest_job_id(db, session_id)

    # The job hard-fails, the way the live one did.
    with db.transaction() as conn:
        conn.execute(
            "UPDATE jobs SET status='failed', error_json=?, updated_at='2026-07-18T09:27:00+00:00' "
            "WHERE id=?",
            ('{"error": "Connection error."}', job_id),
        )

    body = client.get(f"/production/{session_id}", headers=_H).json()

    assert body["job"]["status"] == "failed"


def test_the_status_endpoint_surfaces_what_a_resume_restored(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """``run_production`` always writes ``restored`` into its result (the provenance-chain
    restore); the status endpoint must surface it so the UI can show a "restored" chip instead
    of leaving a resume that brought back archived artifacts indistinguishable from a fresh run.
    """
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    client, db = _app(tmp_path)
    asset_id = _seed_asset(db)
    session_id = _start(client, asset_id)
    job_id = _latest_job_id(db, session_id)

    with db.transaction() as conn:
        conn.execute(
            "UPDATE jobs SET status='succeeded', result_json=?, updated_at=?, finished_at=? "
            "WHERE id=?",
            (
                json.dumps({"ok": True, "restored": ["voice", "cutlist"]}),
                "2026-07-20T09:00:00+00:00",
                "2026-07-20T09:00:00+00:00",
                job_id,
            ),
        )

    body = client.get(f"/production/{session_id}", headers=_H).json()

    assert body["job"]["restored"] == ["voice", "cutlist"]


def test_a_job_with_no_result_reports_no_restored_artifacts(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A queued job has no ``result_json`` yet; the field must default to empty, never raise."""
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    client, db = _app(tmp_path)
    asset_id = _seed_asset(db)
    session_id = _start(client, asset_id)

    body = client.get(f"/production/{session_id}", headers=_H).json()

    assert body["job"]["restored"] == []


def test_a_follow_up_message_becomes_the_session_s_current_job(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Liveness must track the LATEST run, not the first — a vibe-edit is a new job."""
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    client, db = _app(tmp_path)
    asset_id = _seed_asset(db)
    session_id = _start(client, asset_id)
    first_job = _latest_job_id(db, session_id)

    # A follow-up needs a board; the first run never built one here, so this 404s and must NOT
    # move the job link. (The board-present path is covered by the message endpoint's own tests.)
    client.post(f"/production/{session_id}/message", json={"text": "make it punchier"}, headers=_H)

    still = _latest_job_id(db, session_id)
    assert still == first_job, "a rejected follow-up must not repoint liveness"


# --- enqueue response carries config_warnings ------------------------------------------------


def test_production_enqueue_response_carries_config_warnings(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The 202 must say out loud when the text agents will run on a local ollama model."""
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    monkeypatch.delenv("LAURA_AGENT_PROVIDER", raising=False)  # -> ollama default
    client, db = _app(tmp_path)
    asset_id = _seed_asset(db)

    r = client.post(
        f"/assets/{asset_id}/production",
        json={"task": "a recap", "target_seconds": 30},
        headers=_H,
    )

    assert r.status_code == 202, r.text
    body = r.json()
    assert isinstance(body["warnings"], list) and len(body["warnings"]) == 1
    assert "ollama" in body["warnings"][0]


def test_production_enqueue_response_warnings_empty_for_hosted(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    monkeypatch.setenv("LAURA_AGENT_PROVIDER", "openai-compat")
    monkeypatch.setenv("LAURA_AGENT_API_KEY", "k")
    client, db = _app(tmp_path)
    asset_id = _seed_asset(db)

    r = client.post(
        f"/assets/{asset_id}/production",
        json={"task": "a recap", "target_seconds": 30},
        headers=_H,
    )

    assert r.status_code == 202, r.text
    assert r.json()["warnings"] == []
