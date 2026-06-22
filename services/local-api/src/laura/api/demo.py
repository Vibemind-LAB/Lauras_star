"""Product-Demo Assistant endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from .. import PIPELINE_VERSION
from ..db import repos
from ..db.database import Database
from ..demo.drafts import build_demo_draft_items
from ..editing.otio_sync import rebuild_otio
from ..jobs.queues import queue_for
from ..jobs.runner import enqueue
from ..scenes.materialize import materialize_scene
from .models import (
    DemoDraftAccepted,
    DemoDraftApplyOut,
    DemoDraftItem,
    DemoDraftOut,
    DemoDraftUpdate,
    SequenceItemOut,
    SequenceOut,
)
from .security import require_token

router = APIRouter(tags=["demo"], dependencies=[Depends(require_token)])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _draft_out(draft: dict[str, Any]) -> DemoDraftOut:
    return DemoDraftOut(
        id=draft["id"],
        project_id=draft["project_id"],
        asset_id=draft["asset_id"],
        status=draft["status"],
        items=[DemoDraftItem(**item) for item in draft["items"]],
        result=draft["result"],
        created_at=draft["created_at"],
        updated_at=draft["updated_at"],
        applied_at=draft.get("applied_at"),
    )


def _sequence_out(db: Database, seq: dict[str, Any]) -> SequenceOut:
    items: list[SequenceItemOut] = []
    for item in repos.list_sequence_items(db, seq["id"]):
        scene = repos.get_scene(db, item["scene_id"])
        items.append(
            SequenceItemOut(
                id=item["id"],
                scene_id=item["scene_id"],
                scene_name=scene["name"] if scene is not None else "?",
                order_index=item["order_index"],
                transition_after_kind=str(item.get("transition_after_kind") or "hard"),
                transition_after_frames=int(item.get("transition_after_frames") or 0),
            )
        )
    return SequenceOut(timeline_id=seq["id"], project_id=seq["project_id"], items=items)


@router.post(
    "/assets/{asset_id}/demo-drafts",
    response_model=DemoDraftAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_demo_draft(asset_id: str, request: Request) -> DemoDraftAccepted:
    db = _db(request)
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    if asset["type"] != "video":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "demo draft requires a video asset",
        )
    draft = repos.create_demo_draft(db, project_id=asset["project_id"], asset_id=asset_id)
    job_id = enqueue(
        db,
        queue=queue_for("demo.analyze"),
        kind="demo.analyze",
        payload={"draft_id": draft["id"], "asset_id": asset_id},
        max_attempts=1,
        pipeline_version=PIPELINE_VERSION,
    )
    return DemoDraftAccepted(draft_id=draft["id"], job_id=job_id)


@router.get("/demo-drafts/{draft_id}", response_model=DemoDraftOut)
def get_demo_draft(draft_id: str, request: Request) -> DemoDraftOut:
    draft = repos.get_demo_draft(_db(request), draft_id)
    if draft is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "demo draft not found")
    return _draft_out(draft)


@router.patch("/demo-drafts/{draft_id}", response_model=DemoDraftOut)
def update_demo_draft(
    draft_id: str,
    body: DemoDraftUpdate,
    request: Request,
) -> DemoDraftOut:
    draft = repos.update_demo_draft(
        _db(request),
        draft_id,
        status="ready",
        items=[item.model_dump() for item in body.items],
    )
    if draft is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "demo draft not found")
    return _draft_out(draft)


@router.post("/demo-drafts/{draft_id}/apply", response_model=DemoDraftApplyOut)
def apply_demo_draft(draft_id: str, request: Request) -> DemoDraftApplyOut:
    db = _db(request)
    draft = repos.get_demo_draft(db, draft_id)
    if draft is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "demo draft not found")
    if repos.get_asset(db, draft["asset_id"]) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")

    enabled = [DemoDraftItem(**item) for item in draft["items"] if item.get("enabled", True)]
    if not enabled:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "demo draft has no enabled items",
        )

    rough = repos.create_timeline(
        db,
        project_id=draft["project_id"],
        name="Demo Draft",
        kind="rough_cut",
        created_from=draft["asset_id"],
    )
    clip_rows: list[dict[str, Any]] = []
    scene_ranges: list[tuple[int, int]] = []
    cursor = 0
    for item in enabled:
        duration = item.src_out_frame_exclusive - item.src_in_frame
        clip_rows.append(
            {
                "asset_id": draft["asset_id"],
                "src_in_frame": item.src_in_frame,
                "src_out_frame_exclusive": item.src_out_frame_exclusive,
                "seq_in_frame": cursor,
                "seq_out_frame_exclusive": cursor + duration,
                "lane": 0,
                "speed_num": 1,
                "speed_den": 1,
            }
        )
        scene_ranges.append((cursor, cursor + duration))
        cursor += duration

    repos.replace_timeline_clips(db, rough["id"], clip_rows)
    rebuild_otio(db, rough["id"])
    repos.replace_scenes(db, draft["project_id"], rough["id"], scene_ranges)
    scenes = repos.list_scenes(db, rough["id"])
    scene_ids: list[str] = []
    for scene, item in zip(scenes, enabled, strict=True):
        repos.update_scene_name(db, scene["id"], item.label)
        fresh_scene = repos.get_scene(db, scene["id"])
        assert fresh_scene is not None
        materialize_scene(db, fresh_scene)
        scene_ids.append(scene["id"])

    sequence = repos.get_or_create_project_sequence(db, draft["project_id"])
    repos.replace_sequence_items(db, sequence["id"], scene_ids)
    rebuild_otio(db, sequence["id"])

    updated = repos.update_demo_draft(
        db,
        draft_id,
        status="applied",
        result={
            "rough_cut_id": rough["id"],
            "sequence_id": sequence["id"],
            "scene_ids": scene_ids,
        },
        applied=True,
    )
    assert updated is not None
    fresh_sequence = repos.get_timeline(db, sequence["id"])
    assert fresh_sequence is not None
    return DemoDraftApplyOut(draft=_draft_out(updated), sequence=_sequence_out(db, fresh_sequence))


def build_preview_items(db: Database, asset_id: str) -> list[DemoDraftItem]:
    return [DemoDraftItem(**item) for item in build_demo_draft_items(db, asset_id)]
