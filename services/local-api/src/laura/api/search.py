"""Transcript search (docs/04-api.md). Lexical now; FTS5/semantic later (docs/15)."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from ..auth import Principal, require_permission
from ..db import repos
from ..db.database import Database
from ..semantic import get_index
from .models import SearchRequest, SearchResult

logger = logging.getLogger(__name__)

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
    if body.mode == "semantic":
        try:
            index = get_index()
        except Exception:  # noqa: BLE001 - semantic search is best-effort: a down/unreachable
            # Qdrant server raises during client/collection construction; degrade to lexical
            # instead of bubbling a 500 out of this read endpoint.
            logger.warning("semantic index unavailable; falling back to lexical", exc_info=True)
            index = None
        if index is not None:  # else fall through to lexical (extra not installed / unavailable)
            hits = index.query(body.query, project_id=body.project_id, limit=body.limit)
            return [
                SearchResult(
                    segment_id=str(h["segment_id"]), asset_id=str(h["asset_id"]),
                    asset_name=str(h.get("asset_name", "")),
                    start_frame=int(h.get("start_frame", 0)),
                    end_frame=int(h.get("end_frame", 0)),
                    text=str(h.get("text", "")), speaker_label=h.get("speaker_label"),
                    score=h.get("score"),
                )
                for h in hits
            ]
    results = repos.search_transcript(
        _db(request), project_id=body.project_id, query=body.query, limit=body.limit
    )
    return [SearchResult(**r) for r in results]
