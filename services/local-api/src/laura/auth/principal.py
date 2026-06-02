"""The authenticated principal for a request."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    kind: str               # "local" | "key"
    role: str               # owner|admin|editor|exporter|reviewer
    user_id: str | None = None
    org_id: str | None = None
