"""Asset import and read endpoints (docs/04-api.md).

Import is asynchronous: it registers the asset and enqueues the ingest pipeline,
returning a job id the client polls. Originals are referenced in place (local-first,
no copy of large media); derived artifacts live under the project workspace.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, PlainTextResponse

from .. import audit
from ..auth import Principal, require_permission
from ..db import repos
from ..db.database import Database
from ..interchange.captions import segments_to_srt, segments_to_vtt
from ..jobs.runner import enqueue
from .models import AssetFileOut, AssetImport, AssetOut, ImportAccepted
from .pagination import PageParams
from .security import require_token

router = APIRouter(tags=["assets"], dependencies=[Depends(require_token)])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


@router.post(
    "/projects/{project_id}/assets/import",
    response_model=ImportAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def import_asset(project_id: str, body: AssetImport, request: Request) -> ImportAccepted:
    db = _db(request)
    if repos.get_project(db, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")

    src = Path(body.source_path)
    if not src.exists() or not src.is_file():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"source not found: {src}")

    asset = repos.create_asset(
        db,
        project_id=project_id,
        type="video",  # corrected by the probe stage
        display_name=body.display_name or src.name,
        source_path=str(src),
    )
    job_id = enqueue(
        db, queue="ingest.io", kind="ingest.probe",
        payload={"asset_id": asset["id"]}, idempotency_key=f"probe:{asset['id']}",
    )
    return ImportAccepted(asset_id=asset["id"], job_id=job_id)


@router.get("/projects/{project_id}/assets", response_model=list[AssetOut])
def list_project_assets(
    project_id: str, request: Request, response: Response, page: PageParams
) -> list[AssetOut]:
    db = _db(request)
    if repos.get_project(db, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    response.headers["X-Total-Count"] = str(repos.count_assets(db, project_id))
    out: list[AssetOut] = []
    for asset in repos.list_assets(db, project_id, limit=page.limit, offset=page.offset):
        files = [AssetFileOut(**f) for f in repos.list_asset_files(db, asset["id"])]
        out.append(AssetOut(**asset, files=files))
    return out


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(
    asset_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("asset:write"))],
) -> None:
    db = _db(request)
    if repos.get_asset(db, asset_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    repos.delete_asset(db, asset_id)
    audit.record(db, principal, "asset.delete", entity_type="asset", entity_id=asset_id)


@router.get("/assets/{asset_id}", response_model=AssetOut)
def get_asset(asset_id: str, request: Request) -> AssetOut:
    db = _db(request)
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    files = [AssetFileOut(**f) for f in repos.list_asset_files(db, asset_id)]
    return AssetOut(**asset, files=files)


@router.get("/assets/{asset_id}/files/{kind}")
def get_asset_file(asset_id: str, kind: str, request: Request) -> FileResponse:
    """Serve a derived asset artifact (waveform/poster/proxy/audio) by kind.

    The renderer fetches these with the session token rather than reading the
    filesystem directly (docs/09-security.md)."""
    db = _db(request)
    match = next((f for f in repos.list_asset_files(db, asset_id) if f["kind"] == kind), None)
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no {kind} file for asset")
    path = Path(match["path"])
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file missing on disk")
    return FileResponse(path)


def _captions(request: Request, asset_id: str, fmt: str) -> str:
    db = _db(request)
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    project = repos.get_project(db, asset["project_id"])
    assert project is not None
    rate_num = asset["rate_num"] or project["sequence_rate_num"]
    rate_den = asset["rate_den"] or project["sequence_rate_den"]
    run = repos.get_latest_analysis_run(db, asset_id)
    segments = repos.get_transcript(db, asset_id, run["id"]) if run is not None else []
    return (
        segments_to_srt(segments, rate_num, rate_den)
        if fmt == "srt"
        else segments_to_vtt(segments, rate_num, rate_den)
    )


@router.get("/assets/{asset_id}/captions.srt")
def asset_captions_srt(asset_id: str, request: Request) -> PlainTextResponse:
    return PlainTextResponse(
        _captions(request, asset_id, "srt"), media_type="application/x-subrip"
    )


@router.get("/assets/{asset_id}/captions.vtt")
def asset_captions_vtt(asset_id: str, request: Request) -> PlainTextResponse:
    return PlainTextResponse(_captions(request, asset_id, "vtt"), media_type="text/vtt")
