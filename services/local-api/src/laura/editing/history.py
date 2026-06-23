"""Undo/redo checkpoint helpers for the synchronous mutation routes.

Usage::

    with timeline_checkpoint(db, timeline_id, "Wörter gelöscht"):
        repos.replace_timeline_clips(db, timeline_id, new_rows)

The snapshot is taken *before* the mutation so the undo stack holds the
pre-edit state (Task 3 / Task 5 interfaces).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from laura.db import repos


@contextmanager
def timeline_checkpoint(db: Any, timeline_id: str, label: str) -> Iterator[None]:
    """Snapshot the timeline's pre-edit editorial state onto the undo stack,
    then run the mutation."""
    repos.push_undo_checkpoint(db, timeline_id, label)
    yield
