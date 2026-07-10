"""NL-agent short-creator endpoint (Iteration 8).

``POST /assets/{asset_id}/auto-short`` enqueues a ``short_creator.run`` job that builds a short
about *topic* from the asset via the multi-agent escalation ladder. Requires the optional
``autoshort`` extra; without it the endpoint returns 503. Poll ``/jobs/{id}`` for completion.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import IO, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..auth import Principal, require_permission
from ..db import repos
from ..db.database import Database
from ..jobs.queues import queue_for
from ..jobs.runner import enqueue

logger = logging.getLogger(__name__)

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

    # Every event is ALSO appended to an NDJSON run log (flushed per line), so a run can be
    # debugged after the fact — the chat panel's copy dies with the window (live finding:
    # "paste me the chat ending" was the only way to diagnose a run).
    log_file: IO[str] | None = None
    log_dir = request.app.state.settings.workspace_root / "agent-runs"
    log_path = log_dir / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{asset_id[:8]}.ndjson"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("a", encoding="utf-8")
        logger.info("auto-short run log: %s", log_path)
    except OSError as exc:  # logging must never block the run
        logger.warning("auto-short run log unavailable (%s): %s", log_path, exc)

    def write_line(event: dict[str, Any]) -> bytes:
        line = json.dumps(event, ensure_ascii=False)
        if log_file is not None:
            try:
                log_file.write(line + "\n")
                log_file.flush()
            except OSError:
                pass  # never break the stream over a full disk
        return (line + "\n").encode("utf-8")

    async def events() -> AsyncIterator[bytes]:
        try:
            write_line(
                {
                    "type": "meta",
                    "asset_id": asset_id,
                    "topic": topic,
                    "target_seconds": target_seconds,
                    "provider": config.provider,
                    "agent_model": config.agent_model,
                }
            )
            try:
                async for event in run_short_creator_stream(
                    db, config, asset_id=asset_id, topic=topic, target_seconds=target_seconds
                ):
                    yield write_line(event)
            except Exception as exc:  # never leak a raw 500 mid-stream — emit a final error
                yield write_line({"type": "error", "message": str(exc)})
        except BaseException:  # client disconnect kills the run — make that visible in the log
            write_line({"type": "aborted", "reason": "client disconnected"})
            raise
        finally:
            if log_file is not None:
                log_file.close()

    return StreamingResponse(events(), media_type="application/x-ndjson")
