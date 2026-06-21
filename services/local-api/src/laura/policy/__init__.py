"""Policy package — per-input policy model, precedence resolver, and persistence.

Public API
----------
Classes:
    Policy          — immutable policy value (auto | human | threshold:<0..1>).
    ResolvedPolicy  — a Policy plus the precedence tier that produced it.

Functions:
    parse_policy    — parse a raw string into a Policy; raises ValueError on bad input.
    policy_to_str   — serialise a Policy back to its canonical string form.
    resolve_policy  — CSV-row > source-pattern > env > default precedence resolver.
    set_asset_policy — upsert the resolved policy for an asset (DB write).
    get_asset_policy — retrieve the persisted policy for an asset (DB read).
"""

from __future__ import annotations

from .policy import Policy, ResolvedPolicy, parse_policy, policy_to_str, resolve_policy
from .store import get_asset_policy, set_asset_policy

__all__ = [
    "Policy",
    "ResolvedPolicy",
    "get_asset_policy",
    "parse_policy",
    "policy_to_str",
    "resolve_policy",
    "set_asset_policy",
]
