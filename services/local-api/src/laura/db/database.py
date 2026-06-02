"""SQLite connection management and a tiny forward-only migration runner.

Each operation gets its own connection (WAL mode, foreign keys on). That avoids
cross-thread sharing issues between the FastAPI request handlers and the background
job runner, at negligible cost for a local single-user store.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..util import utcnow_iso

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _migration_files() -> list[tuple[int, Path]]:
    out: list[tuple[int, Path]] = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        try:
            version = int(path.name.split("_", 1)[0])
        except ValueError:
            continue
        out.append((version, path))
    return out


class Database:
    """Thin SQLite wrapper. Open a fresh connection per unit of work."""

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
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        """A manual transaction. ``immediate=True`` acquires a write lock up front
        (BEGIN IMMEDIATE) — required for race-free job claiming."""
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

    def migrate(self) -> list[int]:
        """Apply any pending migrations. Returns the versions applied this call."""
        applied_now: list[int] = []
        conn = self.connect()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_meta ("
                "  version INTEGER PRIMARY KEY,"
                "  applied_at TEXT NOT NULL)"
            )
            applied = {row[0] for row in conn.execute("SELECT version FROM schema_meta")}
            for version, path in _migration_files():
                if version in applied:
                    continue
                conn.executescript(path.read_text(encoding="utf-8"))
                conn.execute(
                    "INSERT INTO schema_meta(version, applied_at) VALUES (?, ?)",
                    (version, utcnow_iso()),
                )
                applied_now.append(version)
        finally:
            conn.close()
        return applied_now

    def schema_version(self) -> int:
        with self.connection() as conn:
            row = conn.execute("SELECT MAX(version) AS v FROM schema_meta").fetchone()
            return int(row["v"]) if row and row["v"] is not None else 0
