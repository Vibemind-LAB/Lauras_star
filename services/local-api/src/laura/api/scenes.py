"""Scene endpoints: group a rough-cut's clips into scenes and adjust their boundaries.

Scenes are a lightweight marker layer over an existing rough_cut timeline (see
docs/superpowers/specs/2026-06-08-rough-cut-stage-design.md). Generation only *groups*
existing clips — building the rough-cut is the existing /timelines/from-shots endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..db import repos
from ..db.database import Database
from ..editing.history import timeline_checkpoint
from ..editing.operations import EditClip, ordered, split_clip
from ..editing.otio_sync import serialize_timeline_otio
from ..scenes.build import (
    default_gap_frames,
    group_timeline_scenes,
    populate_rough_cut_from_shots,
)
from ..scenes.materialize import materialize_scene
from .models import (
    ClipOut,
    CutAtFrameRequest,
    GenerateScenesRequest,
    MergeScenesRequest,
    RenameSceneRequest,
    SceneOut,
    SetSceneMusicRequest,
    SplitSceneRequest,
    TimelineOut,
)
from .security import require_token

router = APIRouter(tags=["scenes"], dependencies=[Depends(require_token)])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


@router.post("/timelines/{timeline_id}/scenes:generate", response_model=list[SceneOut])
def generate_scenes(
    timeline_id: str, body: GenerateScenesRequest, request: Request
) -> list[SceneOut]:
    db = _db(request)
    tl = repos.get_timeline(db, timeline_id)
    if tl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")
    asset = repos.get_asset(db, body.asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    clips = repos.list_timeline_clips(db, timeline_id)
    if not clips:
        # Auto-build a minimal rough cut so generation is never a dead-end. This is the only
        # part that needs shots, and it needs the run that HAS them -- a corpse on top of a
        # good run carries none. The richer build lives in /timelines/from-shots.
        shots_run = repos.get_latest_shots_run(db, body.asset_id)
        if shots_run is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "asset has no analysis run"
            )
        clips = populate_rough_cut_from_shots(db, timeline_id, body.asset_id, shots_run["id"])
        if not clips:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "asset has no kept shots to build a rough cut from",
            )
    gap = body.gap_frames if body.gap_frames is not None else default_gap_frames(asset)
    # Grouping reads the transcript, which can live on a different run than the shots.
    transcript_run = repos.get_latest_transcript_run(db, body.asset_id)
    with timeline_checkpoint(db, timeline_id, "Szenen erzeugt"):
        group_timeline_scenes(
            db,
            project_id=tl["project_id"],
            timeline_id=timeline_id,
            asset=asset,
            run_id=str(transcript_run["id"]) if transcript_run is not None else None,
            clips=clips,
            gap_frames=gap,
        )
    return [SceneOut(**s) for s in repos.list_scenes(db, timeline_id)]


@router.get("/timelines/{timeline_id}/scenes", response_model=list[SceneOut])
def list_scenes(timeline_id: str, request: Request) -> list[SceneOut]:
    db = _db(request)
    if repos.get_timeline(db, timeline_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")
    return [SceneOut(**s) for s in repos.list_scenes(db, timeline_id)]


@router.post("/timelines/{timeline_id}/scenes/{scene_id}/split", response_model=list[SceneOut])
def split_scene(
    timeline_id: str, scene_id: str, body: SplitSceneRequest, request: Request
) -> list[SceneOut]:
    db = _db(request)
    scene = repos.get_scene(db, scene_id)
    if scene is None or scene["source_timeline_id"] != timeline_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scene not found")
    at = body.at_seq_frame
    if not (scene["seq_in_frame"] < at < scene["seq_out_frame_exclusive"]):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "split point outside scene")
    boundaries = {c["seq_in_frame"] for c in repos.list_timeline_clips(db, timeline_id)}
    if at not in boundaries:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "split point is not a clip boundary"
        )
    ranges: list[tuple[int, int]] = []
    for s in repos.list_scenes(db, timeline_id):
        if s["id"] == scene_id:
            ranges.append((s["seq_in_frame"], at))
            ranges.append((at, s["seq_out_frame_exclusive"]))
        else:
            ranges.append((s["seq_in_frame"], s["seq_out_frame_exclusive"]))
    with timeline_checkpoint(db, timeline_id, "Szene geteilt"):
        repos.replace_scenes(db, scene["project_id"], timeline_id, ranges)
    return [SceneOut(**s) for s in repos.list_scenes(db, timeline_id)]


@router.post("/timelines/{timeline_id}/cut-at-frame")
def cut_at_frame(
    timeline_id: str, body: CutAtFrameRequest, request: Request
) -> dict[str, Any]:
    """Composite cut: split the lane-0 clip at ``at_seq_frame`` (if mid-clip), then split
    the scene that strictly contains that frame at the resulting boundary.

    Guarantees a valid clip boundary before the scene split so the result is always
    consistent (Laura invariant: scene boundaries must land on clip edges).
    Idempotent: if ``at_seq_frame`` is already a clip+scene boundary this is a no-op.
    """
    db = _db(request)
    tl = repos.get_timeline(db, timeline_id)
    if tl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")
    at = body.at_seq_frame

    with timeline_checkpoint(db, timeline_id, "Schnitt gesetzt"):
        # --- Phase 1: ensure a clip boundary exists at `at` ----------------------------
        clips = [EditClip.from_row(c) for c in repos.list_timeline_clips(db, timeline_id)]
        boundaries = {c.seq_in_frame for c in clips} | {c.seq_out_frame_exclusive for c in clips}
        if at not in boundaries:
            # Frame is inside a clip — split it to create the boundary
            # (integer frames, invariant #1).
            try:
                clips = split_clip(clips, at)
            except ValueError as exc:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
            repos.replace_timeline_clips(db, timeline_id, [c.to_row() for c in ordered(clips)])
            fresh = repos.get_timeline(db, timeline_id)
            assert fresh is not None
            repos.update_timeline_otio(db, timeline_id, serialize_timeline_otio(db, fresh))

        # --- Phase 2: split the scene that strictly contains `at` ----------------------
        # If `at` is already a scene boundary, return the current state (idempotent — no error).
        all_scenes = repos.list_scenes(db, timeline_id)
        scene_boundaries: set[int] = set()
        for s in all_scenes:
            scene_boundaries.add(int(s["seq_in_frame"]))
            scene_boundaries.add(int(s["seq_out_frame_exclusive"]))
        if at in scene_boundaries:
            return {
                "clips": [
                    ClipOut(**c).model_dump()
                    for c in repos.list_timeline_clips(db, timeline_id)
                ],
                "scenes": [SceneOut(**s).model_dump() for s in all_scenes],
            }

        scene = next(
            (
                s
                for s in all_scenes
                if s["seq_in_frame"] < at < s["seq_out_frame_exclusive"]
            ),
            None,
        )
        if scene is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "cut point is not inside a scene"
            )
        ranges: list[tuple[int, int]] = []
        for s in repos.list_scenes(db, timeline_id):
            if s["id"] == scene["id"]:
                ranges.append((s["seq_in_frame"], at))
                ranges.append((at, s["seq_out_frame_exclusive"]))
            else:
                ranges.append((s["seq_in_frame"], s["seq_out_frame_exclusive"]))
        repos.replace_scenes(db, tl["project_id"], timeline_id, ranges)

    return {
        "clips": [ClipOut(**c).model_dump() for c in repos.list_timeline_clips(db, timeline_id)],
        "scenes": [SceneOut(**s).model_dump() for s in repos.list_scenes(db, timeline_id)],
    }


@router.post("/timelines/{timeline_id}/scenes/merge", response_model=list[SceneOut])
def merge_scenes(
    timeline_id: str, body: MergeScenesRequest, request: Request
) -> list[SceneOut]:
    db = _db(request)
    scene = repos.get_scene(db, body.scene_id)
    if scene is None or scene["source_timeline_id"] != timeline_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scene not found")
    scenes = repos.list_scenes(db, timeline_id)
    idx = next((i for i, s in enumerate(scenes) if s["id"] == body.scene_id), None)
    if idx is None or idx == len(scenes) - 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "no following scene to merge")
    ranges: list[tuple[int, int]] = []
    i = 0
    while i < len(scenes):
        if i == idx:
            ranges.append((scenes[i]["seq_in_frame"], scenes[i + 1]["seq_out_frame_exclusive"]))
            i += 2
        else:
            ranges.append((scenes[i]["seq_in_frame"], scenes[i]["seq_out_frame_exclusive"]))
            i += 1
    with timeline_checkpoint(db, timeline_id, "Szenen verbunden"):
        repos.replace_scenes(db, scene["project_id"], timeline_id, ranges)
    return [SceneOut(**s) for s in repos.list_scenes(db, timeline_id)]


@router.patch("/scenes/{scene_id}", response_model=SceneOut)
def rename_scene(scene_id: str, body: RenameSceneRequest, request: Request) -> SceneOut:
    db = _db(request)
    scene = repos.get_scene(db, scene_id)
    if scene is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scene not found")
    tid = scene["source_timeline_id"]
    with timeline_checkpoint(db, tid, "Szene umbenannt"):
        repos.update_scene_name(db, scene_id, body.name)
    updated = repos.get_scene(db, scene_id)
    assert updated is not None
    return SceneOut(**updated)


def _timeline_out(db: Database, row: dict[str, Any]) -> TimelineOut:
    clips = [ClipOut(**c) for c in repos.list_timeline_clips(db, row["id"])]
    return TimelineOut(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        kind=row["kind"],
        created_at=row["created_at"],
        clips=clips,
    )


@router.post("/scenes/{scene_id}/open", response_model=TimelineOut)
def open_scene(scene_id: str, request: Request) -> TimelineOut:
    db = _db(request)
    scene = repos.get_scene(db, scene_id)
    if scene is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scene not found")
    return _timeline_out(db, materialize_scene(db, scene))


@router.put("/scenes/{scene_id}/music", response_model=SceneOut)
def set_scene_music(scene_id: str, body: SetSceneMusicRequest, request: Request) -> SceneOut:
    db = _db(request)
    scene = repos.get_scene(db, scene_id)
    if scene is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scene not found")
    if repos.get_asset(db, body.asset_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    tid = scene["source_timeline_id"]
    with timeline_checkpoint(db, tid, "Musik geändert"):
        repos.set_scene_music(db, scene_id, body.asset_id, body.gain_percent)
    updated = repos.get_scene(db, scene_id)
    assert updated is not None
    return SceneOut(**updated)


@router.delete("/scenes/{scene_id}/music", response_model=SceneOut)
def clear_scene_music(scene_id: str, request: Request) -> SceneOut:
    db = _db(request)
    scene = repos.get_scene(db, scene_id)
    if scene is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scene not found")
    tid = scene["source_timeline_id"]
    with timeline_checkpoint(db, tid, "Musik entfernt"):
        repos.clear_scene_music(db, scene_id)
    updated = repos.get_scene(db, scene_id)
    assert updated is not None
    return SceneOut(**updated)


@router.get(
    "/projects/{project_id}/assets/{asset_id}/rough-cut",
    response_model=TimelineOut,
)
def get_rough_cut(project_id: str, asset_id: str, request: Request) -> TimelineOut:
    """Return (or lazily create) the rough-cut timeline for one asset in a project."""
    db = _db(request)
    if repos.get_project(db, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    row = repos.get_or_create_asset_rough_cut(db, project_id, asset_id)
    return _timeline_out(db, row)


@router.get(
    "/projects/{project_id}/scenes",
    response_model=list[SceneOut],
)
def list_project_scenes_ep(project_id: str, request: Request) -> list[SceneOut]:
    """All scenes across every rough-cut timeline in a project."""
    db = _db(request)
    if repos.get_project(db, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return [SceneOut(**s) for s in repos.list_project_scenes(db, project_id)]
