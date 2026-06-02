"""API key generation/hashing. The raw key is shown once; only its sha256 is stored.

Keys are high-entropy random tokens, so a fast hash (sha256) is appropriate — unlike
user passwords, there is nothing to brute-force.
"""

from __future__ import annotations

import hashlib
import secrets

KEY_PREFIX = "laura_sk_"


def hash_key(full_key: str) -> str:
    return hashlib.sha256(full_key.encode("utf-8")).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """Return ``(full_key, prefix, key_hash)``. Persist prefix + hash; return full once."""
    full = KEY_PREFIX + secrets.token_urlsafe(32)
    return full, full[:16], hash_key(full)
