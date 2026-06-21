"""TDD tests for P3-T3: typed LauraJobError + structured trace in error_json.

Covers:
  - Unit tests for LauraJobError.to_trace and trace_from_exception.
  - Runner integration: retriable=False → permanent fail even with attempts remaining.
  - Runner integration: retriable=True → requeued (attempt budget allows it).
  - Runner integration: bare ValueError → wrapped as code="unknown", retriable=True;
    requeue still follows attempt count (regression guard).
  - Backward-compat: substring match on "error" key still works.
"""

from __future__ import annotations

import json
from typing import Any

from laura.db import repos
from laura.db.database import Database
from laura.jobs import JobRunner, default_registry, enqueue
from laura.jobs.errors import LauraJobError, trace_from_exception

# ---------------------------------------------------------------------------
# Unit tests: LauraJobError.to_trace
# ---------------------------------------------------------------------------


def test_to_trace_permanent() -> None:
    """A permanent LauraJobError produces the right trace shape."""
    exc = LauraJobError("gone", code="consent_revoked", retriable=False)
    trace = exc.to_trace()
    assert trace == {
        "error": "gone",
        "code": "consent_revoked",
        "retriable": False,
        "details": None,
    }


def test_to_trace_retriable_with_details() -> None:
    """A retriable LauraJobError includes details and retriable=True."""
    exc = LauraJobError(
        "gpu oom", code="gpu_oom", retriable=True, details={"device": "cuda:0"}
    )
    trace = exc.to_trace()
    assert trace == {
        "error": "gpu oom",
        "code": "gpu_oom",
        "retriable": True,
        "details": {"device": "cuda:0"},
    }


def test_str_of_laura_job_error() -> None:
    """str(exc) == message (via super().__init__(message))."""
    exc = LauraJobError("gone", code="x", retriable=False)
    assert str(exc) == "gone"


# ---------------------------------------------------------------------------
# Unit tests: trace_from_exception
# ---------------------------------------------------------------------------


def test_trace_from_laura_job_error() -> None:
    """trace_from_exception delegates to LauraJobError.to_trace."""
    exc = LauraJobError("gone", code="consent_revoked", retriable=False)
    trace = trace_from_exception(exc)
    assert trace == {
        "error": "gone",
        "code": "consent_revoked",
        "retriable": False,
        "details": None,
    }


def test_trace_from_bare_value_error() -> None:
    """Bare exceptions wrap as code='unknown', retriable=True."""
    exc = ValueError("boom")
    trace = trace_from_exception(exc)
    assert trace["code"] == "unknown"
    assert trace["retriable"] is True
    assert trace["details"] is None
    assert trace["error"] == "ValueError: boom"


def test_trace_from_bare_runtime_error() -> None:
    """Same wrapping applies to any non-LauraJobError type."""
    exc = RuntimeError("transient GPU OOM")
    trace = trace_from_exception(exc)
    assert trace["error"] == "RuntimeError: transient GPU OOM"
    assert trace["code"] == "unknown"
    assert trace["retriable"] is True


# ---------------------------------------------------------------------------
# Runner integration helpers
# ---------------------------------------------------------------------------


def _runner(db: Database) -> JobRunner:
    return JobRunner(db, default_registry(), lease_seconds=60)


# ---------------------------------------------------------------------------
# Integration: retriable=False → permanent fail even with attempts remaining
# ---------------------------------------------------------------------------


def test_permanent_error_not_requeued(db: Database) -> None:
    """A handler raising LauraJobError(retriable=False) ends the job immediately.

    Even though max_attempts=3 and only one attempt has been consumed, the job
    must end in status='failed' (not requeued).
    """
    registry = default_registry()

    def _permanent_fail(ctx: Any) -> None:
        raise LauraJobError(
            "gone", code="consent_revoked", retriable=False
        )

    registry["fail.permanent"] = _permanent_fail
    runner = JobRunner(db, registry, lease_seconds=60)

    job_id = enqueue(db, queue="ai", kind="fail.permanent", max_attempts=3)
    assert runner.run_once() is True

    job = repos.get_job(db, job_id)
    assert job is not None
    assert job["status"] == "failed", (
        f"Expected 'failed' for retriable=False with attempts remaining, got {job['status']!r}"
    )
    trace = json.loads(job["error_json"])
    assert trace == {
        "error": "gone",
        "code": "consent_revoked",
        "retriable": False,
        "details": None,
    }


def test_permanent_error_json_has_error_key(db: Database) -> None:
    """Backward-compat: 'gone' is present as a substring in the raw error_json string."""
    registry = default_registry()

    def _permanent_fail(ctx: Any) -> None:
        raise LauraJobError("gone", code="consent_revoked", retriable=False)

    registry["fail.permanent2"] = _permanent_fail
    runner = JobRunner(db, registry, lease_seconds=60)

    job_id = enqueue(db, queue="ai", kind="fail.permanent2", max_attempts=3)
    runner.run_once()

    job = repos.get_job(db, job_id)
    assert job is not None
    # Substring match — backwards-compat guarantee
    assert "gone" in job["error_json"]


# ---------------------------------------------------------------------------
# Integration: retriable=True → requeued when attempts remain
# ---------------------------------------------------------------------------


