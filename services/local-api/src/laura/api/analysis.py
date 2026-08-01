"""Analysis endpoints: start a run, read status, shots, and transcript (docs/04-api.md)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse

from .. import PIPELINE_VERSION, audit
from ..auth import Principal, require_permission
from ..db import repos
from ..db.database import Database
from ..jobs.queues import queue_for
from ..jobs.runner import enqueue
from .models import (
    AnalysisAccepted,
    AnalysisRunOut,
    AnalysisStart,
    SegmentOut,
    SegmentUpdate,
    ShotOut,
    TranscriptRealignAccepted,
    TranscriptRealignRequest,
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
        "stages": {
            "scene": body.scene, "asr": body.asr,
            "diarize": body.diarize, "align": body.align,
        },
        "model": body.model,
        "language": body.language,
        "detector": body.detector,
    }
    run = repos.create_analysis_run(
        db, asset_id=asset_id, pipeline_version=PIPELINE_VERSION, config=config
    )
    job_id = enqueue(
        db, queue=queue_for("analysis.run"), kind="analysis.run",
        payload={"asset_id": asset_id, "analysis_run_id": run["id"], "config": config},
        idempotency_key=f"analysis:{run['id']}", pipeline_version=PIPELINE_VERSION,
        # Cap retries below the default 3: analysis is heavy and rarely transient, so a
        # poison input (one that crashes/wedges a worker) is bounded to 2 attempts instead
        # of repeatedly taking the worker down. One retry still covers a genuine worker death.
        max_attempts=2,
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


@router.get("/shots/{shot_id}/thumbnail")
def get_shot_thumbnail(shot_id: str, request: Request) -> FileResponse:
    shot = repos.get_shot(_db(request), shot_id)
    if shot is None or not shot.get("thumbnail_path"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no thumbnail for shot")
    path = Path(shot["thumbnail_path"])
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "thumbnail missing on disk")
    return FileResponse(path)


@router.get("/assets/{asset_id}/transcript", response_model=list[SegmentOut])
def get_transcript(asset_id: str, request: Request) -> list[SegmentOut]:
    db = _db(request)
    run = repos.get_latest_transcript_run(db, asset_id)
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
                alignment_status=seg.get("alignment_status", "aligned"),
                alignment_job_id=seg.get("alignment_job_id"),
                alignment_language=seg.get("alignment_language"),
                alignment_error=seg.get("alignment_error"),
                alignment_updated_at=seg.get("alignment_updated_at"),
                words=[WordOut(**w) for w in seg["words"]],
            )
        )
    return out


def _segment_out(seg: dict[str, Any], words: list[dict[str, Any]]) -> SegmentOut:
    return SegmentOut(
        id=seg["id"], speaker_id=seg["speaker_id"], speaker_label=seg.get("speaker_label"),
        start_sample=seg["start_sample"], end_sample=seg["end_sample"],
        start_frame=seg["start_frame"], end_frame=seg["end_frame"], text=seg["text"],
        confidence=seg["confidence"],
        alignment_status=seg.get("alignment_status", "aligned"),
        alignment_job_id=seg.get("alignment_job_id"),
        alignment_language=seg.get("alignment_language"),
        alignment_error=seg.get("alignment_error"),
        alignment_updated_at=seg.get("alignment_updated_at"),
        words=[WordOut(**w) for w in words],
    )


def _analysis_language(db: Database, asset_id: str) -> str:
    # The language belongs to the run that produced the transcript we are about to realign —
    # not to whatever ran last (a scene-only re-analysis carries no ASR config that matters).
    run = repos.get_latest_transcript_run(db, asset_id)
    if run is None:
        return "en"
    try:
        config = json.loads(run.get("config_json") or "{}")
    except json.JSONDecodeError:
        return "en"
    language = config.get("language")
    return language if isinstance(language, str) and language else "en"


def _realign_segment_ids(db: Database, asset_id: str, requested: list[str] | None) -> list[str]:
    if requested:
        segment_ids: list[str] = []
        for segment_id in requested:
            seg = repos.get_segment(db, segment_id)
            if seg is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "segment not found")
            if seg["asset_id"] != asset_id:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "segment does not belong to asset",
                )
            segment_ids.append(segment_id)
        return segment_ids

    run = repos.get_latest_transcript_run(db, asset_id)
    if run is None:
        return []
    return [str(seg["id"]) for seg in repos.get_transcript(db, asset_id, run["id"])]


@router.patch("/transcript/segments/{segment_id}", response_model=SegmentOut)
def update_transcript_segment(
    segment_id: str,
    body: SegmentUpdate,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("asset:write"))],
) -> SegmentOut:
    db = _db(request)
    if repos.get_segment(db, segment_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "segment not found")
    repos.update_segment(db, segment_id, text=body.text, speaker_id=body.speaker_id)
    audit.record(db, principal, "transcript.update", entity_type="segment", entity_id=segment_id)
    seg = repos.get_segment(db, segment_id)
    assert seg is not None
    return _segment_out(seg, repos.get_segment_words(db, segment_id))


@router.post(
    "/assets/{asset_id}/transcript:realign",
    response_model=TranscriptRealignAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def realign_transcript(
    asset_id: str,
    body: TranscriptRealignRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("asset:write"))],
) -> TranscriptRealignAccepted:
    db = _db(request)
    if repos.get_asset(db, asset_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    segment_ids = _realign_segment_ids(db, asset_id, body.segment_ids)
    language = body.language or _analysis_language(db, asset_id)
    job_id = enqueue(
        db,
        queue=queue_for("transcript.realign"),
        kind="transcript.realign",
        payload={
            "asset_id": asset_id,
            "segment_ids": segment_ids or None,
            "language": language,
        },
        max_attempts=1,
        pipeline_version=PIPELINE_VERSION,
    )
    repos.mark_segments_alignment(
        db,
        segment_ids,
        status="aligning",
        job_id=job_id,
        language=language,
        error=None,
    )
    audit.record(db, principal, "transcript.realign", entity_type="asset", entity_id=asset_id)
    return TranscriptRealignAccepted(job_id=job_id)
