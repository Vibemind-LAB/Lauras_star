"""Timeline audio-lane endpoints for sequence music and voiceover clips."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import ValidationError

from ..db import repos
from ..db.database import Database
from ..editing.history import timeline_checkpoint
from .models import TimelineAudioClipCreate, TimelineAudioClipOut, TimelineAudioClipUpdate
from .security import require_token

router = APIRouter(tags=["audio"], dependencies=[Depends(require_token)])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _timeline_or_404(db: Database, timeline_id: str) -> dict[str, Any]:
    timeline = repos.get_timeline(db, timeline_id)
    if timeline is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")
    return timeline


def _asset_or_404(db: Database, asset_id: str) -> dict[str, Any]:
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    if asset.get("type") != "audio" and not asset.get("codec_audio"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "asset has no audio stream",
        )
    return asset


def _validated_update(
    current: dict[str, Any],
    body: TimelineAudioClipUpdate,
) -> dict[str, Any]:
    patch = body.model_dump(exclude_unset=True)
    merged = {
        "asset_id": current["asset_id"],
        "seq_in_frame": current["seq_in_frame"],
        "seq_out_frame_exclusive": current["seq_out_frame_exclusive"],
        "asset_in_frame": current["asset_in_frame"],
        "gain_percent": current["gain_percent"],
        "fade_in_frames": current["fade_in_frames"],
        "fade_out_frames": current["fade_out_frames"],
        "mix_mode": current["mix_mode"],
        "ducking_percent": current["ducking_percent"],
        "label": current.get("label"),
        **patch,
    }
    try:
        TimelineAudioClipCreate(**merged)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.errors()) from exc
    return patch


@router.get(
    "/timelines/{timeline_id}/audio-clips",
    response_model=list[TimelineAudioClipOut],
)
def list_audio_clips(timeline_id: str, request: Request) -> list[TimelineAudioClipOut]:
    db = _db(request)
    _timeline_or_404(db, timeline_id)
    return [
        TimelineAudioClipOut(**clip)
        for clip in repos.list_timeline_audio_clips(db, timeline_id)
    ]


@router.post(
    "/timelines/{timeline_id}/audio-clips",
    status_code=status.HTTP_201_CREATED,
    response_model=TimelineAudioClipOut,
)
def create_audio_clip(
    timeline_id: str,
    body: TimelineAudioClipCreate,
    request: Request,
) -> TimelineAudioClipOut:
    db = _db(request)
    _timeline_or_404(db, timeline_id)
    _asset_or_404(db, body.asset_id)
    with timeline_checkpoint(db, timeline_id, "Audio hinzugefügt"):
        clip = repos.add_timeline_audio_clip(
            db,
            timeline_id=timeline_id,
            asset_id=body.asset_id,
            seq_in_frame=body.seq_in_frame,
            seq_out_frame_exclusive=body.seq_out_frame_exclusive,
            asset_in_frame=body.asset_in_frame,
            gain_percent=body.gain_percent,
            fade_in_frames=body.fade_in_frames,
            fade_out_frames=body.fade_out_frames,
            mix_mode=body.mix_mode,
            ducking_percent=body.ducking_percent,
            label=body.label,
        )
    return TimelineAudioClipOut(**clip)


@router.patch(
    "/timelines/{timeline_id}/audio-clips/{clip_id}",
    response_model=TimelineAudioClipOut,
)
def update_audio_clip(
    timeline_id: str,
    clip_id: str,
    body: TimelineAudioClipUpdate,
    request: Request,
) -> TimelineAudioClipOut:
    db = _db(request)
    _timeline_or_404(db, timeline_id)
    current = repos.get_timeline_audio_clip(db, clip_id)
    if current is None or current["timeline_id"] != timeline_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "audio clip not found")
    patch = _validated_update(current, body)
    with timeline_checkpoint(db, timeline_id, "Audio geändert"):
        updated = repos.update_timeline_audio_clip(db, clip_id, **patch)
    assert updated is not None
    return TimelineAudioClipOut(**updated)


@router.delete(
    "/timelines/{timeline_id}/audio-clips/{clip_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_audio_clip(timeline_id: str, clip_id: str, request: Request) -> Response:
    db = _db(request)
    _timeline_or_404(db, timeline_id)
    current = repos.get_timeline_audio_clip(db, clip_id)
    if current is None or current["timeline_id"] != timeline_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "audio clip not found")
    with timeline_checkpoint(db, timeline_id, "Audio gelöscht"):
        repos.delete_timeline_audio_clip(db, clip_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
