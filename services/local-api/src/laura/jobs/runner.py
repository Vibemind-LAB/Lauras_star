"""DB-based job runner: claim -> run -> succeed/fail, with lease + reaper.

Lifecycle (docs/05-workers-queue.md):
    queued -> running -> succeeded | failed | canceled
A lease (``lease_expires_at``) plus a reaper makes crashes recoverable: a job whose
worker died is requeued once its lease expires (until ``max_attempts``).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ..db.database import Database
from ..metrics import JOBS
from ..telemetry import span
from ..util import new_id, utcnow_iso

# A handler receives a JobContext and returns an optional JSON-serialisable result.
JobHandler = Callable[["JobContext"], "dict[str, Any] | None"]


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class JobContext:
    """Passed to a handler. Exposes the payload and a heartbeat to extend the lease."""

    job_id: str
    kind: str
    queue: str
    payload: dict[str, Any]
    db: Database
    lease_seconds: int = 60

    def heartbeat(self) -> None:
        expires = _iso(_now() + timedelta(seconds=self.lease_seconds))
        with self.db.connection() as conn:
            conn.execute(
                "UPDATE jobs SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ? "
                "WHERE id = ?",
                (utcnow_iso(), expires, utcnow_iso(), self.job_id),
            )


def enqueue(
    db: Database,
    *,
    queue: str,
    kind: str,
    payload: dict[str, Any] | None = None,
    priority: int = 0,
    idempotency_key: str | None = None,
    caused_by_job_id: str | None = None,
    max_attempts: int = 3,
    pipeline_version: str | None = None,
) -> str:
    """Insert a job and return its id. If ``idempotency_key`` matches an existing
    non-failed job, that job's id is returned instead of creating a duplicate."""
    now = utcnow_iso()
    with db.transaction(immediate=True) as conn:
        if idempotency_key is not None:
            row = conn.execute(
                "SELECT id, status FROM jobs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is not None and row["status"] in ("queued", "leased", "running", "succeeded"):
                return str(row["id"])
            if row is not None:
                # previous attempt failed/canceled — drop it so the key can be reused
                conn.execute("DELETE FROM jobs WHERE id = ?", (row["id"],))
        job_id = new_id()
        conn.execute(
            "INSERT INTO jobs (id, queue, kind, priority, payload_json, status, attempt, "
            "max_attempts, caused_by_job_id, pipeline_version, idempotency_key, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                queue,
                kind,
                priority,
                json.dumps(payload or {}),
                max_attempts,
                caused_by_job_id,
                pipeline_version,
                idempotency_key,
                now,
                now,
            ),
        )
        return job_id


