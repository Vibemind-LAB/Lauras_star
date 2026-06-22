from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ..ai.runtime_manager import refresh_runtime, start_runtime, stop_runtime
from ..db import repos
from ..db.database import Database
from .models import AiRuntimeCreate, AiRuntimeEventOut, AiRuntimeOut
from .security import require_token

router = APIRouter(tags=["ai-runtimes"], dependencies=[Depends(require_token)])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


@router.post("/ai/runtimes", response_model=AiRuntimeOut, status_code=status.HTTP_201_CREATED)
def create_runtime(body: AiRuntimeCreate, request: Request) -> AiRuntimeOut:
    runtime = repos.create_ai_runtime(_db(request), **body.model_dump())
    return AiRuntimeOut(**runtime)


@router.get("/ai/runtimes", response_model=list[AiRuntimeOut])
def list_runtimes(
    request: Request, effect: str | None = Query(default=None)
) -> list[AiRuntimeOut]:
    return [AiRuntimeOut(**row) for row in repos.list_ai_runtimes(_db(request), effect=effect)]


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
    return [
        AiRuntimeEventOut(**row)
        for row in repos.list_ai_runtime_events(_db(request), runtime_id)
    ]
