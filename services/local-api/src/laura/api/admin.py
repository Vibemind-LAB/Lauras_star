"""Enterprise admin API: organizations, users, API keys, audit (docs/14-enterprise.md).

All endpoints require the ``admin:manage`` (audit: ``audit:read``) permission, which
only owner/admin roles — and the implicit local owner — hold.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from .. import audit
from ..auth import Principal, generate_api_key, is_valid_role, require_permission
from ..db import repos
from ..db.database import Database
from .models import (
    AuditEventOut,
    KeyCreate,
    KeyCreated,
    OrgCreate,
    OrgOut,
    UserCreate,
    UserOut,
)

router = APIRouter(prefix="/admin", tags=["admin"])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _check_role(role: str) -> None:
    if not is_valid_role(role):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"invalid role: {role}")


@router.post("/orgs", response_model=OrgOut, status_code=status.HTTP_201_CREATED)
def create_org(
    body: OrgCreate, request: Request,
    principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
) -> OrgOut:
    db = _db(request)
    org = repos.create_org(db, name=body.name)
    audit.record(db, principal, "org.create", entity_type="org", entity_id=org["id"])
    return OrgOut(**org)


@router.post(
    "/orgs/{org_id}/users", response_model=UserOut, status_code=status.HTTP_201_CREATED
)
def add_user(
    org_id: str, body: UserCreate, request: Request,
    principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
) -> UserOut:
    db = _db(request)
    if repos.get_org(db, org_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "org not found")
    _check_role(body.role)
    user = repos.create_user(db, email=body.email, display_name=body.display_name)
    repos.add_membership(db, org_id=org_id, user_id=user["id"], role=body.role)
    audit.record(db, principal, "user.add", entity_type="user", entity_id=user["id"],
                 payload={"org_id": org_id, "role": body.role})
    return UserOut(**user, role=body.role)


@router.post(
    "/orgs/{org_id}/keys", response_model=KeyCreated, status_code=status.HTTP_201_CREATED
)
def create_key(
    org_id: str, body: KeyCreate, request: Request,
    principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
) -> KeyCreated:
    db = _db(request)
    if repos.get_org(db, org_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "org not found")
    _check_role(body.role)
    full, prefix, key_hash = generate_api_key()
    row = repos.create_api_key(
        db, org_id=org_id, user_id=body.user_id, name=body.name, prefix=prefix,
        key_hash=key_hash, role=body.role,
    )
    audit.record(db, principal, "key.create", entity_type="api_key", entity_id=row["id"],
                 payload={"org_id": org_id, "role": body.role})
    return KeyCreated(id=row["id"], prefix=prefix, role=body.role, key=full,
                      created_at=row["created_at"])


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_key(
    key_id: str, request: Request,
    principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
) -> None:
    db = _db(request)
    if not repos.revoke_api_key(db, key_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "key not found")
    audit.record(db, principal, "key.revoke", entity_type="api_key", entity_id=key_id)


@router.get("/audit", response_model=list[AuditEventOut])
def list_audit(
    request: Request,
    _principal: Annotated[Principal, Depends(require_permission("audit:read"))],
    limit: int = 100,
) -> list[AuditEventOut]:
    return [AuditEventOut(**e) for e in repos.list_audit_events(_db(request), limit=limit)]
