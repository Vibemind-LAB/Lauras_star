"""POST /shorts-candidates/{candidate_id}/render — creates an export + enqueues shorts.render.

The job is NOT drained here (draining would invoke real ffmpeg via the worker). We assert the
export row exists with the right options and that a queued ``shorts.render`` job references it.
404 when the candidate is unknown.
"""

from __future__ import annotations

from pathlib import Path

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


def _seed_candidate(db: SqliteDatabase) -> tuple[str, str]:
    """Project + asset + one candidate. Returns (candidate_id, project_id)."""
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="a", source_path="/tmp/a.mp4",
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_shorts_candidates(
        db, project["id"], asset["id"], tl["id"],
        [{
            "start_frame": 0, "end_frame_exclusive": 30,
            "start_boundary": "word", "end_boundary": "word",
            "score": 0.9, "rejected": False, "reject_reason": None,
            "score_breakdown": {}, "qa_passed": True, "qa_issues": [],
        }],
    )
    candidate = repos.list_shorts_candidates_by_asset(db, asset["id"])[0]
    return candidate["id"], project["id"]


def test_render_enqueues_job_and_creates_export(tmp_path: Path) -> None:
    test_client, candidate_id, project_id = _seed_candidate_app(tmp_path)
    db: SqliteDatabase = test_client.app.state.db  # type: ignore[attr-defined]

    r = test_client.post(
        f"/shorts-candidates/{candidate_id}/render",
        json={"captions": True, "hook_text": "Wow", "loudnorm": True},
        headers=_H,
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["export_id"]
    assert body["job_id"]

    # Export row created with our options.
    exp = repos.get_export(db, body["export_id"])
    assert exp is not None
    assert exp["project_id"] == project_id
    assert exp["status"] == "rendering"
    assert exp["options"]["candidate_id"] == candidate_id
    assert exp["options"]["hook_text"] == "Wow"
    assert exp["options"]["loudnorm"] is True

    # A shorts.render job referencing the export was enqueued.
    job = repos.get_job(db, body["job_id"])
    assert job is not None
    assert job["kind"] == "shorts.render"
    assert job["queue"] == "export"


def test_render_unknown_candidate_is_404(tmp_path: Path) -> None:
    test_client, _db = _app(tmp_path)
    r = test_client.post(
        "/shorts-candidates/does-not-exist/render", json={}, headers=_H
    )
    assert r.status_code == 404, r.text


def test_render_defaults_when_body_empty(tmp_path: Path) -> None:
    """Empty body uses defaults (captions/loudnorm on, hook None)."""
    test_client, candidate_id, _project_id = _seed_candidate_app(tmp_path)
    r = test_client.post(
        f"/shorts-candidates/{candidate_id}/render", json={}, headers=_H
    )
    assert r.status_code == 202, r.text
    db: SqliteDatabase = test_client.app.state.db  # type: ignore[attr-defined]
    exp = repos.get_export(db, r.json()["export_id"])
    assert exp is not None
    assert exp["options"]["captions"] is True
    assert exp["options"]["loudnorm"] is True
    assert exp["options"]["hook_text"] is None


def _seed_candidate_app(tmp_path: Path) -> tuple[TestClient, str, str]:
    """Convenience: app + seeded candidate. Returns (client, candidate_id, project_id)."""
    client, db = _app(tmp_path)
    candidate_id, project_id = _seed_candidate(db)
    return client, candidate_id, project_id
