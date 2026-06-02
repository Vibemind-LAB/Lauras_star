"""Identity, API keys, and RBAC (docs/14-enterprise.md).

Additive and non-breaking: with no API key presented, the principal is the implicit
local "owner" (full access), preserving desktop/local behaviour. Enterprise/server
deployments issue scoped API keys whose role gates each mutating endpoint.
"""

from .deps import require_permission, resolve_principal
from .keys import generate_api_key, hash_key
from .permissions import ROLE_PERMISSIONS, has_permission, is_valid_role
from .principal import Principal

__all__ = [
    "ROLE_PERMISSIONS",
    "Principal",
    "generate_api_key",
    "has_permission",
    "hash_key",
    "is_valid_role",
    "require_permission",
    "resolve_principal",
]
