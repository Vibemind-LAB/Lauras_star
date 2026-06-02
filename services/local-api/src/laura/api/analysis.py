"""Analysis endpoints: start a run, read status, shots, and transcript (docs/04-api.md)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from .. import PIPELINE_VERSION
from ..db import repos
from ..db.database import Database
from ..jobs.runner import enqueue
from .models import (
    AnalysisAccepted,
    AnalysisRunOut,
    AnalysisStart,
    SegmentOut,
    ShotOut,
    WordOut,
)
from .security import require_token

router = APIRouter(tags=["analysis"], dependencies=[Depends(require_token)])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _run_out(run: dict[str, Any]) -> AnalysisRunOut:
    diagnostics = json.loads(run.get("diagnostics_json") or "{}")
    return AnalysisRunOut(
        id=run["id"],
        asset_id=run["asset_id"],
        pipeline_version=run["pipeline_version"],
        status=run["status"],
        started_at=run["started_at"],
        finished_at=run["finished_at"],
        diagnostics=diagnostics,
    )


@router.post(
    "/assets/{asset_id}/analysis",
    response_model=AnalysisAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_analysis(asset_id: str, body: AnalysisStart, request: Request) -> AnalysisAccepted:
    db = _db(request)
    if repos.get_asset(db, asset_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    config: dict[str, Any] = {
        "stages": {"scene": body.scene, "asr": body.asr, "diarize": body.diarize},
        "model": body.model,
        "language": body.language,
    }
    run = repos.create_analysis_run(
        db, asset_id=asset_id, pipeline_version=PIPELINE_VERSION, config=config
    )
    job_id = enqueue(
        db, queue="analysis.scene", kind="analysis.run",
        payload={"asset_id": asset_id, "analysis_run_id": run["id"], "config": config},
        idempotency_key=f"analysis:{run['id']}", pipeline_version=PIPELINE_VERSION,
    )
    return AnalysisAccepted(analysis_run_id=run["id"], job_id=job_id)


@router.get("/assets/{asset_id}/analysis/latest", response_model=AnalysisRunOut)
def latest_analysis(asset_id: str, request: Request) -> AnalysisRunOut:
    run = repos.get_latest_analysis_run(_db(request), asset_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no analysis run for asset")
    return _run_out(run)


@router.get("/assets/{asset_id}/shots", response_model=list[ShotOut])
def get_shots(asset_id: str, request: Request) -> list[ShotOut]:
    db = _db(request)
    run = repos.get_latest_analysis_run(db, asset_id)
    if run is None:
        return []
    return [ShotOut(**s) for s in repos.list_shots(db, asset_id, run["id"])]


@router.get("/assets/{asset_id}/transcript", response_model=list[SegmentOut])
def get_transcript(asset_id: str, request: Request) -> list[SegmentOut]:
    db = _db(request)
    run = repos.get_latest_analysis_run(db, asset_id)
    if run is None:
        return []
    out: list[SegmentOut] = []
    for seg in repos.get_transcript(db, asset_id, run["id"]):
        out.append(
            SegmentOut(
                id=seg["id"],
                speaker_id=seg["speaker_id"],
                speaker_label=seg.get("speaker_label"),
                start_sample=seg["start_sample"],
                end_sample=seg["end_sample"],
                start_frame=seg["start_frame"],
                end_frame=seg["end_frame"],
                text=seg["text"],
                confidence=seg["confidence"],
                words=[WordOut(**w) for w in seg["words"]],
            )
        )
    return out
