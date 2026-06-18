"""Sequence (stage 5) endpoints: arrange scenes into one final sequence and read the
flattened clip list. The sequence is a kind="sequence" timeline; its content is the ordered
scene references in `sequence_items`, flattened on demand."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..db import repos
from ..db.database import Database
from ..editing.otio_sync import rebuild_otio
from ..scenes.materialize import materialize_scene
from ..sequences.flatten import flatten_sequence
from ..sequences.transcript import sequence_transcript_blocks
from .models import (
    ClipOut,
    SequenceItemOut,
    SequenceOut,
    SequenceTranscriptBlockOut,
    SequenceTransitionRequest,
    SetSequenceScenesRequest,
)
from .security import require_token

router = APIRouter(tags=["sequences"], dependencies=[Depends(require_token)])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _sequence_out(db: Database, seq: dict[str, Any]) -> SequenceOut:
    items: list[SequenceItemOut] = []
    for it in repos.list_sequence_items(db, seq["id"]):
        scene = repos.get_scene(db, it["scene_id"])
        items.append(
            SequenceItemOut(
                id=it["id"],
                scene_id=it["scene_id"],
                scene_name=scene["name"] if scene is not None else "?",
                order_index=it["order_index"],
                transition_after_kind=str(it.get("transition_after_kind") or "hard"),
                transition_after_frames=int(it.get("transition_after_frames") or 0),
            )
        )
    return SequenceOut(timeline_id=seq["id"], project_id=seq["project_id"], items=items)


@router.get("/projects/{project_id}/sequence", response_model=SequenceOut)
def get_sequence(project_id: str, request: Request) -> SequenceOut:
    db = _db(request)
    if repos.get_project(db, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return _sequence_out(db, repos.get_or_create_project_sequence(db, project_id))


@router.put("/sequences/{sequence_id}/scenes", response_model=SequenceOut)
def set_sequence_scenes(
    sequence_id: str, body: SetSequenceScenesRequest, request: Request
) -> SequenceOut:
    db = _db(request)
    seq = repos.get_timeline(db, sequence_id)
    if seq is None or seq["kind"] != "sequence":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sequence not found")
    for sid in body.scene_ids:
        scene = repos.get_scene(db, sid)
        if scene is None or scene["project_id"] != seq["project_id"]:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown scene: {sid}"
            )
        materialize_scene(db, scene)
    repos.replace_sequence_items(db, sequence_id, body.scene_ids)
    rebuild_otio(db, sequence_id)
    return _sequence_out(db, seq)


@router.patch(
    "/sequences/{sequence_id}/items/{item_id}/transition",
    response_model=SequenceOut,
)
def update_sequence_transition(
    sequence_id: str,
    item_id: str,
    body: SequenceTransitionRequest,
    request: Request,
) -> SequenceOut:
    db = _db(request)
    seq = repos.get_timeline(db, sequence_id)
    if seq is None or seq["kind"] != "sequence":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sequence not found")
    allowed = {"hard", "dip_black", "fade_black", "crossfade"}
    kind = body.kind if body.kind in allowed else "hard"
    frames = 0 if kind == "hard" else body.duration_frames
    if not repos.update_sequence_item_transition(
        db,
        sequence_id,
        item_id,
        kind=kind,
        duration_frames=frames,
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sequence item not found")
    rebuild_otio(db, sequence_id)
    return _sequence_out(db, seq)


@router.get("/sequences/{sequence_id}/flattened", response_model=list[ClipOut])
def get_sequence_flattened(sequence_id: str, request: Request) -> list[ClipOut]:
    db = _db(request)
    if repos.get_timeline(db, sequence_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sequence not found")
    return [ClipOut(**c) for c in flatten_sequence(db, sequence_id)]


@router.get("/sequences/{sequence_id}/transcript", response_model=list[SequenceTranscriptBlockOut])
def get_sequence_transcript(sequence_id: str, request: Request) -> list[SequenceTranscriptBlockOut]:
    db = _db(request)
    seq = repos.get_timeline(db, sequence_id)
    if seq is None or seq["kind"] != "sequence":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sequence not found")
    return [SequenceTranscriptBlockOut(**b) for b in sequence_transcript_blocks(db, sequence_id)]
