"""Ledger package — durable short_runs ledger behind the LedgerStore port.

Decision D1: SQLite now; a Supabase impl slots in here later by swapping
the concrete class returned by ``get_ledger_store`` without touching callers.
"""

from __future__ import annotations

from ..db.database import Database
from .sqlite_store import SQLiteLedgerStore
from .store import LedgerStore

__all__ = ["LedgerStore", "SQLiteLedgerStore", "get_ledger_store"]


def get_ledger_store(db: Database) -> LedgerStore:
    """Factory: return the active LedgerStore backend.

    Today this is always ``SQLiteLedgerStore(db)``.  Future: check a feature
    flag / settings value and return a SupabaseLedgerStore when configured.
    """
    return SQLiteLedgerStore(db)
