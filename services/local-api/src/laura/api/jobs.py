"""Job status endpoint (docs/04-api.md)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..db import repos
from ..db.database import Database
from ..jobs.runner import enqueue
from .models import JobAccepted, JobOut
from .security import require_token

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(require_token)])


@router.get("", response_model=list[JobOut])
def list_jobs(request: Request, limit: int = 50) -> list[JobOut]:
    db: Database = request.app.state.db
    safe_limit = max(1, min(200, limit))
    return [JobOut(**job) for job in repos.list_jobs(db, limit=safe_limit)]


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, request: Request) -> JobOut:
    db: Database = request.app.state.db
    job = repos.get_job(db, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return JobOut(**job)


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: str, request: Request) -> JobOut:
    db: Database = request.app.state.db
    if not repos.cancel_job(db, job_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    job = repos.get_job(db, job_id)
    assert job is not None
    return JobOut(**job)


@router.post("/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED, response_model=JobAccepted)
def retry_job(job_id: str, request: Request) -> JobAccepted:
    db: Database = request.app.state.db
    job = repos.get_job(db, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    if job["status"] != "failed":
        raise HTTPException(status.HTTP_409_CONFLICT, "only failed jobs can be retried")
    new_job_id = enqueue(
        db,
        queue=str(job["queue"]),
        kind=str(job["kind"]),
        payload=payload_json_to_dict(job.get("payload_json")),
        priority=int(job["priority"]),
        max_attempts=int(job["max_attempts"]),
        caused_by_job_id=job_id,
    )
    return JobAccepted(job_id=new_job_id)


def payload_json_to_dict(raw: object) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw:
        return {}
    decoded = json.loads(raw)
    return decoded if isinstance(decoded, dict) else {}
