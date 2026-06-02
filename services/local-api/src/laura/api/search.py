"""Transcript search (docs/04-api.md). Lexical now; FTS5/semantic later (docs/15)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from ..auth import Principal, require_permission
from ..db import repos
from ..db.database import Database
from .models import SearchRequest, SearchResult

router = APIRouter(tags=["search"])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


@router.post("/search", response_model=list[SearchResult])
def search(
    body: SearchRequest,
    request: Request,
    _principal: Annotated[Principal, Depends(require_permission("read"))],
) -> list[SearchResult]:
    results = repos.search_transcript(
        _db(request), project_id=body.project_id, query=body.query, limit=body.limit
    )
    return [SearchResult(**r) for r in results]
