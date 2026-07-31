"""Auto-shorts candidates API (S5b): enqueue a ``shorts.extract`` job + list results.

Separate router from :mod:`laura.api.shorts` (the read-only next-action read-model) —
this is the *write* path that drives the cutter over the pipeline.

* ``POST /assets/{asset_id}/shorts-candidates:extract`` enqueues the background job
  (409 when the asset has no succeeded analysis run — analyze it first).
* ``GET  /assets/{asset_id}/shorts-candidates`` lists the persisted candidates.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from .. import PIPELINE_VERSION
from ..db import repos
from ..db.database import Database
from ..jobs.queues import queue_for
from ..jobs.runner import enqueue
from .models import (
    ExtractShortsAccepted,
    ExtractShortsRequest,
    RenderShortAccepted,
    RenderShortRequest,
    ShortsCandidateOut,
)
from .security import require_token

router = APIRouter(tags=["shorts-candidates"], dependencies=[Depends(require_token)])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


@router.post(
    "/assets/{asset_id}/shorts-candidates:extract",
    response_model=ExtractShortsAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def extract_shorts(
    asset_id: str, body: ExtractShortsRequest, request: Request
) -> ExtractShortsAccepted:
    """Enqueue a ``shorts.extract`` job for *asset_id*.

    409 when the asset has no *succeeded* analysis run (the cutter needs a transcript /
    shots first). Idempotent per ``(asset, latest run)``: a second call while the same
    run is current returns the existing job.
    """
    db = _db(request)
    if repos.get_asset(db, asset_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")

    run = repos.get_latest_succeeded_analysis_run(db, asset_id)
    if run is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "analyze the asset first")

    config: dict[str, Any] = body.model_dump(exclude_none=True)
    job_id = enqueue(
        db,
        queue=queue_for("shorts.extract"),
        kind="shorts.extract",
        payload={"asset_id": asset_id, **config},
        idempotency_key=f"shorts:{asset_id}:{run['id']}",
        pipeline_version=PIPELINE_VERSION,
    )
    # Chain the frame embeddings (visual search / hook scoring) — idempotent per run,
    # graceful without a visual backend; nothing else enqueues this job.
    enqueue(
        db,
        queue=queue_for("shorts.embed_frames"),
        kind="shorts.embed_frames",
        payload={"asset_id": asset_id},
        idempotency_key=f"embed:{asset_id}:{run['id']}",
        pipeline_version=PIPELINE_VERSION,
    )
    return ExtractShortsAccepted(job_id=job_id, analysis_run_id=run["id"])


@router.get(
    "/assets/{asset_id}/shorts-candidates",
    response_model=list[ShortsCandidateOut],
)
def list_shorts_candidates(asset_id: str, request: Request) -> list[ShortsCandidateOut]:
    """List an asset's persisted short candidates, best score first (by ``order_index``)."""
    db = _db(request)
    rows = repos.list_shorts_candidates_by_asset(db, asset_id)
    return [ShortsCandidateOut(**row) for row in rows]


@router.post(
    "/shorts-candidates/{candidate_id}/render",
    response_model=RenderShortAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def render_short(
    candidate_id: str, body: RenderShortRequest, request: Request
) -> RenderShortAccepted:
    """Render one short candidate to a vertical 9:16 MP4 (captions + optional hook + loudness).

    Creates the ``exports`` row up front (status ``rendering``) and enqueues a ``shorts.render``
    job carrying its id — the worker resolves the candidate, trims the single source clip, and
    burns captions. 404 when the candidate (or its asset) no longer exists.
    """
    db = _db(request)
    candidate = repos.get_short_candidate(db, candidate_id)
    if candidate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "candidate not found")

    asset = repos.get_asset(db, candidate["asset_id"])
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")

    options: dict[str, Any] = {
        "kind": "short",
        "candidate_id": candidate_id,
        "captions": body.captions,
        "hook_text": body.hook_text,
        "loudnorm": body.loudnorm,
    }
    exp = repos.create_export(
        db,
        project_id=asset["project_id"],
        timeline_id=candidate.get("source_timeline_id"),
        format="mp4",
        options=options,
    )
    job_id = enqueue(
        db,
        queue=queue_for("shorts.render"),
        kind="shorts.render",
        payload={"export_id": exp["id"]},
        idempotency_key=f"shortrender:{exp['id']}",
    )
    return RenderShortAccepted(export_id=exp["id"], job_id=job_id)
