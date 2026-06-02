"""Job status endpoint (docs/04-api.md)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..db import repos
from ..db.database import Database
from .models import JobOut
from .security import require_token

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(require_token)])


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, request: Request) -> JobOut:
    db: Database = request.app.state.db
    job = repos.get_job(db, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return JobOut(**job)
