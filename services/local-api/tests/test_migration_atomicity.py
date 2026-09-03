"""A migration run is all-or-nothing.

``Database.migrate`` applies every pending migration file in order. Before this was
wrapped in a transaction it ran on an autocommit connection (``isolation_level=None``
in :mod:`laura.db.sqlite`), so each statement committed on its own: a run that died
halfway left the schema half-applied, with ``schema_meta`` claiming neither the old
nor the new state. Re-running then hit "table already exists" on the part that HAD
landed, and the database could only be fixed by hand.

The same change is what makes the test suite affordable: 122 statements committing
individually cost ~15s per migrated database, and the suite builds one per test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from laura.config import Settings
from laura.db import base
from laura.db.database import SqliteDatabase


def _db(tmp_path: Path) -> SqliteDatabase:
    settings = Settings(workspace_root=tmp_path / "ws", token=None, start_runner=False)
    return SqliteDatabase(settings.db_path)


def _write_migrations(tmp_path: Path, *scripts: tuple[int, str]) -> list[tuple[int, Path]]:
    out: list[tuple[int, Path]] = []
    folder = tmp_path / "migrations"
    folder.mkdir(parents=True, exist_ok=True)
    for version, sql in scripts:
        path = folder / f"{version:04d}_probe.sql"
        path.write_text(sql, encoding="utf-8")
        out.append((version, path))
    return out


def test_a_failing_migration_rolls_back_the_whole_run(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Migration 1 succeeds, migration 2 is broken -> NEITHER may survive.

    This is the property that autocommit could not give: without a transaction the
    ``probe_ok`` table from the first file stays behind, and the next run trips over it.
    """
    files = _write_migrations(
        tmp_path,
        (1, "CREATE TABLE probe_ok (id INTEGER PRIMARY KEY);"),
        (2, "CREATE TABLE probe_broken (this is not valid sql;"),
    )
    monkeypatch.setattr(base, "migration_files", lambda: files)
    db = _db(tmp_path)

    with pytest.raises(Exception):
        db.migrate()

    with db.connection() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    assert "probe_ok" not in tables, "the successful half of a failed run must not survive"
    assert "probe_broken" not in tables


def test_a_clean_run_still_applies_everything_and_records_it(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Counter-check: without a failure the transaction must COMMIT, not swallow the work.

    Without this, a rollback-everything bug would make the test above pass for the
    wrong reason.
    """
    files = _write_migrations(
        tmp_path,
        (1, "CREATE TABLE probe_one (id INTEGER PRIMARY KEY);"),
        (2, "CREATE TABLE probe_two (id INTEGER PRIMARY KEY);"),
    )
    monkeypatch.setattr(base, "migration_files", lambda: files)
    db = _db(tmp_path)

    applied = db.migrate()

    assert applied == [1, 2]
    assert db.schema_version() == 2
    with db.connection() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {"probe_one", "probe_two"} <= tables


def test_already_applied_migrations_are_kept_and_skipped(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A LATER failing run must not undo what an EARLIER successful run applied.

    All-or-nothing is per run, not per database: versions recorded in ``schema_meta``
    are skipped, so they are never inside the failing transaction to begin with.
    """
    first = _write_migrations(tmp_path, (1, "CREATE TABLE probe_kept (id INTEGER PRIMARY KEY);"))
    monkeypatch.setattr(base, "migration_files", lambda: first)
    db = _db(tmp_path)
    assert db.migrate() == [1]

    second = first + _write_migrations(tmp_path, (2, "CREATE TABLE nope (bad sql here;"))
    monkeypatch.setattr(base, "migration_files", lambda: second)
    with pytest.raises(Exception):
        db.migrate()

    assert db.schema_version() == 1
    with db.connection() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "probe_kept" in tables, "an earlier successful run must survive a later failure"
