"""Project endpoints (docs/04-api.md)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..config import Settings
from ..db import repos
from ..db.database import Database
from ..timebase import FrameRate
from ..util import new_id
from .models import ProjectCreate, ProjectOut
from .security import require_token

router = APIRouter(prefix="/projects", tags=["projects"], dependencies=[Depends(require_token)])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, request: Request) -> ProjectOut:
    # Validate the frame rate via the time core (rejects e.g. drop-frame on 24p).
    try:
        FrameRate(body.sequence_rate_num, body.sequence_rate_den, body.drop_frame)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    settings = _settings(request)
    pid = new_id()
    project_root = settings.workspace_root / f"project-{pid}"
    project_root.mkdir(parents=True, exist_ok=True)

    project = repos.create_project(
        _db(request),
        project_id=pid,
        name=body.name,
        rate_num=body.sequence_rate_num,
        rate_den=body.sequence_rate_den,
        drop_frame=body.drop_frame,
        workspace_root=str(project_root),
    )
    return ProjectOut(**project)


@router.get("", response_model=list[ProjectOut])
def list_projects(request: Request) -> list[ProjectOut]:
    return [ProjectOut(**p) for p in repos.list_projects(_db(request))]


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, request: Request) -> ProjectOut:
    project = repos.get_project(_db(request), project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return ProjectOut(**project)
