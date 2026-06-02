"""Shared fixtures: an isolated workspace, a migrated DB, and a TestClient."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db.database import Database, SqliteDatabase
from laura.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # No token, no background runner thread -> deterministic tests.
    return Settings(workspace_root=tmp_path, token=None, start_runner=False)


@pytest.fixture
def db(settings: Settings) -> Database:
    database = SqliteDatabase(settings.db_path)
    database.migrate()
    return database


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
