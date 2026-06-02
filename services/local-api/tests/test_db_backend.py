"""Backend abstraction: statement splitting, placeholder translation, factory."""

from __future__ import annotations

from pathlib import Path

import pytest

from laura.config import Settings
from laura.db.base import split_statements, to_pyformat
from laura.db.database import SqliteDatabase, create_database


def test_split_statements_strips_comments() -> None:
    sql = "-- header\nCREATE TABLE x (id TEXT);  -- inline\nCREATE INDEX i ON x(id);\n"
    assert split_statements(sql) == ["CREATE TABLE x (id TEXT)", "CREATE INDEX i ON x(id)"]


def test_to_pyformat_translates_placeholders() -> None:
    assert to_pyformat("SELECT * FROM t WHERE a=? AND b=?") == (
        "SELECT * FROM t WHERE a=%s AND b=%s"
    )
    # literal % is escaped for psycopg
    assert to_pyformat("x LIKE 'a%'") == "x LIKE 'a%%'"


def test_factory_selects_sqlite(tmp_path: Path) -> None:
    db = create_database(Settings(workspace_root=tmp_path))
    assert isinstance(db, SqliteDatabase)


def test_factory_selects_postgres(tmp_path: Path) -> None:
    pytest.importorskip("psycopg")
    db = create_database(Settings(workspace_root=tmp_path, database_url="postgresql://x/y"))
    assert type(db).__name__ == "PostgresDatabase"


def test_celery_app_importable() -> None:
    pytest.importorskip("celery")
    from laura.jobs.celery_app import celery_app

    assert celery_app.main == "laura"
