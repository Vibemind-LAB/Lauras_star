"""Lipsync/deepfake job endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..db import repos
from ..db.database import Database
from ..jobs.queues import queue_for
from ..jobs.runner import enqueue
from .models import LipsyncAccepted, LipsyncRequest
from .security import require_token

router = APIRouter(tags=["lipsync"], dependencies=[Depends(require_token)])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _validate_runtime(db: Database, runtime_id: str | None, effect: str) -> None:
    if runtime_id is None:
        return
    runtime = repos.get_ai_runtime(db, runtime_id)
    if runtime is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "runtime not found")
    if runtime["effect"] != effect:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"runtime effect must be {effect}",
        )
    if not runtime["enabled"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "runtime is disabled")


@router.post(
    "/timelines/{timeline_id}/lipsync",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=LipsyncAccepted,
)
def create_lipsync(
    timeline_id: str,
    body: LipsyncRequest,
    request: Request,
) -> LipsyncAccepted:
    db = _db(request)
    timeline = repos.get_timeline(db, timeline_id)
    if timeline is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")

    _validate_runtime(db, body.runtime_id, "lipsync")

    audio = repos.get_asset(db, body.audio_asset_id)
    if audio is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "audio asset not found")
    if audio["project_id"] != timeline["project_id"]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "audio asset does not belong to this timeline project",
        )
    if audio.get("type") != "audio" and not audio.get("codec_audio"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "asset has no audio stream",
        )

    consent = repos.get_consent_record(db, body.consent_id)
    if consent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "consent record not found")
    if consent["project_id"] != timeline["project_id"]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "consent does not belong to this timeline project",
        )
    if consent.get("revoked_at") is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "consent has been revoked; obtain fresh consent before lipsync",
        )

    payload: dict[str, Any] = {
        "timeline_id": timeline_id,
        "seq_in_frame": body.seq_in_frame,
        "seq_out_frame_exclusive": body.seq_out_frame_exclusive,
        "audio_asset_id": body.audio_asset_id,
        "consent_id": body.consent_id,
        "license_accepted": body.license_accepted,
        "backend": body.backend,
        "quality_threshold": body.quality_threshold,
    }
    if body.runtime_id is not None:
        payload["runtime_id"] = body.runtime_id
    job_id = enqueue(
        db,
        queue=queue_for("ai.lipsync", default="ai"),
        kind="ai.lipsync",
        payload=payload,
        max_attempts=1,
    )
    return LipsyncAccepted(job_id=job_id)
