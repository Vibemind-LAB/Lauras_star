"""Database backend selection.

``Database`` is the backend-agnostic interface used by repositories and the runner.
``create_database`` picks SQLite (desktop/embedded) or PostgreSQL (server) from settings.
"""

from __future__ import annotations

from ..config import Settings
from .base import Database
from .sqlite import SqliteDatabase

__all__ = ["Database", "SqliteDatabase", "create_database"]


def create_database(settings: Settings) -> Database:
    url = settings.database_url
    if url and url.startswith(("postgres://", "postgresql://")):
        # Lazy import — requires the `server` extra (psycopg).
        from .postgres import PostgresDatabase

        return PostgresDatabase(url)
    return SqliteDatabase(settings.db_path)
