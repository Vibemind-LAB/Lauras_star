from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ..ai.runtime_manager import refresh_runtime, start_runtime, stop_runtime
from ..db import repos
from ..db.database import Database
from .models import (
    AiPersonaCreate,
    AiPersonaOut,
    AiRuntimeCreate,
    AiRuntimeEventOut,
    AiRuntimeOut,
)
from .security import require_token

router = APIRouter(tags=["ai-persona"], dependencies=[Depends(require_token)])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _require_reference_asset(
    db: Database,
    asset_id: str | None,
    *,
    field_label: str,
    project_id: str,
) -> None:
    if asset_id is None:
        return
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{field_label} asset not found")
    if asset["project_id"] != project_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{field_label} asset belongs to another project",
        )


@router.post("/ai/runtimes", response_model=AiRuntimeOut, status_code=status.HTTP_201_CREATED)
def create_runtime(body: AiRuntimeCreate, request: Request) -> AiRuntimeOut:
    runtime = repos.create_ai_runtime(_db(request), **body.model_dump())
    return AiRuntimeOut(**runtime)


@router.get("/ai/runtimes", response_model=list[AiRuntimeOut])
def list_runtimes(
    request: Request, effect: str | None = Query(default=None)
) -> list[AiRuntimeOut]:
    rows = repos.list_ai_runtimes(_db(request), effect=effect)
    return [AiRuntimeOut(**row) for row in rows]


@router.post("/ai/runtimes/{runtime_id}/refresh", response_model=AiRuntimeOut)
def refresh_runtime_route(runtime_id: str, request: Request) -> AiRuntimeOut:
    try:
        runtime = refresh_runtime(_db(request), runtime_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return AiRuntimeOut(**runtime)


@router.post("/ai/runtimes/{runtime_id}/start", response_model=AiRuntimeOut)
def start_runtime_route(runtime_id: str, request: Request) -> AiRuntimeOut:
    try:
        runtime = start_runtime(_db(request), runtime_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return AiRuntimeOut(**runtime)


@router.post("/ai/runtimes/{runtime_id}/stop", response_model=AiRuntimeOut)
def stop_runtime_route(runtime_id: str, request: Request) -> AiRuntimeOut:
    try:
        runtime = stop_runtime(_db(request), runtime_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return AiRuntimeOut(**runtime)


@router.get("/ai/runtimes/{runtime_id}/events", response_model=list[AiRuntimeEventOut])
def list_runtime_events(runtime_id: str, request: Request) -> list[AiRuntimeEventOut]:
    if repos.get_ai_runtime(_db(request), runtime_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "runtime not found")
    rows = repos.list_ai_runtime_events(_db(request), runtime_id)
    return [AiRuntimeEventOut(**row) for row in rows]


@router.post("/ai/personas", response_model=AiPersonaOut, status_code=status.HTTP_201_CREATED)
def create_persona(body: AiPersonaCreate, request: Request) -> AiPersonaOut:
    db = _db(request)
    consent = repos.get_consent_record(db, body.consent_id)
    if consent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "consent record not found")
    if consent.get("revoked_at") is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "consent has been revoked")
    if body.project_id is not None and consent["project_id"] != body.project_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "consent belongs to another project",
        )
    project_id = str(consent["project_id"])
    _require_reference_asset(
        db,
        body.face_reference_asset_id,
        field_label="face reference",
        project_id=project_id,
    )
    _require_reference_asset(
        db,
        body.voice_reference_asset_id,
        field_label="voice reference",
        project_id=project_id,
    )
    persona = repos.create_ai_persona(
        db,
        **body.model_dump(exclude={"project_id"}),
        project_id=project_id,
    )
    return AiPersonaOut(**persona)


@router.get("/ai/personas", response_model=list[AiPersonaOut])
def list_personas(
    request: Request, project_id: str | None = Query(default=None)
) -> list[AiPersonaOut]:
    rows = repos.list_ai_personas(_db(request), project_id=project_id)
    return [AiPersonaOut(**row) for row in rows]
