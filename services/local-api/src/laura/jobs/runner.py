"""DB-based job runner: claim -> run -> succeed/fail, with lease + reaper.

Lifecycle (docs/05-workers-queue.md):
    queued -> running -> succeeded | failed | canceled
A lease (``lease_expires_at``) plus a reaper makes crashes recoverable: a job whose
worker died is requeued once its lease expires (until ``max_attempts``).
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ..db.database import Database
from ..metrics import JOBS
from ..telemetry import span
from ..util import new_id, utcnow_iso
from .errors import trace_from_exception

logger = logging.getLogger(__name__)

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
        order_row = conn.execute(
            "SELECT COALESCE(MAX(created_order), 0) + 1 AS created_order FROM jobs"
        ).fetchone()
        created_order = int(order_row["created_order"])
        conn.execute(
            "INSERT INTO jobs (id, queue, kind, priority, payload_json, status, attempt, "
            "max_attempts, caused_by_job_id, pipeline_version, idempotency_key, created_order, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?, ?, ?, ?)",
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
                created_order,
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
        concurrency: int = 1,
        max_runtime_seconds: int = 3600,
        runtime_overrides: dict[str, int] | None = None,
    ) -> None:
        self.db = db
        self.registry: dict[str, JobHandler] = registry or {}
        self.worker_id = worker_id or f"worker-{new_id()[:8]}"
        self.lease_seconds = lease_seconds
        self.poll_interval = poll_interval
        self.queues = queues  # None = all queues
        self.concurrency = max(1, concurrency)
        self.max_runtime_seconds = max_runtime_seconds
        # Per-kind caps for work that legitimately outlives the global one (an agent team
        # runs for hours; an hour-long probe is a hang). Kinds not listed keep the global.
        self.runtime_overrides: dict[str, int] = dict(runtime_overrides or {})
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def runtime_limit_for(self, kind: str) -> int:
        """How long *kind* may run before its lease stops being refreshed."""
        return self.runtime_overrides.get(kind, self.max_runtime_seconds)

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

    def _finish_fail(self, job: dict[str, Any], error: BaseException | str) -> None:
        now = utcnow_iso()
        attempt = int(job["attempt"]) + 1  # already incremented at claim

        # Build a structured trace from whatever error type we received.
        if isinstance(error, str):
            # Plain string path (reap_expired, "no handler registered") — wrap
            # exactly as bare exceptions so requeue logic is unchanged.
            trace: dict[str, Any] = {
                "error": error, "code": "unknown", "retriable": True, "details": None
            }
        else:
            trace = trace_from_exception(error)

        # Requeue only when BOTH the attempt budget allows it AND the error
        # is retriable.  A LauraJobError with retriable=False ends permanently
        # even when attempts remain.
        should_requeue = (attempt < int(job["max_attempts"])) and bool(trace["retriable"])

        with self.db.connection() as conn:
            if should_requeue:
                conn.execute(
                    "UPDATE jobs SET status='queued', worker_id=NULL, lease_expires_at=NULL, "
                    "error_json=?, updated_at=? WHERE id=?",
                    (json.dumps(trace), now, job["id"]),
                )
            else:
                conn.execute(
                    "UPDATE jobs SET status='failed', error_json=?, finished_at=?, "
                    "updated_at=? WHERE id=?",
                    (json.dumps(trace), now, now, job["id"]),
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
            # Keep the lease fresh for the whole (possibly minutes-long) handler so a
            # concurrent worker's reaper never requeues an in-flight job. This decouples
            # liveness from handler internals — no handler needs to call heartbeat itself.
            stop_hb = threading.Event()
            hb_started = time.monotonic()

            runtime_limit = self.runtime_limit_for(kind)

            def _heartbeat_loop() -> None:
                interval = max(1.0, self.lease_seconds / 2)
                while not stop_hb.wait(interval):
                    if time.monotonic() - hb_started > runtime_limit:
                        logger.warning(
                            "job %s (%s) exceeded max runtime %ss; ceasing heartbeat so "
                            "the reaper can recover it", job["id"], kind, runtime_limit,
                        )
                        return
                    with contextlib.suppress(Exception):  # heartbeat is best-effort
                        ctx.heartbeat()

            hb = threading.Thread(
                target=_heartbeat_loop, name=f"hb-{str(job['id'])[:8]}", daemon=True
            )
            hb.start()
            # The terminal DB write (finish_ok/finish_fail) runs INSIDE the try so the
            # heartbeat keeps the lease fresh until the job is marked terminal — otherwise
            # a concurrent worker's reaper could requeue the job in the gap between the
            # heartbeat stopping and the status write, causing a double-run.
            try:
                result = handler(ctx)
                sp.set_attribute("job.status", "succeeded")
                self._finish_ok(str(job["id"]), result)
                JOBS.labels(kind, "succeeded").inc()
            except Exception as exc:  # noqa: BLE001 - we record any handler failure
                sp.set_attribute("job.status", "failed")
                self._finish_fail(job, exc)
                JOBS.labels(kind, "failed").inc()
            finally:
                stop_hb.set()
                hb.join(timeout=2.0)

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
        if self._threads:
            return
        self._stop.clear()
        for i in range(self.concurrency):
            t = threading.Thread(target=self._loop, name=f"laura-job-runner-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        deadline = time.monotonic() + timeout
        for t in self._threads:
            t.join(timeout=max(0.0, deadline - time.monotonic()))
        self._threads = []


# --- default handlers -----------------------------------------------------
def _handle_echo(ctx: JobContext) -> dict[str, Any]:
    """Trivial handler used for smoke tests and as a registry placeholder."""
    return {"echo": ctx.payload}


def default_registry() -> dict[str, JobHandler]:
    """The handler registry. Ingest/analysis/export handlers are added in later
    portions; for now only a harmless echo handler is wired up."""
    return {"echo": _handle_echo}
