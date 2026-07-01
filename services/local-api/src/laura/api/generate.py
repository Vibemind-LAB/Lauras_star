"""Generative-video endpoint (Axis 2, Slice 1).

``POST /projects/{project_id}/generate-video`` enqueues a ``generate.video`` job that produces a
clip for the prompt and registers it as a synthetic project asset (``ai_effect="generate_video"``).
The asset joins the media pool; place it via the normal timeline ops. Poll ``/jobs/{id}`` for
completion. v1 uses a model-free stub backend — the real model (ComfyUI/LTX) is a later slice.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..auth import Principal, require_permission
from ..db import repos
from ..db.database import Database
from ..jobs.queues import queue_for
from ..jobs.runner import enqueue

router = APIRouter(tags=["generate"])


class GenerateVideoRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    duration_frames: int = Field(gt=0, le=100_000)


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


@router.post("/projects/{project_id}/generate-video", status_code=status.HTTP_202_ACCEPTED)
def generate_video(
    project_id: str,
    body: GenerateVideoRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """Enqueue a ``generate.video`` job for *project_id*. Returns ``{"job_id": ...}``."""
    db = _db(request)
    if repos.get_project(db, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    payload: dict[str, Any] = {
        "project_id": project_id,
        "prompt": body.prompt,
        "duration_frames": body.duration_frames,
    }
    job_id = enqueue(
        db,
        queue=queue_for("generate.video"),
        kind="generate.video",
        payload=payload,
        max_attempts=2,
    )
    return {"job_id": job_id}
