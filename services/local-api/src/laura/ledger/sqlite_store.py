"""SQLite-backed LedgerStore implementation.

Follows the SQL idioms in ``laura.db.repos``:
- ``db.connection()`` for reads, ``db.transaction()`` for writes.
- ``dict(row)`` for row-to-dict conversion.
- ``new_id()`` / ``utcnow_iso()`` from ``laura.util``.
"""

from __future__ import annotations

from typing import Any

from ..db.database import Database
from ..util import new_id, utcnow_iso

_VALID_STATUSES = frozenset({"queued", "running", "succeeded", "failed"})


def _validate_status(status: str) -> None:
    if status not in _VALID_STATUSES:
        raise ValueError(
            f"Invalid status {status!r}; must be one of {sorted(_VALID_STATUSES)}"
        )


class SQLiteLedgerStore:
    """LedgerStore backed by the ``short_runs`` SQLite table (migration 0027)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # record_run
    # ------------------------------------------------------------------

    def record_run(
        self,
        *,
        short_id: str,
        pipeline_version: str,
        input_sha256: str | None = None,
        recipe_hash: str | None = None,
        status: str = "queued",
    ) -> dict[str, Any]:
        _validate_status(status)
        run_id = new_id()
        now = utcnow_iso()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO short_runs "
                "(id, short_id, input_sha256, pipeline_version, recipe_hash, "
                "status, trace_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    run_id, short_id, input_sha256, pipeline_version,
                    recipe_hash, status, now, now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM short_runs WHERE id=?", (run_id,)
            ).fetchone()
        return dict(row)

    # ------------------------------------------------------------------
    # get_run
    # ------------------------------------------------------------------

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM short_runs WHERE id=?", (run_id,)
            ).fetchone()
            return dict(row) if row is not None else None

    # ------------------------------------------------------------------
    # list_runs_for_short
    # ------------------------------------------------------------------

    def list_runs_for_short(self, short_id: str) -> list[dict[str, Any]]:
        with self._db.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM short_runs WHERE short_id=? "
                "ORDER BY created_at DESC, id DESC",
                (short_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # update_run
    # ------------------------------------------------------------------

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        trace_json: str | None = None,
    ) -> dict[str, Any] | None:
        if status is not None:
            _validate_status(status)

        sets: list[str] = ["updated_at=?"]
        params: list[Any] = [utcnow_iso()]

        if status is not None:
            sets.append("status=?")
            params.append(status)
        if trace_json is not None:
            sets.append("trace_json=?")
            params.append(trace_json)

        params.append(run_id)
        with self._db.transaction() as conn:
            cur = conn.execute(
                f"UPDATE short_runs SET {', '.join(sets)} WHERE id=?",
                params,
            )
            if cur.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM short_runs WHERE id=?", (run_id,)
            ).fetchone()
        return dict(row)
