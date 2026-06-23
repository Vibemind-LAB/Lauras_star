"""Undo/redo checkpoint helpers for the synchronous mutation routes.

Usage::

    with timeline_checkpoint(db, timeline_id, "Wörter gelöscht"):
        repos.replace_timeline_clips(db, timeline_id, new_rows)

The snapshot is taken *before* the mutation so the undo stack holds the
pre-edit state (Task 3 / Task 5 interfaces).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from laura.db import repos
from laura.editing.otio_sync import rebuild_otio

logger = logging.getLogger(__name__)


@contextmanager
def timeline_checkpoint(db: Any, timeline_id: str, label: str) -> Iterator[None]:
    """Snapshot the timeline's pre-edit editorial state onto the undo stack,
    then run the mutation."""
    repos.push_undo_checkpoint(db, timeline_id, label)
    yield


class HistoryEmpty(Exception):
    """Raised when there is nothing to undo/redo."""


def _perform(
    db: Any,
    timeline_id: str,
    from_stack: str,
    to_stack: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Core undo/redo logic.

    1. Cancel any in-flight jobs for this timeline (seamless / best-effort).
    2. Capture the live state BEFORE opening the transaction.
    3. In ONE atomic transaction: pop the top of *from_stack*, push the live
       state onto *to_stack*, restore the popped snapshot.
    4. Rebuild the OTIO cache as a separate idempotent step (soft-fail).
    """
    repos.request_timeline_jobs_cancel(db, timeline_id)  # cancel in-flight (§7)
    live = repos.capture_timeline_snapshot(db, timeline_id)
    with db.transaction(immediate=True) as conn:
        top = repos.pop_top(conn, timeline_id, from_stack)
        if top is None:
            raise HistoryEmpty()
        repos.push_row(conn, timeline_id, to_stack, top["label"], live)
        repos.restore_timeline_snapshot(db, timeline_id, top["payload"], conn=conn)
    try:
        rebuild_otio(db, timeline_id)
    except Exception as exc:  # rows restored; otio cache stale but regenerable on next edit
        logger.warning(
            "rebuild_otio after %s failed (otio cache stale): %s",
            from_stack,
            exc,
        )
    return repos.list_timeline_clips(db, timeline_id), repos.list_scenes(db, timeline_id)


def perform_undo(
    db: Any, timeline_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Undo the last edit: push live state onto redo, pop and restore from undo."""
    return _perform(db, timeline_id, "undo", "redo")


def perform_redo(
    db: Any, timeline_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Redo the last undone edit: push live state onto undo, pop and restore from redo."""
    return _perform(db, timeline_id, "redo", "undo")
