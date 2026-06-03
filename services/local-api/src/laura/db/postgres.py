"""PostgreSQL backend (server / on-prem). Requires the ``server`` extra (psycopg).

Repos use ``?`` placeholders and dict rows (SQLite style); this backend translates
``?`` -> ``%s`` and uses ``dict_row``. Job claiming uses ``FOR UPDATE SKIP LOCKED``
so multiple workers never grab the same job.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..util import utcnow_iso
from .base import Conn, Database, split_statements, to_pyformat

_RLS_FILE = Path(__file__).parent / "rls.sql"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class _PgConn:
    """Adapts a psycopg connection to the repo-facing ``Conn`` surface."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def execute(self, sql: str, params: Any = ()) -> Any:
        return self._raw.execute(to_pyformat(sql), params)


class PostgresDatabase(Database):
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def _connect(self, *, autocommit: bool) -> Any:
        return psycopg.connect(self.dsn, autocommit=autocommit, row_factory=dict_row)

    @contextmanager
    def connection(self) -> Iterator[Conn]:
        raw = self._connect(autocommit=True)
        try:
            yield _PgConn(raw)
        finally:
            raw.close()

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[Conn]:
        raw = self._connect(autocommit=False)
        try:
            yield _PgConn(raw)
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()

    def claim_job(
        self, *, worker_id: str, lease_seconds: int, queues: tuple[str, ...] | None
    ) -> dict[str, Any] | None:
        expires = _iso(datetime.now(UTC) + timedelta(seconds=lease_seconds))
        raw = self._connect(autocommit=False)
        try:
            conn = _PgConn(raw)
            if queues:
                placeholders = ",".join("?" for _ in queues)
                row = conn.execute(
                    f"SELECT * FROM jobs WHERE status='queued' AND queue IN ({placeholders}) "
                    "ORDER BY priority DESC, created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED",
                    tuple(queues),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM jobs WHERE status='queued' "
                    "ORDER BY priority DESC, created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED"
                ).fetchone()
            if row is None:
                raw.commit()
                return None
            conn.execute(
                "UPDATE jobs SET status='running', attempt=attempt+1, worker_id=?, "
                "lease_expires_at=?, heartbeat_at=?, updated_at=? WHERE id=?",
                (worker_id, expires, utcnow_iso(), utcnow_iso(), row["id"]),
            )
            raw.commit()
            return dict(row)
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()

    # --- row-level security (Postgres-only, defense in depth) -------------
    def apply_rls(self) -> None:
        """Enable the multi-tenant RLS policies. Idempotent; call after ``migrate``."""
        sql = _RLS_FILE.read_text(encoding="utf-8")
        with self.transaction() as conn:
            for statement in split_statements(sql):
                conn.execute(statement)

    def set_org(self, conn: Conn, org_id: str | None) -> None:
        """Set the ``app.current_org`` GUC on ``conn`` so RLS scopes rows to that org
        (empty/None = local owner / admin, which sees everything)."""
        conn.execute("SELECT set_config('app.current_org', ?, false)", (org_id or "",))
