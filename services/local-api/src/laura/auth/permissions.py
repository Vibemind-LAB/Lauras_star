"""Role -> permission mapping (RBAC). Roles from docs/09-security.md."""

from __future__ import annotations

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": frozenset({"*"}),
    "admin": frozenset({"*"}),
    "editor": frozenset(
        {"project:write", "asset:write", "analysis:run", "timeline:edit", "export:create", "read"}
    ),
    "exporter": frozenset({"export:create", "read"}),
    "reviewer": frozenset({"read"}),
}


def is_valid_role(role: str) -> bool:
    return role in ROLE_PERMISSIONS


def has_permission(role: str, permission: str) -> bool:
    perms = ROLE_PERMISSIONS.get(role)
    if perms is None:
        return False
    return "*" in perms or permission in perms
