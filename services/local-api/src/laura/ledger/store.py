"""LedgerStore port — backend-agnostic Protocol for the short_runs ledger.

Decision D1: SQLite today, Supabase-swappable later.  All callers program against
this Protocol; the factory in ``laura.ledger`` selects the concrete backend.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LedgerStore(Protocol):
    """Append-only ledger of short-build attempts (one row per run)."""

    def record_run(
        self,
        *,
        short_id: str,
        pipeline_version: str,
        input_sha256: str | None = None,
        recipe_hash: str | None = None,
        status: str = "queued",
    ) -> dict[str, Any]:
        """Insert a new run row and return it.

        Raises ``ValueError`` when *status* is not one of the four allowed values.
        """
        ...

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Return the run row for *run_id*, or ``None`` when not found."""
        ...

    def list_runs_for_short(self, short_id: str) -> list[dict[str, Any]]:
        """All runs for *short_id*, newest first (by ``created_at`` then ``id`` desc)."""
        ...

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        trace_json: str | None = None,
    ) -> dict[str, Any] | None:
        """Partial-update *run_id*: set *status* and/or *trace_json*, bump ``updated_at``.

        Returns the updated row, or ``None`` when *run_id* does not exist.
        Raises ``ValueError`` when *status* is not one of the four allowed values.
        """
        ...
