"""Reusable list pagination (docs/04-api.md).

Optional ``limit``/``offset`` query params with defensive clamping. ``limit=None``
returns the full list (non-breaking default); list endpoints set an ``X-Total-Count``
header so clients can page without changing the response envelope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query

MAX_LIMIT = 200


@dataclass(frozen=True)
class Page:
    limit: int | None
    offset: int


def pagination(
    limit: Annotated[int | None, Query(description="page size, clamped to 1..200")] = None,
    offset: Annotated[int, Query(description="rows to skip")] = 0,
) -> Page:
    """Clamp page params defensively rather than rejecting out-of-range values."""
    clamped = None if limit is None else max(1, min(limit, MAX_LIMIT))
    return Page(limit=clamped, offset=max(0, offset))


PageParams = Annotated[Page, Depends(pagination)]
