from __future__ import annotations

import json

from fastapi.testclient import TestClient

from laura.db import repos
from laura.db.database import Database
from laura.jobs.runner import enqueue


def test_list_jobs_returns_newest_first(client: TestClient, db: Database) -> None:
    first = enqueue(db, queue="q", kind="echo", payload={"n": 1})
    second = enqueue(db, queue="q", kind="echo", payload={"n": 2})

    response = client.get("/jobs")

    assert response.status_code == 200
    ids = [row["id"] for row in response.json()]
    assert ids.index(second) < ids.index(first)


def test_cancel_queued_job_marks_it_cancelled(client: TestClient, db: Database) -> None:
    job_id = enqueue(db, queue="q", kind="echo", payload={"n": 1})

    response = client.post(f"/jobs/{job_id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    job = repos.get_job(db, job_id)
    assert job is not None
    assert job["cancel_requested"] == 1


def test_retry_failed_job_enqueues_copy(client: TestClient, db: Database) -> None:
    job_id = enqueue(db, queue="q", kind="echo", payload={"n": 1})
    with db.connection() as conn:
        conn.execute(
            "UPDATE jobs SET status='failed', error_json=? WHERE id=?",
            (json.dumps({"error": "boom"}), job_id),
        )

    response = client.post(f"/jobs/{job_id}/retry")

    assert response.status_code == 202, response.text
    retry_id = response.json()["job_id"]
    retry = repos.get_job(db, retry_id)
    assert retry is not None
    assert retry["kind"] == "echo"
    assert json.loads(retry["payload_json"]) == {"n": 1}
    assert retry["caused_by_job_id"] == job_id
