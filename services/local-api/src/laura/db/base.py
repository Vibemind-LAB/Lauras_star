"""Database backend abstraction (docs/14-enterprise.md).

A single interface (`Database`) with two implementations: SQLite (desktop/embedded)
and PostgreSQL (server/on-prem). The schema is portable; repos use ``?`` placeholders
and dict-like rows, which the Postgres backend adapts. Job claiming is dialect-specific
(SQLite BEGIN IMMEDIATE vs Postgres FOR UPDATE SKIP LOCKED).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Protocol

from ..util import utcnow_iso

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class Cursor(Protocol):
    def fetchone(self) -> Any: ...
    def fetchall(self) -> list[Any]: ...
    @property
    def rowcount(self) -> int: ...


class Conn(Protocol):
    """Minimal connection surface used by the repositories."""

    def execute(self, sql: str, params: Any = ...) -> Cursor: ...


def migration_files() -> list[tuple[int, Path]]:
    out: list[tuple[int, Path]] = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        try:
            version = int(path.name.split("_", 1)[0])
        except ValueError:
            continue
        out.append((version, path))
    return out


def split_statements(sql: str) -> list[str]:
    """Split a migration script into statements, stripping line comments.

    Portable across backends (SQLite executescript is not available on Postgres).
    """
    lines: list[str] = []
    for line in sql.splitlines():
        idx = line.find("--")
        lines.append(line if idx < 0 else line[:idx])
    text = "\n".join(lines)
    return [stmt.strip() for stmt in text.split(";") if stmt.strip()]


def to_pyformat(sql: str) -> str:
    """Translate SQLite-style ``?`` placeholders to psycopg ``%s`` (escaping literal %)."""
    return sql.replace("%", "%%").replace("?", "%s")


class Database(ABC):
    """Backend interface used by repositories and the job runner."""

    # --- connections / transactions (backend-specific) --------------------
    @abstractmethod
    def connection(self) -> AbstractContextManager[Conn]:
        """An autocommit connection for reads and one-off writes."""

    @abstractmethod
    def transaction(self, *, immediate: bool = False) -> AbstractContextManager[Conn]:
        """A transaction. ``immediate`` acquires a write lock up front on SQLite."""

    @abstractmethod
    def claim_job(
        self, *, worker_id: str, lease_seconds: int, queues: tuple[str, ...] | None
    ) -> dict[str, Any] | None:
        """Atomically claim one queued job and mark it running. Returns it or None."""
        raise NotImplementedError

    # --- migrations (shared) ----------------------------------------------
    def migrate(self) -> list[int]:
        applied_now: list[int] = []
        with self.connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_meta ("
                "  version INTEGER PRIMARY KEY,"
                "  applied_at TEXT NOT NULL)"
            )
            applied = {
                row["version"] for row in conn.execute("SELECT version FROM schema_meta").fetchall()
            }
            for version, path in migration_files():
                if version in applied:
                    continue
                for statement in split_statements(path.read_text(encoding="utf-8")):
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_meta(version, applied_at) VALUES (?, ?)",
                    (version, utcnow_iso()),
                )
                applied_now.append(version)
        return applied_now

    def schema_version(self) -> int:
        with self.connection() as conn:
            row = conn.execute("SELECT MAX(version) AS v FROM schema_meta").fetchone()
            return int(row["v"]) if row and row["v"] is not None else 0
