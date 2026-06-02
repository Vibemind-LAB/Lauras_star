"""SQLite backend (desktop / embedded). WAL mode, one connection per unit of work."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..util import utcnow_iso
from .base import Conn, Database


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class SqliteDatabase(Database):
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @contextmanager
    def connection(self) -> Iterator[Conn]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[Conn]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def claim_job(
        self, *, worker_id: str, lease_seconds: int, queues: tuple[str, ...] | None
    ) -> dict[str, Any] | None:
        expires = _iso(datetime.now(UTC) + timedelta(seconds=lease_seconds))
        with self.transaction(immediate=True) as conn:
            if queues:
                placeholders = ",".join("?" for _ in queues)
                row = conn.execute(
                    f"SELECT * FROM jobs WHERE status='queued' AND queue IN ({placeholders}) "
                    "ORDER BY priority DESC, created_at ASC LIMIT 1",
                    queues,
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM jobs WHERE status='queued' "
                    "ORDER BY priority DESC, created_at ASC LIMIT 1"
                ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE jobs SET status='running', attempt=attempt+1, worker_id=?, "
                "lease_expires_at=?, heartbeat_at=?, updated_at=? WHERE id=?",
                (worker_id, expires, utcnow_iso(), utcnow_iso(), row["id"]),
            )
            return dict(row)
