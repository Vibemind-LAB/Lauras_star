"""Tests for the DB job runner: run, idempotency, missing handler, reaper."""

from __future__ import annotations

from laura.db import repos
from laura.db.database import Database
from laura.jobs import JobRunner, default_registry, enqueue


def _runner(db: Database) -> JobRunner:
    return JobRunner(db, default_registry(), lease_seconds=60)


def test_enqueue_and_run_echo(db: Database) -> None:
    job_id = enqueue(db, queue="maintenance.gc", kind="echo", payload={"a": 1})
    runner = _runner(db)
    assert runner.run_once() is True
    job = repos.get_job(db, job_id)
    assert job is not None
    assert job["status"] == "succeeded"
    assert '"echo"' in job["result_json"]


def test_run_once_returns_false_when_empty(db: Database) -> None:
    assert _runner(db).run_once() is False


def test_idempotency_key_reuses_job(db: Database) -> None:
    a = enqueue(db, queue="ingest.io", kind="echo", idempotency_key="k1")
    b = enqueue(db, queue="ingest.io", kind="echo", idempotency_key="k1")
    assert a == b
    # after success, the same key still maps to the same job (cached result reuse)
    assert _runner(db).run_once() is True
    c = enqueue(db, queue="ingest.io", kind="echo", idempotency_key="k1")
    assert c == a


def test_missing_handler_fails(db: Database) -> None:
    job_id = enqueue(db, queue="x", kind="no_such_kind", max_attempts=1)
    assert _runner(db).run_once() is True
    job = repos.get_job(db, job_id)
    assert job is not None
    assert job["status"] == "failed"
    assert "no handler" in job["error_json"]


def test_cancelled_handler_result_is_terminal_cancelled(db: Database) -> None:
    """A cooperative cancellation result must never be persisted as success or failure."""
    result = {"status": "cancelled", "reason": "user"}
    job_id = enqueue(db, queue="x", kind="cancel-test")
    runner = JobRunner(
        db,
        {"cancel-test": lambda _ctx: result},
        lease_seconds=60,
    )

    assert runner.run_once() is True

    job = repos.get_job(db, job_id)
    assert job is not None
    assert job["status"] == "cancelled"
    assert job["finished_at"] is not None
    assert job["result_json"] is not None
    assert '"reason": "user"' in job["result_json"]
    assert job["error_json"] is None


def test_reaper_requeues_then_fails(db: Database) -> None:
    job_id = enqueue(db, queue="x", kind="echo", max_attempts=3)
    past = "2000-01-01T00:00:00.000000Z"
    runner = _runner(db)

    # Simulate a crashed worker: running, expired lease, attempt below max.
    with db.connection() as conn:
        conn.execute(
            "UPDATE jobs SET status='running', attempt=1, lease_expires_at=? WHERE id=?",
            (past, job_id),
        )
    assert runner.reap_expired() == 1
    job = repos.get_job(db, job_id)
    assert job is not None and job["status"] == "queued"

    # Now exhaust attempts: running, expired lease, attempt == max.
    with db.connection() as conn:
        conn.execute(
            "UPDATE jobs SET status='running', attempt=3, lease_expires_at=? WHERE id=?",
            (past, job_id),
        )
    assert runner.reap_expired() == 1
    job = repos.get_job(db, job_id)
    assert job is not None and job["status"] == "failed"