def test_retriable_error_requeues(db: Database) -> None:
    """A handler raising LauraJobError(retriable=True) gets requeued when attempts remain."""
    registry = default_registry()

    def _retriable_fail(ctx: Any) -> None:
        raise LauraJobError("transient", code="gpu_oom", retriable=True)

    registry["fail.retriable"] = _retriable_fail
    runner = JobRunner(db, registry, lease_seconds=60)

    job_id = enqueue(db, queue="ai", kind="fail.retriable", max_attempts=3)
    assert runner.run_once() is True

    job = repos.get_job(db, job_id)
    assert job is not None
    assert job["status"] == "queued", (
        f"Expected 'queued' for retriable=True with attempts remaining, got {job['status']!r}"
    )
    trace = json.loads(job["error_json"])
    assert trace["code"] == "gpu_oom"
    assert trace["retriable"] is True


def test_retriable_error_exhausted_becomes_failed(db: Database) -> None:
    """A retriable error that exhausts all attempts ends in status='failed'."""
    registry = default_registry()

    def _retriable_fail(ctx: Any) -> None:
        raise LauraJobError("transient", code="gpu_oom", retriable=True)

    registry["fail.retriable.exhaust"] = _retriable_fail
    runner = JobRunner(db, registry, lease_seconds=60)

    job_id = enqueue(db, queue="ai", kind="fail.retriable.exhaust", max_attempts=1)
    assert runner.run_once() is True

    job = repos.get_job(db, job_id)
    assert job is not None
    assert job["status"] == "failed", (
        f"Expected 'failed' after exhausting max_attempts, got {job['status']!r}"
    )


# ---------------------------------------------------------------------------
# Integration: bare ValueError → wrapped, attempt-based requeue preserved
# ---------------------------------------------------------------------------


def test_bare_value_error_wrapped_and_requeued(db: Database) -> None:
    """Bare ValueError still wraps as code='unknown', retriable=True, and is requeued."""
    registry = default_registry()

    def _bare_fail(ctx: Any) -> None:
        raise ValueError("boom")

    registry["fail.bare"] = _bare_fail
    runner = JobRunner(db, registry, lease_seconds=60)

    job_id = enqueue(db, queue="ai", kind="fail.bare", max_attempts=3)
    assert runner.run_once() is True

    job = repos.get_job(db, job_id)
    assert job is not None
    assert job["status"] == "queued", (
        f"Expected 'queued' (bare ValueError, attempts remain), got {job['status']!r}"
    )
    trace = json.loads(job["error_json"])
    assert trace["error"] == "ValueError: boom"
    assert trace["code"] == "unknown"
    assert trace["retriable"] is True
    assert trace["details"] is None
    # Backward-compat: substring still works
    assert "boom" in job["error_json"]


def test_bare_value_error_exhausted_becomes_failed(db: Database) -> None:
    """A bare exception that exhausts all attempts ends in status='failed'."""
    registry = default_registry()

    def _bare_fail(ctx: Any) -> None:
        raise ValueError("boom")

    registry["fail.bare.exhaust"] = _bare_fail
    runner = JobRunner(db, registry, lease_seconds=60)

    job_id = enqueue(db, queue="ai", kind="fail.bare.exhaust", max_attempts=1)
    assert runner.run_once() is True

    job = repos.get_job(db, job_id)
    assert job is not None
    assert job["status"] == "failed"
    trace = json.loads(job["error_json"])
    assert trace["error"] == "ValueError: boom"


# ---------------------------------------------------------------------------
# Backward-compat: "no handler registered" path (plain string → _finish_fail)
# ---------------------------------------------------------------------------


def test_no_handler_still_fails(db: Database) -> None:
    """The 'no handler registered' plain-string path still results in status='failed'."""
    runner = _runner(db)
    job_id = enqueue(db, queue="x", kind="no_such_kind", max_attempts=1)
    assert runner.run_once() is True

    job = repos.get_job(db, job_id)
    assert job is not None
    assert job["status"] == "failed"
    trace = json.loads(job["error_json"])
    assert "no handler" in trace["error"]
    assert "no_such_kind" in trace["error"]
    # code/retriable/details still present with expected defaults
    assert trace["code"] == "unknown"
    assert trace["retriable"] is True


# ---------------------------------------------------------------------------
# Backward-compat: substring match (simulates test_lipsync_job.py pattern)
# ---------------------------------------------------------------------------


def test_backward_compat_substring_in_error_json(db: Database) -> None:
    """'<message>' in job['error_json'] still holds after the structured-trace change."""
    registry = default_registry()

    def _fail_with_message(ctx: Any) -> None:
        raise LauraJobError(
            "no face in selected range",
            code="probe_no_face",
            retriable=False,
        )

    registry["fail.face"] = _fail_with_message
    runner = JobRunner(db, registry, lease_seconds=60)

    job_id = enqueue(db, queue="ai", kind="fail.face", max_attempts=1)
    runner.run_once()

    job = repos.get_job(db, job_id)
    assert job is not None
    # This is the exact assertion pattern used in test_lipsync_job.py
    assert "no face in selected range" in job["error_json"]
