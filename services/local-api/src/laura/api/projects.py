"""Project endpoints (docs/04-api.md)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from .. import audit
from ..auth import Principal, require_permission
from ..config import Settings
from ..db import repos
from ..db.database import Database
from ..timebase import FrameRate
from ..util import new_id
from .models import ProjectCreate, ProjectOut, RenameRequest
from .security import require_token

router = APIRouter(prefix="/projects", tags=["projects"], dependencies=[Depends(require_token)])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


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
    principal: Annotated[Principal, Depends(require_permission("read"))],
) -> list[ProjectOut]:
    # local owner (org_id None) -> all; org-scoped key -> only its org.
    projects = repos.list_projects(_db(request), org_id=principal.org_id)
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


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("project:delete"))],
) -> None:
    db = _db(request)
    _load_project(request, project_id, principal)
    repos.delete_project(db, project_id)
    audit.record(db, principal, "project.delete", entity_type="project", entity_id=project_id)
