"""Consent management + reenact job endpoints.

POST /projects/{project_id}/consent          — record subject consent
GET  /projects/{project_id}/consent          — list consent records
POST /projects/{project_id}/consent/{id}/revoke — revoke a consent record
POST /timelines/{timeline_id}/reenact        — enqueue ai.reenact job (consent-gated)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..db import repos
from ..db.database import Database
from ..jobs.queues import queue_for
from ..jobs.runner import enqueue
from .models import ConsentOut, ConsentRequest, ReenactRequest
from .security import require_token

router = APIRouter(tags=["reenact"], dependencies=[Depends(require_token)])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _consent_out(rec: dict[str, object]) -> ConsentOut:
    return ConsentOut(**{k: rec.get(k) for k in ConsentOut.model_fields})  # type: ignore[arg-type]


def _validate_runtime(db: Database, runtime_id: str | None, effect: str) -> None:
    if runtime_id is None:
        return
    runtime = repos.get_ai_runtime(db, runtime_id)
    if runtime is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "runtime not found")
    if runtime["effect"] != effect:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"runtime effect must be {effect}",
        )
    if not runtime["enabled"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "runtime is disabled")


@router.post(
    "/projects/{project_id}/consent",
    status_code=status.HTTP_201_CREATED,
    response_model=ConsentOut,
)
def create_consent(
    project_id: str, body: ConsentRequest, request: Request
) -> ConsentOut:
    """Record informed consent for a named subject within a project."""
    db = _db(request)
    if repos.get_project(db, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    rec = repos.create_consent_record(
        db,
        project_id=project_id,
        subject_label=body.subject_label,
        source_asset_id=body.source_asset_id,
        confirmed_by=body.confirmed_by,
        note=body.note,
    )
    return _consent_out(rec)


@router.get(
    "/projects/{project_id}/consent",
    status_code=status.HTTP_200_OK,
    response_model=list[ConsentOut],
)
def list_consent(project_id: str, request: Request) -> list[ConsentOut]:
    """List all consent records for a project, newest first."""
    db = _db(request)
    records = repos.list_consent_records(db, project_id)
    return [_consent_out(r) for r in records]


@router.post(
    "/projects/{project_id}/consent/{consent_id}/revoke",
    status_code=status.HTTP_200_OK,
    response_model=ConsentOut,
)
def revoke_consent(
    project_id: str, consent_id: str, request: Request
) -> ConsentOut:
    """Revoke a consent record; rejected reenact jobs after this point."""
    db = _db(request)
    ok = repos.revoke_consent_record(db, consent_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "consent record not found")
    rec = repos.get_consent_record(db, consent_id)
    assert rec is not None
    return _consent_out(rec)


@router.post(
    "/timelines/{timeline_id}/reenact",
    status_code=status.HTTP_202_ACCEPTED,
)
def reenact(
    timeline_id: str, body: ReenactRequest, request: Request
) -> dict[str, str]:
    """Enqueue an ai.reenact job — replaces a timeline segment with a portrait-driven
    reenactment. Consent record must exist and must not be revoked."""
    db = _db(request)

    tl = repos.get_timeline(db, timeline_id)
    if tl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")

    if body.seq_out_frame_exclusive <= body.seq_in_frame:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "seq_out_frame_exclusive must be greater than seq_in_frame",
        )

    _validate_runtime(db, body.runtime_id, "reenact")

    consent = repos.get_consent_record(db, body.consent_id)
    if consent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "consent record not found")
    if consent.get("revoked_at") is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "consent has been revoked; obtain fresh consent before re-enacting",
        )

    portrait = repos.get_asset(db, body.portrait_asset_id)
    if portrait is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "portrait asset not found")

    payload = {
        "timeline_id": timeline_id,
        "seq_in_frame": body.seq_in_frame,
        "seq_out_frame_exclusive": body.seq_out_frame_exclusive,
        "portrait_asset_id": body.portrait_asset_id,
        "consent_id": body.consent_id,
        "backend": body.backend,
    }
    if body.runtime_id is not None:
        payload["runtime_id"] = body.runtime_id

    job_id = enqueue(
        db,
        queue=queue_for("ai.reenact", default="ai"),
        kind="ai.reenact",
        payload=payload,
        idempotency_key=(
            f"reenact:{timeline_id}:{body.seq_in_frame}:{body.seq_out_frame_exclusive}"
            f":{body.portrait_asset_id}"
        ),
    )
    return {"job_id": job_id}
