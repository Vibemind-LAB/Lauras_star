"""Asset import and read endpoints (docs/04-api.md).

Import is asynchronous: it registers the asset and enqueues the ingest pipeline,
returning a job id the client polls. Originals are referenced in place (local-first,
no copy of large media); derived artifacts live under the project workspace.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, PlainTextResponse

from .. import audit
from ..auth import Principal, require_permission
from ..db import repos
from ..db.database import Database
from ..ingest.proxy import build_thumbnail
from ..ingest.ytdlp import expand_playlist, ytdlp_available
from ..interchange.captions import segments_to_srt, segments_to_vtt
from ..jobs.runner import enqueue
from .models import AssetFileOut, AssetImport, AssetOut, ImportAccepted, ImportStatusOut
from .pagination import PageParams
from .security import require_token

router = APIRouter(tags=["assets"], dependencies=[Depends(require_token)])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


# Cap playlist/channel fan-out so one paste of a huge channel can't enqueue thousands
# of fetch jobs. Matches expand_playlist's default ``limit``.
_PLAYLIST_LIMIT = 50


def _expand_playlist_urls(source_url: str, cookies_from_browser: str | None) -> list[str] | None:
    """Return the entry URLs if ``source_url`` is a yt-dlp playlist/channel, else None.

    Metadata-only (``extract_flat``); never downloads. Returns None when yt-dlp is
    unavailable, the URL is a single video, or expansion fails — callers then treat the
    URL as a single asset.
    """
    if not ytdlp_available():
        return None
    return expand_playlist(
        source_url, limit=_PLAYLIST_LIMIT, cookies_from_browser=cookies_from_browser
    )


def _enqueue_url_fetch(
    db: Database,
    project_id: str,
    source_url: str,
    *,
    display_name: str | None,
    fmt: str | None,
    cookies_from_browser: str | None,
) -> tuple[str, str]:
    """Create one offline asset for ``source_url`` and enqueue its ingest.fetch job.

    The chosen format/cookies ride on the job payload so the fetch handler can pass them
    to yt-dlp. Returns ``(asset_id, job_id)``.
    """
    name = display_name or Path(source_url.split("?", 1)[0]).name or "download.bin"
    asset = repos.create_asset(
        db, project_id=project_id, type="video",
        display_name=name, source_path=f"url:{source_url}", online=False,
    )
    payload: dict[str, Any] = {"asset_id": asset["id"], "source_url": source_url}
    if fmt:
        payload["format"] = fmt
    if cookies_from_browser:
        payload["cookies_from_browser"] = cookies_from_browser
    job_id = enqueue(
        db, queue="ingest.io", kind="ingest.fetch",
        payload=payload, idempotency_key=f"fetch:{asset['id']}", max_attempts=5,
    )
    return asset["id"], job_id


@router.post(
    "/projects/{project_id}/assets/import",
    response_model=ImportAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def import_asset(project_id: str, body: AssetImport, request: Request) -> ImportAccepted:
    db = _db(request)
    if repos.get_project(db, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")

    if body.source_url:
        # A playlist/channel URL fans out into one asset + fetch job per entry. The
        # response still carries a single (first) asset_id/job_id for backward compat;
        # extra_asset_ids lists the rest so the UI can track them all.
        entry_urls = _expand_playlist_urls(body.source_url, body.cookies_from_browser)
        urls = entry_urls if entry_urls else [body.source_url]
        accepted: list[tuple[str, str]] = []
        for entry_url in urls:
            accepted.append(
                _enqueue_url_fetch(
                    db, project_id, entry_url,
                    display_name=body.display_name if len(urls) == 1 else None,
                    fmt=body.format,
                    cookies_from_browser=body.cookies_from_browser,
                )
            )
        first_asset, first_job = accepted[0]
        return ImportAccepted(
            asset_id=first_asset, job_id=first_job,
            extra_asset_ids=[aid for aid, _ in accepted[1:]],
        )

    assert body.source_path is not None  # guaranteed by the AssetImport validator

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


@router.delete(
    "/assets/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_asset(
    asset_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("asset:write"))],
) -> Response:
    db = _db(request)
    if repos.get_asset(db, asset_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    repos.delete_asset(db, asset_id)
    audit.record(db, principal, "asset.delete", entity_type="asset", entity_id=asset_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/assets/{asset_id}", response_model=AssetOut)
def get_asset(asset_id: str, request: Request) -> AssetOut:
    db = _db(request)
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    files = [AssetFileOut(**f) for f in repos.list_asset_files(db, asset_id)]
    return AssetOut(**asset, files=files)


@router.get("/assets/{asset_id}/provenance")
def get_asset_provenance(asset_id: str, request: Request) -> dict[str, Any]:
    db = _db(request)
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")

    manifest_path = Path(f"{asset['source_path']}.laura-provenance.json")
    if not manifest_path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "provenance manifest not found")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "provenance manifest unreadable") from exc
    if not isinstance(manifest, dict):
        raise HTTPException(status.HTTP_409_CONFLICT, "provenance manifest must be an object")
    if manifest.get("asset_id") != asset_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "provenance manifest asset mismatch")
    return manifest


def _derive_import_status(db: Database, asset: dict[str, Any]) -> ImportStatusOut:
    job = repos.get_fetch_job(db, asset["id"])
    # A cancel request is terminal for the import regardless of the job's own row
    # status (the handler returns normally after aborting, so the job ends
    # "succeeded"); surface it as the cancelled phase.
    if job is not None and job.get("cancel_requested"):
        return ImportStatusOut(phase="cancelled")
    if job is not None and job["status"] == "failed":
        files = {f["kind"]: f for f in repos.list_asset_files(db, asset["id"])}
        detail = None
        integ = files.get("integrity")
        if integ is not None:
            try:
                detail = json.loads(Path(integ["path"]).read_text(encoding="utf-8")).get("detail")
            except (OSError, ValueError):
                detail = None
        if detail is None and job.get("error_json"):
            try:
                detail = json.loads(job["error_json"]).get("error")
            except ValueError:
                detail = job["error_json"]
        return ImportStatusOut(phase="error", error=detail)

    if job is not None and job["status"] in ("queued", "leased", "running"):
        prog = json.loads(job["progress_json"]) if job.get("progress_json") else None
        if prog:
            downloaded, total = prog.get("downloaded"), prog.get("total")
            speed = prog.get("speed_bps")
            eta = ((total - downloaded) / speed) if (total and speed and speed > 0) else None
            return ImportStatusOut(
                phase="downloading", downloaded_bytes=downloaded, total_bytes=total,
                speed_bps=speed, eta_seconds=eta,
            )
        return ImportStatusOut(phase="queued")

    if not asset["online"]:
        return ImportStatusOut(phase="queued")
    kinds = {f["kind"] for f in repos.list_asset_files(db, asset["id"])}
    if "waveform" in kinds or "proxy" in kinds:
        return ImportStatusOut(phase="ready")
    return ImportStatusOut(phase="analyzing")


@router.post("/assets/{asset_id}/import-retry", status_code=status.HTTP_202_ACCEPTED)
def import_retry(asset_id: str, request: Request) -> dict[str, str]:
    db = _db(request)
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    job = repos.get_fetch_job(db, asset_id)
    if job is None or job["status"] != "failed":
        raise HTTPException(status.HTTP_409_CONFLICT, "no failed import to retry")
    old_payload = json.loads(job["payload_json"])
    source_url = old_payload.get("source_url")
    if not source_url:
        raise HTTPException(status.HTTP_409_CONFLICT, "fetch job has no source_url")
    payload: dict[str, Any] = {"asset_id": asset_id, "source_url": source_url}
    # Preserve the originally-chosen format/cookies so a retry behaves identically.
    if old_payload.get("format"):
        payload["format"] = old_payload["format"]
    if old_payload.get("cookies_from_browser"):
        payload["cookies_from_browser"] = old_payload["cookies_from_browser"]
    job_id = enqueue(
        db, queue="ingest.io", kind="ingest.fetch",
        payload=payload, idempotency_key=f"fetch:{asset_id}", max_attempts=5,
    )
    return {"asset_id": asset_id, "job_id": job_id}


@router.post("/assets/{asset_id}/import-cancel", status_code=status.HTTP_202_ACCEPTED)
def import_cancel(asset_id: str, request: Request) -> dict[str, str]:
    """Request cancellation of an in-flight import/download.

    Sets a cooperative cancel flag the fetch handler polls; the running download
    aborts at its next progress tick, removes any partial file, and the import
    settles into the ``cancelled`` phase. Idempotent and a harmless no-op when the
    import has already finished (no fetch job to flag)."""
    db = _db(request)
    if repos.get_asset(db, asset_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    repos.request_import_cancel(db, asset_id)
    return {"asset_id": asset_id, "status": "cancelling"}


@router.get("/assets/{asset_id}/import-status", response_model=ImportStatusOut)
def import_status(asset_id: str, request: Request) -> ImportStatusOut:
    db = _db(request)
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    return _derive_import_status(db, asset)


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


@router.get("/assets/{asset_id}/frame/{frame}")
def get_asset_frame(asset_id: str, frame: int, request: Request) -> FileResponse:
    """A JPEG of the source frame ``frame`` (the proxy when available), rendered on
    demand and cached — used for timeline-clip thumbnails."""
    db = _db(request)
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    project = repos.get_project(db, asset["project_id"])
    assert project is not None
    files = {f["kind"]: f for f in repos.list_asset_files(db, asset_id)}
    video = files["proxy"]["path"] if "proxy" in files else asset["source_path"]
    rate_num = asset["rate_num"] or 25
    rate_den = asset["rate_den"] or 1
    dest = Path(project["workspace_root"]) / "analysis" / asset_id / "frames" / f"f-{frame:06d}.jpg"
    if not dest.exists():
        try:
            build_thumbnail(video, dest, at_seconds=max(0, frame) * rate_den / rate_num)
        except Exception as exc:  # noqa: BLE001 - bad frame / missing media
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"could not render frame: {exc}"
            ) from exc
    return FileResponse(dest)


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
