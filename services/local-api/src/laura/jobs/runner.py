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

from ..db import repos
from ..db.database import Database
from ..metrics import JOBS
from ..telemetry import span
from ..util import new_id, utcnow_iso
from .errors import LauraJobError, trace_from_exception

logger = logging.getLogger(__name__)

# A handler receives a JobContext and returns an optional JSON-serialisable result.
JobHandler = Callable[["JobContext"], "dict[str, Any] | None"]

# The one job kind that owns a row in another table (analysis_runs). jobs/queues.py already
# names every kind for routing; a finalizer registry would be more architecture than a single
# kind earns.
ANALYSIS_RUN_KIND = "analysis.run"

# One predicate for the expired-lease rows, shared by the SELECT that classifies them and the
# two UPDATEs that act on them -- so the classification can never drift from the action.
_EXPIRED_LEASE = (
    "status IN ('leased','running') AND lease_expires_at IS NOT NULL AND lease_expires_at < ?"
)


def analysis_run_id_from_payload(payload_json: str | None) -> str | None:
    """The analysis_runs row an ``analysis.run`` job owns, or None.

    analysis_runs has no job_id column; the link lives in the payload, which all three enqueue
    sites carry (api/analysis.py, ingest/handlers.py, mcp/tools.py). Parsed in Python rather
    than with json_extract so it works on SQLite and Postgres alike.
    """
    try:
        payload = json.loads(payload_json or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    run_id = payload.get("analysis_run_id")
    return str(run_id) if run_id else None


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


def job_failure_from_result(result: object) -> str | None:
    """The failure a handler reported in its return value, or None if it reported none.

    The runner used to treat "the handler returned" as "the work succeeded", so a production run
    that died on a missing API key was written to the jobs table as ``succeeded`` — result_json
    saying ``hard_fail`` right beside it, and the Prometheus counter agreeing with the status.

    The tempting discriminator, ``ok is False``, is wrong: ``shorts.embed_frames`` returns
    ``{"ok": False, "skipped": "no visual backend"}`` as its normal outcome on any install
    without the optional visual extra — the default here — and marking those failed would break
    healthy installs to fix a reporting bug. The handlers already separate the two cases: a
    graceful skip carries ``skipped`` and no ``error``. So the error is the signal.
    """
    if not isinstance(result, dict):
        return None
    error = result.get("error")
    if isinstance(error, str) and error.strip():
        return error
    if result.get("status") == "hard_fail":
        summary = result.get("summary")
        return str(summary) if summary else "the run hard-failed"
    return None


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
        """Requeue or fail jobs whose lease expired. Returns count of jobs touched.

        An ``analysis.run`` job that runs out of attempts here also finalizes its
        analysis_runs row. The jobs table has always had this reaper; analysis_runs never did,
        so a SIGKILLed worker -- or the runtime cap below, which stops the heartbeat while the
        handler thread lives on and raises nothing -- left the run saying 'running' forever.
        A *requeued* job is deliberately left alone: the retry re-stamps the row through
        repos.start_analysis_run.
        """
        now = utcnow_iso()
        err = json.dumps({"error": "lease expired, max attempts reached"})
        stranded: list[tuple[str, str]] = []  # (job_id, analysis_run_id)
        with self.db.transaction(immediate=True) as conn:
            for row in conn.execute(
                f"SELECT id, kind, payload_json, attempt, max_attempts FROM jobs "
                f"WHERE {_EXPIRED_LEASE}",
                (now,),
            ).fetchall():
                if str(row["kind"]) != ANALYSIS_RUN_KIND:
                    continue
                if int(row["attempt"]) < int(row["max_attempts"]):
                    continue  # requeued below -- the retry re-stamps the run
                run_id = analysis_run_id_from_payload(row["payload_json"])
                if run_id is not None:
                    stranded.append((str(row["id"]), run_id))
            failed = conn.execute(
                f"UPDATE jobs SET status='failed', finished_at=?, updated_at=?, error_json=? "
                f"WHERE {_EXPIRED_LEASE} AND attempt >= max_attempts",
                (now, now, err, now),
            ).rowcount
            requeued = conn.execute(
                f"UPDATE jobs SET status='queued', worker_id=NULL, lease_expires_at=NULL, "
                f"updated_at=? WHERE {_EXPIRED_LEASE} AND attempt < max_attempts",
                (now, now),
            ).rowcount
        # Outside the transaction: a second write connection inside a held SQLite write lock
        # deadlocks. A crash in the gap leaves the run stranded -- which the startup sweep
        # (analysis/recovery.py) heals.
        for job_id, run_id in stranded:
            if repos.fail_stranded_analysis_run(
                self.db,
                run_id,
                error=f"job {job_id}: lease expired, max attempts reached",
                recovered_by="job reaper",
            ):
                logger.warning(
                    "analysis run %s finalized as failed: job %s ran out of attempts",
                    run_id, job_id,
                )
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

    def _finish_cancelled(self, job_id: str, result: dict[str, Any] | None) -> None:
        now = utcnow_iso()
        with self.db.connection() as conn:
            conn.execute(
                "UPDATE jobs SET status='cancelled', result_json=?, finished_at=?, "
                "updated_at=? WHERE id=?",
                (json.dumps(result or {}), now, now, job_id),
            )

    def _store_result(self, job_id: str, result: dict[str, Any] | None) -> None:
        """Keep the handler's payload without claiming the job succeeded.

        A handler that reports its own failure still returns the context needed to diagnose it
        (the production board, the stage it died in, the summary). That belongs in result_json
        whichever way the job ends; _finish_fail writes only error_json.
        """
        with self.db.connection() as conn:
            conn.execute(
                "UPDATE jobs SET result_json=?, updated_at=? WHERE id=?",
                (json.dumps(result or {}), utcnow_iso(), job_id),
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
                if isinstance(result, dict) and result.get("status") == "cancelled":
                    sp.set_attribute("job.status", "cancelled")
                    self._finish_cancelled(str(job["id"]), result)
                    JOBS.labels(kind, "cancelled").inc()
                    return
                failure = job_failure_from_result(result)
                if failure is None:
                    sp.set_attribute("job.status", "succeeded")
                    self._finish_ok(str(job["id"]), result)
                    JOBS.labels(kind, "succeeded").inc()
                else:
                    # "The handler returned" is not "the work succeeded". A production run that
                    # died on a missing API key returned its failure as data and was recorded as
                    # a succeeded job for 55 minutes, metric included. The payload is still
                    # written first: it carries the board state and the summary that make the
                    # failure diagnosable, and _finish_fail only touches error_json.
                    sp.set_attribute("job.status", "failed")
                    self._store_result(str(job["id"]), result)
                    self._finish_fail(
                        job,
                        LauraJobError(
                            failure, code="handler_reported_failure", retriable=False
                        ),
                    )
                    JOBS.labels(kind, "failed").inc()
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
