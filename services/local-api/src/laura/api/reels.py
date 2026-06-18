"""Reel-render endpoint — mp4 export with social-reel options + render job enqueue."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..db import repos
from ..db.database import Database
from ..jobs.queues import queue_for
from ..jobs.runner import enqueue
from .models import ReelRenderRequest
from .security import require_token

router = APIRouter(tags=["reels"], dependencies=[Depends(require_token)])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


@router.post("/timelines/{timeline_id}/render-reel", status_code=status.HTTP_202_ACCEPTED)
def render_reel(
    timeline_id: str, body: ReelRenderRequest, request: Request
) -> dict[str, str]:
    """Enqueue an MP4 reel render job for a timeline and return the export record id.

    Mirrors ``render_timeline`` but carries social-reel options (vertical framing,
    hook text, disclosure text) forwarded by ``handle_render`` to the renderer.
    """
    db = _db(request)
    tl = repos.get_timeline(db, timeline_id)
    if tl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")
    exp = repos.create_export(
        db,
        project_id=tl["project_id"],
        timeline_id=timeline_id,
        format="mp4",
        options={
            "vertical": body.vertical,
            "hook_text": body.hook_text,
            "disclosure_text": body.disclosure_text,
            "captions": body.captions,
            "caption_preset": body.caption_preset,
            "caption_mode": body.caption_mode,
            "caption_position": body.caption_position,
            "caption_fontsize": body.caption_fontsize,
            "caption_safe_margin": body.caption_safe_margin,
            "max_duration_seconds": body.max_duration_seconds,
        },
    )
    job_id = enqueue(
        db,
        queue=queue_for("export.render"),
        kind="export.render",
        payload={"export_id": exp["id"]},
        idempotency_key=f"render:{exp['id']}",
    )
    return {"export_id": exp["id"], "job_id": job_id}
