"""Voiceover/TTS job endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..ai.voiceover_backend import list_sapi_voices
from ..db import repos
from ..db.database import Database
from ..jobs.queues import queue_for
from ..jobs.runner import enqueue
from .models import VoiceoverAccepted, VoiceoverRequest
from .security import require_token

router = APIRouter(tags=["voiceover"], dependencies=[Depends(require_token)])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


@router.post(
    "/timelines/{timeline_id}/voiceover",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=VoiceoverAccepted,
)
def create_voiceover(
    timeline_id: str,
    body: VoiceoverRequest,
    request: Request,
) -> VoiceoverAccepted:
    """Enqueue an ``ai.voiceover`` job for a sequence range."""
    db = _db(request)
    timeline = repos.get_timeline(db, timeline_id)
    if timeline is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")

    if body.segment_id is not None:
        segment = repos.get_segment(db, body.segment_id)
        if segment is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "segment not found")
        asset = repos.get_asset(db, segment["asset_id"])
        if asset is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "segment asset not found")
        if asset["project_id"] != timeline["project_id"]:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "segment does not belong to this timeline project",
            )

    payload: dict[str, Any] = {
        "timeline_id": timeline_id,
        "segment_id": body.segment_id,
        "text": body.text,
        "seq_in_frame": body.seq_in_frame,
        "seq_out_frame_exclusive": body.seq_out_frame_exclusive,
        "language": body.language,
        "backend": body.backend,
        "gain_percent": body.gain_percent,
        "fade_in_frames": body.fade_in_frames,
        "fade_out_frames": body.fade_out_frames,
        "voice_id": body.voice_id,
        "mix_mode": body.mix_mode,
        "ducking_percent": body.ducking_percent,
    }
    job_id = enqueue(
        db,
        queue=queue_for("ai.voiceover", default="ai"),
        kind="ai.voiceover",
        payload=payload,
        max_attempts=1,
    )
    return VoiceoverAccepted(job_id=job_id)


@router.get("/voiceover/voices", response_model=list[dict[str, str]])
def list_voiceover_voices() -> list[dict[str, str]]:
    """Installed local TTS voices (Windows SAPI) as ``[{name, culture, gender}]``.

    Empty when no local voices are available — the UI then hides the picker and the backend
    uses the system default voice (or the dependency-free stub tone).
    """
    return list_sapi_voices()
