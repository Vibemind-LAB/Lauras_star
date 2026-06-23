"""Overlay (replacement-lane) endpoints — add/remove a replace-role clip on a timeline.

v1 assumes a speed==1 base clip exists under the overlay for the given sequence range.
Retimed-base support (non-unity speed_num/speed_den) is deferred to a later iteration.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from ..db import repos
from ..db.database import Database
from ..editing.history import timeline_checkpoint
from .models import OverlayOut, OverlayRequest
from .security import require_token

router = APIRouter(tags=["overlays"], dependencies=[Depends(require_token)])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


@router.post(
    "/timelines/{timeline_id}/overlays",
    status_code=status.HTTP_201_CREATED,
    response_model=OverlayOut,
)
def add_overlay(
    timeline_id: str, body: OverlayRequest, request: Request
) -> OverlayOut:
    """Add a replacement-lane overlay clip to a timeline.

    The overlay asset must cover at least the requested sequence range (1:1 speed mapping).
    v1 assumes a speed==1 base clip exists under the overlay for the given sequence range.
    Retimed-base support is deferred to a later iteration.
    """
    db = _db(request)

    tl = repos.get_timeline(db, timeline_id)
    if tl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")

    asset = repos.get_asset(db, body.asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")

    if body.seq_out_frame_exclusive <= body.seq_in_frame:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "seq_out_frame_exclusive must be greater than seq_in_frame",
        )

    range_len = body.seq_out_frame_exclusive - body.seq_in_frame
    src_out = body.src_in_frame + range_len

    duration_frames: int | None = asset.get("duration_frames")
    if duration_frames is not None and duration_frames > 0 and src_out > duration_frames:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"asset too short: src_out {src_out} exceeds duration_frames {duration_frames}",
        )

    with timeline_checkpoint(db, timeline_id, "Overlay hinzugefügt"):
        clip = repos.add_timeline_clip(
            db,
            timeline_id=timeline_id,
            asset_id=body.asset_id,
            src_in_frame=body.src_in_frame,
            src_out_frame_exclusive=src_out,
            seq_in_frame=body.seq_in_frame,
            seq_out_frame_exclusive=body.seq_out_frame_exclusive,
            lane=body.lane,
            role="replace",
        )

    return OverlayOut(
        id=clip["id"],
        timeline_id=clip["timeline_id"],
        asset_id=clip["asset_id"],
        lane=clip["lane"],
        role=clip["role"],
        src_in_frame=clip["src_in_frame"],
        src_out_frame_exclusive=clip["src_out_frame_exclusive"],
        seq_in_frame=clip["seq_in_frame"],
        seq_out_frame_exclusive=clip["seq_out_frame_exclusive"],
    )


@router.delete(
    "/timelines/{timeline_id}/overlays/{clip_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_overlay(
    timeline_id: str, clip_id: str, request: Request
) -> Response:
    """Remove an overlay clip from a timeline by clip id."""
    db = _db(request)
    clip_exists = any(
        c["id"] == clip_id for c in repos.list_timeline_clips(db, timeline_id)
    )
    if not clip_exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "overlay clip not found")
    with timeline_checkpoint(db, timeline_id, "Overlay entfernt"):
        repos.delete_timeline_clip(db, clip_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
