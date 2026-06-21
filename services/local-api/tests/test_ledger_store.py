"""P3-T1 — LedgerStore port + SQLiteLedgerStore + migration 0027.

TDD suite: written first (fails before implementation), green after.
All files under test are new:
  - laura/ledger/store.py        (LedgerStore Protocol)
  - laura/ledger/sqlite_store.py (SQLiteLedgerStore impl)
  - laura/ledger/__init__.py     (get_ledger_store factory)
  - db/migrations/0027_short_runs.sql
"""

from __future__ import annotations

import time

import pytest

from laura.db.database import Database
from laura.ledger import get_ledger_store
from laura.ledger.sqlite_store import SQLiteLedgerStore
from laura.ledger.store import LedgerStore

# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_migration_creates_short_runs_table(db: Database) -> None:
    """Migration 0027 must create the short_runs table with the required columns."""
    with db.connection() as conn:
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(short_runs)").fetchall()
        }
    required = {
        "id",
        "short_id",
        "input_sha256",
        "pipeline_version",
        "recipe_hash",
        "status",
        "trace_json",
        "created_at",
        "updated_at",
    }
    assert required <= cols


def test_migration_creates_short_id_index(db: Database) -> None:
    """An index on short_id must exist after migration."""
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='short_runs'"
        ).fetchall()
        index_names = {row["name"] for row in rows}
    assert any("short_id" in name for name in index_names), (
        f"No short_id index found; indexes: {index_names}"
    )


# ---------------------------------------------------------------------------
# record_run / get_run
# ---------------------------------------------------------------------------


def test_record_run_returns_queued_row(db: Database) -> None:
    store = get_ledger_store(db)
    row = store.record_run(
        short_id="sid1",
        pipeline_version="v1",
        input_sha256="abc",
        recipe_hash="rh1",
    )
    assert row["short_id"] == "sid1"
    assert row["pipeline_version"] == "v1"
    assert row["input_sha256"] == "abc"
    assert row["recipe_hash"] == "rh1"
    assert row["status"] == "queued"
    assert row["trace_json"] is None
    assert row["id"]
    assert row["created_at"]
    assert row["updated_at"]


def test_record_run_generates_unique_ids(db: Database) -> None:
    store = get_ledger_store(db)
    r1 = store.record_run(short_id="sid", pipeline_version="v1")
    r2 = store.record_run(short_id="sid", pipeline_version="v1")
    assert r1["id"] != r2["id"]


def test_get_run_returns_row(db: Database) -> None:
    store = get_ledger_store(db)
    created = store.record_run(short_id="sid", pipeline_version="v1")
    fetched = store.get_run(created["id"])
    assert fetched is not None
    assert fetched["id"] == created["id"]


def test_get_run_unknown_returns_none(db: Database) -> None:
    store = get_ledger_store(db)
    assert store.get_run("no-such-id") is None


# ---------------------------------------------------------------------------
# list_runs_for_short
# ---------------------------------------------------------------------------


def test_list_runs_for_short_newest_first(db: Database) -> None:
    store = get_ledger_store(db)
    r1 = store.record_run(short_id="s1", pipeline_version="v1")
    # small sleep so created_at timestamps differ
    time.sleep(0.01)
    r2 = store.record_run(short_id="s1", pipeline_version="v2")
    _other = store.record_run(short_id="s2", pipeline_version="v1")

    runs = store.list_runs_for_short("s1")
    ids = [r["id"] for r in runs]
    assert r2["id"] in ids
    assert r1["id"] in ids
    assert _other["id"] not in ids
    # newest first
    assert ids.index(r2["id"]) < ids.index(r1["id"])


def test_list_runs_for_short_empty(db: Database) -> None:
    store = get_ledger_store(db)
    assert store.list_runs_for_short("nonexistent") == []


# ---------------------------------------------------------------------------
# update_run
# ---------------------------------------------------------------------------


def test_update_run_status(db: Database) -> None:
    store = get_ledger_store(db)
    row = store.record_run(short_id="sid", pipeline_version="v1")
    updated = store.update_run(row["id"], status="running")
    assert updated is not None
    assert updated["status"] == "running"


def test_update_run_trace_json(db: Database) -> None:
    store = get_ledger_store(db)
    row = store.record_run(short_id="sid", pipeline_version="v1")
    updated = store.update_run(row["id"], trace_json='{"step": 1}')
    assert updated is not None
    assert updated["trace_json"] == '{"step": 1}'
    # status unchanged
    assert updated["status"] == "queued"


def test_update_run_bumps_updated_at(db: Database) -> None:
    store = get_ledger_store(db)
    row = store.record_run(short_id="sid", pipeline_version="v1")
    original_updated_at = row["updated_at"]
    time.sleep(0.01)
    updated = store.update_run(row["id"], status="succeeded")
    assert updated is not None
    assert updated["updated_at"] >= original_updated_at


def test_update_run_partial_status_only(db: Database) -> None:
    store = get_ledger_store(db)
    row = store.record_run(short_id="sid", pipeline_version="v1")
    store.update_run(row["id"], trace_json="T")
    updated = store.update_run(row["id"], status="failed")
    assert updated is not None
    assert updated["status"] == "failed"
    assert updated["trace_json"] == "T"  # trace preserved


def test_update_run_unknown_returns_none(db: Database) -> None:
    store = get_ledger_store(db)
    assert store.update_run("no-such-id", status="running") is None


# ---------------------------------------------------------------------------
# ValueError on bad status
# ---------------------------------------------------------------------------


def test_record_run_invalid_status_raises(db: Database) -> None:
    store = get_ledger_store(db)
    with pytest.raises(ValueError, match="status"):
        store.record_run(short_id="sid", pipeline_version="v1", status="bogus")


def test_update_run_invalid_status_raises(db: Database) -> None:
    store = get_ledger_store(db)
    row = store.record_run(short_id="sid", pipeline_version="v1")
    with pytest.raises(ValueError, match="status"):
        store.update_run(row["id"], status="not-a-status")


# ---------------------------------------------------------------------------
# Type check: SQLiteLedgerStore satisfies LedgerStore (mypy structural check)
# ---------------------------------------------------------------------------


def _accept_ledger_store(s: LedgerStore) -> None:
    pass


def test_sqlite_store_satisfies_protocol(db: Database) -> None:
    """SQLiteLedgerStore is structurally assignable to LedgerStore."""
    impl = SQLiteLedgerStore(db)
    _accept_ledger_store(impl)  # mypy structurally verifies Protocol satisfaction
