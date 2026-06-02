"""Small shared utilities (ids, timestamps)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime


def new_id() -> str:
    """A compact, sortable-enough unique id (uuid4 hex)."""
    return uuid.uuid4().hex


def utcnow_iso() -> str:
    """Current UTC time as ISO-8601 with millisecond precision and trailing Z."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
