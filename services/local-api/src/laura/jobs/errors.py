"""Typed job failure primitives.

``LauraJobError`` lets a job handler signal a structured, possibly-permanent
failure.  ``trace_from_exception`` converts any exception (typed or bare) into
a flat dict suitable for storage in ``jobs.error_json``.

The ``"error"`` key in every trace MUST remain the human-readable message
string — existing consumers (e.g. test_lipsync_job.py) substring-match it.
"""

from __future__ import annotations

from typing import Any


class LauraJobError(Exception):
    """Typed job failure raised by a handler.

    Parameters
    ----------
    code:
        Machine-readable failure code (e.g. ``"consent_revoked"``).
    message:
        Human-readable description. Also the result of ``str(exc)``.
    retriable:
        ``True``  → the runner may requeue (subject to attempt limit).
        ``False`` → permanent failure; skip the requeue even if attempts remain.
    details:
        Optional free-form dict for structured context (stack snapshot, IDs…).
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        retriable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retriable = retriable
        self.details = details

    def to_trace(self) -> dict[str, Any]:
        """Return a JSON-serialisable trace dict.

        The ``"error"`` key carries the message string so that existing
        substring-match assertions (``"no face in selected range" in error_json``)
        continue to pass.
        """
        return {
            "error": self.message,
            "code": self.code,
            "retriable": self.retriable,
            "details": self.details,
        }


def trace_from_exception(exc: BaseException) -> dict[str, Any]:
    """Convert *any* exception to a trace dict.

    * ``LauraJobError``  → delegates to ``exc.to_trace()``.
    * Any other exception → wraps with ``code="unknown"`` and ``retriable=True``
      to preserve today's attempt-based requeue behaviour for bare exceptions.
    """
    if isinstance(exc, LauraJobError):
        return exc.to_trace()
    return {
        "error": f"{type(exc).__name__}: {exc}",
        "code": "unknown",
        "retriable": True,
        "details": None,
    }
