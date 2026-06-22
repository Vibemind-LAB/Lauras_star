"""Small shared utilities (ids, timestamps)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from threading import Lock

_last_utcnow: datetime | None = None
_utcnow_lock = Lock()


def new_id() -> str:
    """A compact, sortable-enough unique id (uuid4 hex)."""
    return uuid.uuid4().hex


def utcnow_iso() -> str:
    """Current UTC time as monotonic ISO-8601 with microsecond precision and trailing Z."""
    global _last_utcnow
    with _utcnow_lock:
        now = datetime.now(UTC)
        if _last_utcnow is not None and now <= _last_utcnow:
            now = _last_utcnow + timedelta(microseconds=1)
        _last_utcnow = now
        return now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
