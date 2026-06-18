"""Project endpoints (docs/04-api.md)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from .. import audit
from ..auth import Principal, require_permission
from ..config import Settings
from ..db import repos
from ..db.database import Database
from ..editing.otio_sync import rebuild_otio
from ..ingest.ffmpeg import run_ffmpeg
from ..scenes.materialize import materialize_scene
from ..timebase import FrameRate
from ..util import new_id
from .models import ProjectCreate, ProjectOut, RenameRequest
from .pagination import PageParams
from .security import require_token

router = APIRouter(prefix="/projects", tags=["projects"], dependencies=[Depends(require_token)])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _write_demo_clip(path: Path, _label: str, color: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s=1280x720:d=2:r=30",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(path),
    ])


def _create_demo_asset(
    db: Database,
    *,
    project_id: str,
    path: Path,
    display_name: str,
) -> dict[str, Any]:
    asset = repos.create_asset(
        db,
        project_id=project_id,
        type="video",
        display_name=display_name,
        source_path=str(path),
    )
    repos.update_asset_probe(
        db,
        asset["id"],
        type="video",
        duration_frames=60,
        rate_num=30,
        rate_den=1,
        audio_sample_rate=None,
        start_timecode=None,
        width=1280,
        height=720,
        codec_video="h264",
        codec_audio=None,
        is_vfr=False,
        sha256=None,
    )
    size_bytes = path.stat().st_size if path.exists() else None
    repos.add_asset_file(
        db,
        asset_id=asset["id"],
        kind="proxy",
        path=str(path),
        size_bytes=size_bytes,
        is_proxy=True,
    )
    return asset


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    body: ProjectCreate, request: Request,
    principal: Annotated[Principal, Depends(require_permission("project:write"))],
) -> ProjectOut:
    # Validate the frame rate via the time core (rejects e.g. drop-frame on 24p).
    try:
        FrameRate(body.sequence_rate_num, body.sequence_rate_den, body.drop_frame)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    settings = _settings(request)
    db = _db(request)
    pid = new_id()
    project_root = settings.workspace_root / f"project-{pid}"
    project_root.mkdir(parents=True, exist_ok=True)

    project = repos.create_project(
        db,
        project_id=pid,
        name=body.name,
        rate_num=body.sequence_rate_num,
        rate_den=body.sequence_rate_den,
        drop_frame=body.drop_frame,
        workspace_root=str(project_root),
        org_id=principal.org_id,
    )
    audit.record(db, principal, "project.create", entity_type="project", entity_id=pid)
    return ProjectOut(**project)


@router.post("/demo", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_demo_project(
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("project:write"))],
) -> ProjectOut:
    settings = _settings(request)
    db = _db(request)
    pid = new_id()
    project_root = settings.workspace_root / f"project-{pid}"
    media_root = project_root / "demo-media"
    project_root.mkdir(parents=True, exist_ok=True)

    clip_a = media_root / "laura-demo-blue.mp4"
    clip_b = media_root / "laura-demo-green.mp4"
    try:
        _write_demo_clip(clip_a, "Laura Demo 1", "0x0ea5e9")
        _write_demo_clip(clip_b, "Laura Demo 2", "0x22c55e")
    except Exception as exc:  # noqa: BLE001 - surface local ffmpeg/demo generation failures
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    project = repos.create_project(
        db,
        project_id=pid,
        name="Laura Demo",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(project_root),
        org_id=principal.org_id,
    )
    asset_a = _create_demo_asset(
        db, project_id=pid, path=clip_a, display_name="Demo Szene A"
    )
    asset_b = _create_demo_asset(
        db, project_id=pid, path=clip_b, display_name="Demo Szene B"
    )
    rough = repos.create_timeline(db, project_id=pid, name="Demo Rough Cut", kind="rough_cut")
    repos.replace_timeline_clips(
        db,
        rough["id"],
        [
            {
                "asset_id": asset_a["id"],
                "src_in_frame": 0,
                "src_out_frame_exclusive": 60,
                "seq_in_frame": 0,
                "seq_out_frame_exclusive": 60,
                "lane": 0,
                "speed_num": 1,
                "speed_den": 1,
            },
            {
                "asset_id": asset_b["id"],
                "src_in_frame": 0,
                "src_out_frame_exclusive": 60,
                "seq_in_frame": 60,
                "seq_out_frame_exclusive": 120,
                "lane": 0,
                "speed_num": 1,
                "speed_den": 1,
            },
        ],
    )
    repos.replace_scenes(db, pid, rough["id"], [(0, 60), (60, 120)])
    scenes = repos.list_scenes(db, rough["id"])
    for scene in scenes:
        materialize_scene(db, scene)
    sequence = repos.get_or_create_project_sequence(db, pid)
    repos.replace_sequence_items(db, sequence["id"], [scene["id"] for scene in scenes])
    items = repos.list_sequence_items(db, sequence["id"])
    if items:
        repos.update_sequence_item_transition(
            db,
            sequence["id"],
            items[0]["id"],
            kind="dip_black",
            duration_frames=12,
        )
    rebuild_otio(db, rough["id"])
    rebuild_otio(db, sequence["id"])
    audit.record(db, principal, "project.create_demo", entity_type="project", entity_id=pid)
    return ProjectOut(**project)


def _can_access(principal: Principal, project: dict[str, Any]) -> bool:
    """Local owner/admin see everything; org-scoped keys only their own org."""
    if principal.kind != "key":
        return True
    return project.get("org_id") == principal.org_id


def _load_project(request: Request, project_id: str, principal: Principal) -> dict[str, Any]:
    project = repos.get_project(_db(request), project_id)
    if project is None or not _can_access(principal, project):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return project


@router.get("", response_model=list[ProjectOut])
def list_projects(
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(require_permission("read"))],
    page: PageParams,
) -> list[ProjectOut]:
    # local owner (org_id None) -> all; org-scoped key -> only its org.
    db = _db(request)
    response.headers["X-Total-Count"] = str(repos.count_projects(db, org_id=principal.org_id))
    projects = repos.list_projects(
        db, org_id=principal.org_id, limit=page.limit, offset=page.offset
    )
    return [ProjectOut(**p) for p in projects]


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("read"))],
) -> ProjectOut:
    return ProjectOut(**_load_project(request, project_id, principal))


@router.patch("/{project_id}", response_model=ProjectOut)
def rename_project(
    project_id: str,
    body: RenameRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("project:write"))],
) -> ProjectOut:
    db = _db(request)
    _load_project(request, project_id, principal)
    repos.rename_project(db, project_id, body.name)
    audit.record(db, principal, "project.rename", entity_type="project", entity_id=project_id)
    updated = repos.get_project(db, project_id)
    assert updated is not None
    return ProjectOut(**updated)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_project(
    project_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("project:delete"))],
) -> Response:
    db = _db(request)
    _load_project(request, project_id, principal)
    repos.delete_project(db, project_id)
    audit.record(db, principal, "project.delete", entity_type="project", entity_id=project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
