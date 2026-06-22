"""Append-only audit log helper (docs/14-enterprise.md).

Every mutating, security-relevant action records who did what, to which entity, when.
"""

from __future__ import annotations

from typing import Any

from .auth.principal import Principal
from .db import repos
from .db.database import Database


def record(
    db: Database,
    principal: Principal,
    action: str,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    repos.insert_audit_event(
        db,
        org_id=principal.org_id,
        principal_kind=principal.kind,
        principal_id=principal.user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload or {},
    )


def system_principal() -> Principal:
    """The principal recorded for server-side automation (job handlers).

    Jobs run without a request principal; audit rows still need a stable
    actor. This is the local owner identity, matching the default request
    principal in auth/deps.py.
    """
    return Principal(kind="local", role="owner")