class JobRunner:
    """Runs jobs from the DB. Use ``run_once`` for deterministic tests, or
    ``start``/``stop`` for a background polling thread."""

    def __init__(
        self,
        db: Database,
        registry: dict[str, JobHandler] | None = None,
        *,
        worker_id: str | None = None,
        lease_seconds: int = 60,
        poll_interval: float = 0.5,
        queues: tuple[str, ...] | None = None,
    ) -> None:
        self.db = db
        self.registry: dict[str, JobHandler] = registry or {}
        self.worker_id = worker_id or f"worker-{new_id()[:8]}"
        self.lease_seconds = lease_seconds
        self.poll_interval = poll_interval
        self.queues = queues  # None = all queues
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- registry ---------------------------------------------------------
    def register(self, kind: str, handler: JobHandler) -> None:
        self.registry[kind] = handler

    # --- reaper -----------------------------------------------------------
    def reap_expired(self) -> int:
        """Requeue or fail jobs whose lease expired. Returns count touched."""
        now = utcnow_iso()
        err = json.dumps({"error": "lease expired, max attempts reached"})
        with self.db.transaction(immediate=True) as conn:
            failed = conn.execute(
                "UPDATE jobs SET status='failed', finished_at=?, updated_at=?, error_json=? "
                "WHERE status IN ('leased','running') AND lease_expires_at IS NOT NULL "
                "AND lease_expires_at < ? AND attempt >= max_attempts",
                (now, now, err, now),
            ).rowcount
            requeued = conn.execute(
                "UPDATE jobs SET status='queued', worker_id=NULL, lease_expires_at=NULL, "
                "updated_at=? WHERE status IN ('leased','running') "
                "AND lease_expires_at IS NOT NULL AND lease_expires_at < ? "
                "AND attempt < max_attempts",
                (now, now),
            ).rowcount
        return int(failed) + int(requeued)

    # --- execute ----------------------------------------------------------
    def _finish_ok(self, job_id: str, result: dict[str, Any] | None) -> None:
        now = utcnow_iso()
        with self.db.connection() as conn:
            conn.execute(
                "UPDATE jobs SET status='succeeded', result_json=?, finished_at=?, "
                "updated_at=? WHERE id=?",
                (json.dumps(result or {}), now, now, job_id),
            )

    def _finish_fail(self, job: dict[str, Any], error: str) -> None:
        now = utcnow_iso()
        attempt = int(job["attempt"]) + 1  # already incremented at claim
        retriable = attempt < int(job["max_attempts"])
        with self.db.connection() as conn:
            if retriable:
                conn.execute(
                    "UPDATE jobs SET status='queued', worker_id=NULL, lease_expires_at=NULL, "
                    "error_json=?, updated_at=? WHERE id=?",
                    (json.dumps({"error": error}), now, job["id"]),
                )
            else:
                conn.execute(
                    "UPDATE jobs SET status='failed', error_json=?, finished_at=?, "
                    "updated_at=? WHERE id=?",
                    (json.dumps({"error": error}), now, now, job["id"]),
                )

    def _execute(self, job: dict[str, Any]) -> None:
        kind = str(job["kind"])
        with span("job.execute", **{"job.kind": kind, "job.queue": str(job["queue"])}) as sp:
            handler = self.registry.get(kind)
            if handler is None:
                sp.set_attribute("job.status", "failed")
                self._finish_fail(job, f"no handler registered for kind={kind!r}")
                JOBS.labels(kind, "failed").inc()
                return
            ctx = JobContext(
                job_id=str(job["id"]),
                kind=kind,
                queue=str(job["queue"]),
                payload=json.loads(job["payload_json"] or "{}"),
                db=self.db,
                lease_seconds=self.lease_seconds,
            )
            try:
                result = handler(ctx)
            except Exception as exc:  # noqa: BLE001 - we record any handler failure
                sp.set_attribute("job.status", "failed")
                self._finish_fail(job, f"{type(exc).__name__}: {exc}")
                JOBS.labels(kind, "failed").inc()
                return
            sp.set_attribute("job.status", "succeeded")
            self._finish_ok(str(job["id"]), result)
            JOBS.labels(kind, "succeeded").inc()

    def run_once(self) -> bool:
        """Reap, then claim and run at most one job. Returns True if a job ran."""
        self.reap_expired()
        job = self.db.claim_job(
            worker_id=self.worker_id, lease_seconds=self.lease_seconds, queues=self.queues
        )
        if job is None:
            return False
        self._execute(job)
        return True

    # --- background thread ------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                ran = self.run_once()
            except Exception:  # noqa: BLE001 - never let the loop die
                ran = False
            if not ran:
                self._stop.wait(self.poll_interval)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="laura-job-runner", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None


# --- default handlers -----------------------------------------------------
def _handle_echo(ctx: JobContext) -> dict[str, Any]:
    """Trivial handler used for smoke tests and as a registry placeholder."""
    return {"echo": ctx.payload}


def default_registry() -> dict[str, JobHandler]:
    """The handler registry. Ingest/analysis/export handlers are added in later
    portions; for now only a harmless echo handler is wired up."""
    return {"echo": _handle_echo}
