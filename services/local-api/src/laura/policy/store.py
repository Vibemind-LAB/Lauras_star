"""DB access for asset policies (migration 0028).

Follows the idioms in ``laura.ledger.sqlite_store`` and ``laura.db.repos``:
- ``db.connection()`` for reads, ``db.transaction()`` for writes.
- ``dict(row)`` for row-to-dict conversion.
- ``utcnow_iso()`` from ``laura.util``.

Do NOT put this logic in ``db/repos.py`` — it lives here to avoid the
concurrent-write collision with the codex session editing that file.
"""

from __future__ import annotations

from typing import Any

from ..db.database import Database
from ..util import utcnow_iso
from .policy import parse_policy

__all__ = ["get_asset_policy", "set_asset_policy"]

_VALID_SOURCES = frozenset({"row", "pattern", "env", "default"})


def _validate_source(source: str) -> None:
    if source not in _VALID_SOURCES:
        raise ValueError(
            f"Invalid policy_source {source!r}; must be one of {sorted(_VALID_SOURCES)}"
        )


def set_asset_policy(
    db: Database,
    asset_id: str,
    *,
    policy: str,
    source: str,
) -> dict[str, Any]:
    """Upsert the resolved policy for *asset_id*.

    Parameters
    ----------
    db:
        Active :class:`~laura.db.database.Database` connection.
    asset_id:
        Primary key of the ``media_assets`` row.
    policy:
        Policy string — must be parseable by :func:`~laura.policy.policy.parse_policy`.
        Raises :class:`ValueError` otherwise.
    source:
        One of ``'row'``, ``'pattern'``, ``'env'``, ``'default'``.
        Raises :class:`ValueError` if not in that set.

    Returns
    -------
    dict
        The full persisted row as a plain dict.
    """
    # Validate before hitting the DB.
    parse_policy(policy)  # raises ValueError on bad policy string
    _validate_source(source)

    now = utcnow_iso()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO asset_policies(asset_id, policy, policy_source, resolved_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(asset_id) DO UPDATE SET "
            "  policy=excluded.policy, "
            "  policy_source=excluded.policy_source, "
            "  resolved_at=excluded.resolved_at",
            (asset_id, policy, source, now),
        )
        row = conn.execute(
            "SELECT * FROM asset_policies WHERE asset_id=?", (asset_id,)
        ).fetchone()
    return dict(row)


def get_asset_policy(db: Database, asset_id: str) -> dict[str, Any] | None:
    """Return the persisted policy row for *asset_id*, or ``None`` if absent.

    Parameters
    ----------
    db:
        Active :class:`~laura.db.database.Database` connection.
    asset_id:
        Primary key of the ``media_assets`` row.

    Returns
    -------
    dict or None
        The ``asset_policies`` row as a plain dict, or ``None`` if not found.
    """
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM asset_policies WHERE asset_id=?", (asset_id,)
        ).fetchone()
        return dict(row) if row is not None else None
