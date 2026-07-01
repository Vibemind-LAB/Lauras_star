"""Generate-video endpoint (Axis 2, Slice 1) — enqueue a generate.video job.

POST /projects/{id}/generate-video validates the project + request and enqueues a background job
that produces a clip and registers it as a synthetic asset. Uses the conftest ``client``/``db``
fixtures (authenticated for timeline:edit). The job is not run here (``start_runner=False``).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from laura.db import repos
from laura.db.database import Database


def _project(db: Database) -> str:
    p = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    return str(p["id"])


def test_generate_video_enqueues_job(client: TestClient, db: Database) -> None:
    pid = _project(db)
    r = client.post(
        f"/projects/{pid}/generate-video", json={"prompt": "a calm ocean", "duration_frames": 90}
    )
    assert r.status_code == 202, r.text
    assert r.json()["job_id"]


def test_generate_video_unknown_project_404(client: TestClient) -> None:
    r = client.post("/projects/nope/generate-video", json={"prompt": "x", "duration_frames": 30})
    assert r.status_code == 404


def test_generate_video_invalid_duration_422(client: TestClient, db: Database) -> None:
    pid = _project(db)
    r = client.post(f"/projects/{pid}/generate-video", json={"prompt": "x", "duration_frames": 0})
    assert r.status_code == 422
