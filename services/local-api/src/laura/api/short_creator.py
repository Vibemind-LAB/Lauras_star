"""NL-agent short-creator endpoint (Iteration 8).

``POST /assets/{asset_id}/auto-short`` enqueues a ``short_creator.run`` job that builds a short
about *topic* from the asset via the multi-agent escalation ladder. Requires the optional
``autoshort`` extra; without it the endpoint returns 503. Poll ``/jobs/{id}`` for completion.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..auth import Principal, require_permission
from ..db import repos
from ..db.database import Database
from ..jobs.queues import queue_for
from ..jobs.runner import enqueue

router = APIRouter(tags=["short-creator"])


class AutoShortRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=2000)
    target_seconds: int = Field(default=60, gt=0, le=600)


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _autoshort_available() -> bool:
    """True when the optional ``autoshort`` extra (AutoGen) is importable."""
    try:
        import autogen_agentchat  # noqa: F401
    except ImportError:
        return False
    return True


@router.post("/assets/{asset_id}/auto-short", status_code=status.HTTP_202_ACCEPTED)
def auto_short(
    asset_id: str,
    body: AutoShortRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """Enqueue a ``short_creator.run`` job for *asset_id* (503 if the extra is missing)."""
    db = _db(request)
    if repos.get_asset(db, asset_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    if not _autoshort_available():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The 'autoshort' extra is not installed. Run: uv sync --extra autoshort",
        )
    payload: dict[str, Any] = {
        "asset_id": asset_id,
        "topic": body.topic,
        "target_seconds": body.target_seconds,
    }
    # LLM runs are expensive + non-idempotent — do not auto-retry.
    job_id = enqueue(
        db,
        queue=queue_for("short_creator.run"),
        kind="short_creator.run",
        payload=payload,
        max_attempts=1,
    )
    return {"job_id": job_id}


@router.post("/assets/{asset_id}/auto-short/stream")
def auto_short_stream(
    asset_id: str,
    body: AutoShortRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> StreamingResponse:
    """Run the short-creator live and stream normalized NDJSON events (one JSON object per line).

    The run is bound to this connection (closing it stops the run) — that is the "watch it live"
    behavior. 404 (asset) is checked before 503 (missing extra), both before streaming starts.
    """
    db = _db(request)
    if repos.get_asset(db, asset_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    if not _autoshort_available():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The 'autoshort' extra is not installed. Run: uv sync --extra autoshort",
        )
    from ..short_creator.providers import resolve_from_env
    from ..short_creator.stream import run_short_creator_stream

    config = resolve_from_env()
    topic, target_seconds = body.topic, body.target_seconds

    async def events() -> AsyncIterator[bytes]:
        try:
            async for event in run_short_creator_stream(
                db, config, asset_id=asset_id, topic=topic, target_seconds=target_seconds
            ):
                yield (json.dumps(event) + "\n").encode("utf-8")
        except Exception as exc:  # never leak a raw 500 mid-stream — emit a final error event
            yield (json.dumps({"type": "error", "message": str(exc)}) + "\n").encode("utf-8")

    return StreamingResponse(events(), media_type="application/x-ndjson")
