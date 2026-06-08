"""Scene endpoints: group a rough-cut's clips into scenes and adjust their boundaries.

Scenes are a lightweight marker layer over an existing rough_cut timeline (see
docs/superpowers/specs/2026-06-08-rough-cut-stage-design.md). Generation only *groups*
existing clips — building the rough-cut is the existing /timelines/from-shots endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..db import repos
from ..db.database import Database
from ..scenes.grouping import group_into_scenes
from ..scenes.materialize import materialize_scene
from .models import (
    ClipOut,
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


def _asset_words(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for seg in transcript:
        spk = seg.get("speaker_id")
        for w in seg["words"]:
            words.append(
                {"start_frame": w["start_frame"], "end_frame": w["end_frame"], "speaker": spk}
            )
    words.sort(key=lambda w: w["start_frame"])
    return words


def _assign_words(
    clips: list[dict[str, Any]], words: list[dict[str, Any]]
) -> list[list[dict[str, Any]]]:
    out: list[list[dict[str, Any]]] = []
    for c in clips:
        lo, hi = c["src_in_frame"], c["src_out_frame_exclusive"]
        out.append([w for w in words if w["start_frame"] < hi and w["end_frame"] > lo])
    return out


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
    run = repos.get_latest_analysis_run(db, body.asset_id)
    if run is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "asset has no analysis run")
    clips = repos.list_timeline_clips(db, timeline_id)
    if not clips:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "rough cut is empty; build it from shots first",
        )
    words = _asset_words(repos.get_transcript(db, body.asset_id, run["id"]))
    words_by_clip = _assign_words(clips, words)
    if body.gap_frames is not None:
        gap = body.gap_frames
    else:
        gap = round(1.5 * (asset["rate_num"] or 30) / (asset["rate_den"] or 1))
    ranges = group_into_scenes(clips, words_by_clip, gap_frames=gap)
    repos.replace_scenes(db, tl["project_id"], timeline_id, ranges)
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
    repos.replace_scenes(db, scene["project_id"], timeline_id, ranges)
    return [SceneOut(**s) for s in repos.list_scenes(db, timeline_id)]


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
    repos.replace_scenes(db, scene["project_id"], timeline_id, ranges)
    return [SceneOut(**s) for s in repos.list_scenes(db, timeline_id)]


@router.patch("/scenes/{scene_id}", response_model=SceneOut)
def rename_scene(scene_id: str, body: RenameSceneRequest, request: Request) -> SceneOut:
    db = _db(request)
    if repos.get_scene(db, scene_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scene not found")
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
