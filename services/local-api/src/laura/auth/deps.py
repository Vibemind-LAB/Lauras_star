"""FastAPI auth dependencies: resolve a principal and enforce permissions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from ..db import repos
from .keys import hash_key
from .permissions import has_permission
from .principal import Principal


async def resolve_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_laura_token: str | None = Header(default=None),
) -> Principal:
    """Bearer API key -> scoped principal; otherwise the local-token owner."""
    db = request.app.state.db
    settings = request.app.state.settings

    if authorization and authorization.lower().startswith("bearer "):
        full = authorization[7:].strip()
        key = repos.get_api_key_by_hash(db, hash_key(full))
        if key is None or key["revoked"]:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or revoked API key")
        repos.touch_api_key(db, key["id"])
        return Principal(kind="key", role=key["role"], user_id=key["user_id"],
                         org_id=key["org_id"])

    if settings.token and x_laura_token != settings.token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing X-Laura-Token")
    return Principal(kind="local", role="owner")


def require_permission(permission: str) -> Callable[..., Awaitable[Principal]]:
    """Dependency factory enforcing ``permission`` for the resolved principal."""

    async def dependency(
        principal: Annotated[Principal, Depends(resolve_principal)],
    ) -> Principal:
        if not has_permission(principal.role, permission):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"role '{principal.role}' lacks permission '{permission}'",
            )
        return principal

    return dependency
