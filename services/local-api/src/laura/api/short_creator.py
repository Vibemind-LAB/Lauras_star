"""NL-agent short-creator endpoint (Iteration 8).

``POST /assets/{asset_id}/auto-short`` enqueues a ``short_creator.run`` job that builds a short
about *topic* from the asset via the multi-agent escalation ladder. Requires the optional
``autoshort`` extra; without it the endpoint returns 503. Poll ``/jobs/{id}`` for completion.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..auth import Principal, require_permission
from ..db import repos
from ..db.database import Database
from ..jobs.queues import queue_for
from ..jobs.runner import enqueue

# Pure-pydantic leaf: safe at runtime even without the optional 'autoshort' extra, unlike
# short_creator.board below.
from ..short_creator.board_models import Format

# discovery/scout import nothing from autogen at module load either (Tasks 1-2 of the
# auto-short arc) — safe here too. run_scout is imported at module level (rather than inside
# the endpoint, like the other autogen-touching calls below) specifically so tests can
# monkeypatch laura.api.short_creator.run_scout.
from ..short_creator.discovery import search_material
from ..short_creator.overview_build import build_overview
from ..short_creator.overview_scout import OverviewDecision, run_overview_scout
from ..short_creator.overview_windows import build_candidates, duration_seconds
from ..short_creator.scout import ScoutDecision, run_scout
from ..util import new_id

if TYPE_CHECKING:  # annotation only — never imported at runtime
    from ..short_creator.board import Board

logger = logging.getLogger(__name__)

router = APIRouter(tags=["short-creator"])


class AutoShortRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=2000)
    target_seconds: int = Field(default=60, gt=0, le=600)


class ProductionCreateRequest(BaseModel):
    task: str = Field(min_length=1, max_length=2000)
    target_seconds: int = Field(default=60, gt=0, le=600)
    # Delivery format picks the canvas: "insta" 9:16 reel, "x" native 16:9, "linkedin" 1:1.
    format: Format = "insta"
    # The script's language, named as it should be written ("German", "English").
    language: str = Field(default="German", min_length=2, max_length=40)


class ProjectAutoShortRequest(BaseModel):
    """Body for ``POST /projects/{project_id}/auto-short`` — topic in, scouted session out.

    ``target_seconds``/``format``/``language`` mirror :class:`ProductionCreateRequest`'s exact
    defaults; ``topic`` replaces ``task`` — the scout composes the actual production task text
    from it (see :func:`create_project_auto_short`).
    """

    topic: str = Field(min_length=1, max_length=2000)
    target_seconds: int = Field(default=60, gt=0, le=600)
    format: Format = "insta"
    language: str = Field(default="German", min_length=2, max_length=40)


class ProjectAutoOverviewRequest(BaseModel):
    """Body for ``POST /projects/{project_id}/auto-overview`` — topic in, montage out.

    ``target_seconds`` defaults to a 3-minute overview (Phase 1's short defaults to 60s; an
    overview covering several videos needs room). ``language`` is accepted and echoed for
    symmetry with the short endpoints but changes nothing in v1: the cut runs on the clips'
    ORIGINAL audio, so there is no script to write in any language.
    """

    topic: str = Field(min_length=1, max_length=2000)
    target_seconds: int = Field(default=180, gt=0, le=1800)
    language: str = Field(default="German", min_length=2, max_length=40)


class ProductionMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ProductionRevertRequest(BaseModel):
    artifact: str
    version: int


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _autoshort_available() -> bool:
    """True when the optional ``autoshort`` extra (AutoGen) is importable."""
    try:
        import autogen_agentchat  # noqa: F401
    except ImportError:
        return False
    return True


def _require_autoshort() -> None:
    """Raise 503 unless the optional ``autoshort`` extra (AutoGen) is installed.

    Shared by every endpoint that enqueues an agent run (extracted once a 4th call site —
    the follow-up message endpoint — joined the original three).
    """
    if not _autoshort_available():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The 'autoshort' extra is not installed. Run: uv sync --extra autoshort",
        )


def _require_usable_agent_config() -> None:
    """Raise 503 unless the configured agent provider could actually be reached.

    Live incident: a run was started against ``openai-compat`` with no ``LAURA_AGENT_API_KEY``.
    It was enqueued, created a board, spent both escalation stages and came back "Connection
    error." — then looked alive for 55 minutes. The extra was checked; the credential was not.
    """
    from laura.short_creator.providers import config_problems, resolve_from_env

    problems = config_problems(resolve_from_env())
    if problems:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "the agent provider is not usable: " + "; ".join(problems),
        )


def _get_asset_or_404(db: Database, asset_id: str) -> dict[str, Any]:
    """Return the asset row for *asset_id*, or raise 404 if it does not exist."""
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    return asset


def _open_board_or_404(db: Database, asset_id: str, session_id: str) -> Board:
    """Open *session_id*'s production board, or raise 404 if there isn't one yet.

    Covers both ``board_root_for``'s ``ValueError`` (the asset — or its project — no longer
    exists) and ``Board.open``'s ``FileNotFoundError`` (no board written for this session
    yet): from a caller's point of view both mean "nothing to read/append to here".
    """
    from ..short_creator.board import Board
    from ..short_creator.production_orchestrator import board_root_for

    try:
        root = board_root_for(db, asset_id, session_id)
        return Board.open(root)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "board missing") from exc


def _restored_from_job(job: dict[str, Any]) -> list[str]:
    """The artifact names a resume restored from the provenance chain, read off *job*'s result.

    ``run_production`` always writes a ``restored`` key into its result dict (empty when nothing
    was restored), but this is a pure status read: a job with no result yet (still queued), an
    unparseable ``result_json``, or a result missing/mistyping the key must degrade to ``[]``
    rather than raise or 500 the whole status endpoint.
    """
    result = _result_of(job)
    if result is None:
        return []
    restored = result.get("restored")
    if not isinstance(restored, list):
        return []
    return [item for item in restored if isinstance(item, str)]


def _result_of(job: dict[str, Any]) -> dict[str, Any] | None:
    """*job*'s parsed ``result_json``, or None when there is none / it is unusable.

    A pure status read: a queued job, unparseable JSON, or a non-object result must degrade to
    None rather than raise and 500 the endpoint that reports liveness.
    """
    raw = job.get("result_json")
    if not raw:
        return None
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return result if isinstance(result, dict) else None


def _outcome_from_job(job: dict[str, Any]) -> dict[str, Any]:
    """Did the run actually produce a film — and if not, where did it stop?

    The job status answers a different question than the user is asking. It means "the handler
    returned without raising", which is true of a run that spent its whole turn budget failing
    to save a storyline. Live 2026-08-02 (Drive-Test): two such runs both read ``succeeded``
    with ``export_id: null`` and half a board. ``run_production``'s result has carried the real
    answer all along (``complete`` = ``resume_point == "done"``; see its own docstring on why
    ``ok`` is not it) — nobody read it. ``complete`` is None while nothing is known yet: a
    queued job has not failed to deliver, it simply has not run.
    """
    result = _result_of(job)
    if result is None:
        return {"complete": None, "export_id": None, "stopped_at": None}
    complete = result.get("complete")
    complete = bool(complete) if isinstance(complete, bool) else None
    export_id = result.get("export_id")
    resume_point = result.get("resume_point")
    return {
        "complete": complete,
        "export_id": export_id if isinstance(export_id, str) else None,
        # Only an INCOMPLETE run stopped somewhere; a finished one stopped nowhere.
        "stopped_at": (
            str(resume_point) if complete is False and isinstance(resume_point, str) else None
        ),
    }


def _job_view(job: dict[str, Any] | None) -> dict[str, Any] | None:
    """The liveness projection of *job* shared by ``GET /production/{sid}`` and the revert
    endpoint — ``None`` when there is no job on the session yet."""
    if job is None:
        return None
    return {
        "id": job["id"],
        "status": job["status"],
        "attempt": job["attempt"],
        "updated_at": job["updated_at"],
        "lease_expires_at": job["lease_expires_at"],
        "finished_at": job["finished_at"],
        "restored": _restored_from_job(job),
        **_outcome_from_job(job),
    }


def _expected_scenes_for(db: Database, asset_id: str) -> list[int]:
    """Rough-cut scene numbers for *asset_id* (the reviews the board should cover).

    Mirrors ``production_orchestrator._expected_scene_numbers``'s logic on top of
    :func:`laura.short_creator.context.scene_transcripts` — kept as a local copy rather than
    importing that helper since its leading underscore marks it private to its own module.
    """
    from ..short_creator import context

    result = context.scene_transcripts(db, asset_id)
    if not result.get("ok"):
        return []
    return [int(s["scene_number"]) for s in result.get("scenes", [])]


@router.post("/assets/{asset_id}/auto-short", status_code=status.HTTP_202_ACCEPTED)
def auto_short(
    asset_id: str,
    body: AutoShortRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """Enqueue a ``short_creator.run`` job for *asset_id* (503 if the extra is missing)."""
    db = _db(request)
    _get_asset_or_404(db, asset_id)
    _require_autoshort()
    _require_usable_agent_config()
    payload: dict[str, Any] = {
        "asset_id": asset_id,
        "topic": body.topic,
        "target_seconds": body.target_seconds,
    }
    # LLM runs are expensive + non-idempotent — do not auto-retry.
    job_id = enqueue(
        db,
        queue=queue_for("short_creator.run"),
        kind="short_creator.run",
        payload=payload,
        max_attempts=1,
    )
    return {"job_id": job_id}


@router.post("/assets/{asset_id}/auto-short/stream")
def auto_short_stream(
    asset_id: str,
    body: AutoShortRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> StreamingResponse:
    """Run the short-creator live and stream normalized NDJSON events (one JSON object per line).

    The run is bound to this connection (closing it stops the run) — that is the "watch it live"
    behavior. 404 (asset) is checked before 503 (missing extra), both before streaming starts.
    """
    db = _db(request)
    _get_asset_or_404(db, asset_id)
    _require_autoshort()
    _require_usable_agent_config()
    from ..short_creator.providers import resolve_from_env
    from ..short_creator.stream import run_short_creator_stream

    config = resolve_from_env()
    topic, target_seconds = body.topic, body.target_seconds

    # Every event is ALSO appended to an NDJSON run log (flushed per line), so a run can be
    # debugged after the fact — the chat panel's copy dies with the window (live finding:
    # "paste me the chat ending" was the only way to diagnose a run).
    log_file: IO[str] | None = None
    log_dir = request.app.state.settings.workspace_root / "agent-runs"
    log_path = log_dir / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{asset_id[:8]}.ndjson"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("a", encoding="utf-8")
        logger.info("auto-short run log: %s", log_path)
    except OSError as exc:  # logging must never block the run
        logger.warning("auto-short run log unavailable (%s): %s", log_path, exc)

    def write_line(event: dict[str, Any]) -> bytes:
        line = json.dumps(event, ensure_ascii=False)
        if log_file is not None:
            try:
                log_file.write(line + "\n")
                log_file.flush()
            except OSError:
                pass  # never break the stream over a full disk
        return (line + "\n").encode("utf-8")

    async def events() -> AsyncIterator[bytes]:
        try:
            write_line(
                {
                    "type": "meta",
                    "asset_id": asset_id,
                    "topic": topic,
                    "target_seconds": target_seconds,
                    "provider": config.provider,
                    "agent_model": config.agent_model,
                }
            )
            try:
                async for event in run_short_creator_stream(
                    db, config, asset_id=asset_id, topic=topic, target_seconds=target_seconds
                ):
                    yield write_line(event)
            except Exception as exc:  # never leak a raw 500 mid-stream — emit a final error
                yield write_line({"type": "error", "message": str(exc)})
        except BaseException:  # client disconnect kills the run — make that visible in the log
            write_line({"type": "aborted", "reason": "client disconnected"})
            raise
        finally:
            if log_file is not None:
                log_file.close()

    return StreamingResponse(events(), media_type="application/x-ndjson")


# --- v2 production session endpoint (Slice 4) -------------------------------------------------


def _create_production_session(
    db: Database,
    asset_id: str,
    *,
    task: str,
    target_seconds: float,
    format: str,
    language: str,
) -> tuple[str, str]:
    """Create a v2 production session row for *asset_id* and enqueue its ``production.run`` job.

    Extracted verbatim from ``create_production`` (Task 3 of the auto-short arc) so a second
    caller — the project-scoped auto-short endpoint — creates sessions identically. The session
    row is created before the job is enqueued: a session without a job is harmless (visible,
    just never progresses), while a job without a session row would reference an entity that
    doesn't exist. Returns ``(session_id, job_id)``.
    """
    session_id = new_id()
    created_utc = datetime.now(UTC).isoformat(timespec="seconds")
    repos.create_production_session(
        db, session_id=session_id, asset_id=asset_id, created_utc=created_utc
    )
    # LLM-driven production runs are expensive + non-idempotent — do not auto-retry.
    job_id = enqueue(
        db,
        queue=queue_for("production.run"),
        kind="production.run",
        payload={
            "asset_id": asset_id,
            "session_id": session_id,
            "task": task,
            "target_seconds": target_seconds,
            "format": format,
            "language": language,
        },
        max_attempts=1,
    )
    repos.set_production_session_job(db, session_id, job_id)
    return session_id, job_id


@router.post("/assets/{asset_id}/production", status_code=status.HTTP_202_ACCEPTED)
def create_production(
    asset_id: str,
    body: ProductionCreateRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """Create a v2 production session for *asset_id* and enqueue its ``production.run`` job.

    503 if the 'autoshort' extra is missing. See :func:`_create_production_session` for the
    session-row + enqueue mechanics (shared with the project-scoped auto-short endpoint).
    """
    db = _db(request)
    _get_asset_or_404(db, asset_id)
    _require_autoshort()
    _require_usable_agent_config()
    session_id, job_id = _create_production_session(
        db,
        asset_id,
        task=body.task,
        target_seconds=body.target_seconds,
        format=body.format,
        language=body.language,
    )
    from ..short_creator.providers import config_warnings, resolve_from_env

    return {
        "session_id": session_id,
        "job_id": job_id,
        "warnings": config_warnings(resolve_from_env()),
    }


# --- v2 project-scoped auto-short + auto-overview endpoints (Task 3 / spec 2026-07-31) ----------


def _split_by_source_presence(
    db: Database, ranking: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Split *ranking* into (usable, display names of assets whose source file is gone).

    Live 2026-07-31: two videos were ranked, scouted and built into a sequence with full
    confidence — and only ffmpeg discovered their source files no longer existed (they had been
    imported from a temp directory that was later cleaned). Nothing upstream noticed, because
    ``media_assets.online`` is set at import and by ``set_asset_source`` but NEVER flipped back
    when a file disappears; the transcript rows outlive the media.

    So the check is the filesystem itself, done ONCE per ranked asset, here rather than in
    :func:`discovery.search_material`: discovery answers "what matches the topic" and is
    consumed by other callers, while these routes are where the pipeline commits to BUILDING —
    and they must not build out of clips they cannot render. Dropping (rather than aborting)
    keeps the result useful: the remaining videos still make an overview, and the scout still
    has candidates to choose from.

    The asset's proxy may well still exist, but the renderer resolves the SOURCE, so a present
    proxy does not make the asset usable here.
    """
    usable: list[dict[str, Any]] = []
    missing: list[str] = []
    for entry in ranking:
        asset = repos.get_asset(db, str(entry["asset_id"]))
        source = str((asset or {}).get("source_path") or "")
        if source and Path(source).exists():
            usable.append(entry)
            continue
        name = str(entry.get("display_name") or "") or str(entry["asset_id"])
        missing.append(name)
        logger.info("auto-overview: dropping %s — source file missing (%s)", name, source)
    return usable, missing


@router.post("/projects/{project_id}/auto-short", status_code=status.HTTP_202_ACCEPTED)
def create_project_auto_short(
    project_id: str,
    body: ProjectAutoShortRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """Topic in, scouted v2 production session out.

    Distinct from ``POST /assets/{asset_id}/auto-short`` (the v1 per-asset NL-agent path, left
    untouched): this route picks the asset ITSELF by scanning the whole project's transcripts
    (:func:`search_material`), lets the scout (:func:`run_scout`) choose the best asset and
    scenes for *topic*, then starts a normal v2 production session on that asset — the exact
    same session-creation path as ``POST /assets/{asset_id}/production``.

    404 unknown project; 503 preflight (missing extra / unusable agent config) BEFORE any
    material search or scout call; 422 (no matching material) BEFORE any session is created —
    no corpse sessions on a topic nothing was found for.
    """
    db = _db(request)
    if repos.get_project(db, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    _require_autoshort()
    _require_usable_agent_config()

    material = search_material(db, project_id, body.topic)
    if not material["ranking"]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "reason": "no material found for topic",
                "skipped": material["skipped"],
                "source": material["source"],
            },
        )

    # An asset whose source file is gone can be ranked, scouted and produced -- and only fails
    # at render, once a session and a job already exist. Drop those before the scout sees them
    # (see _split_by_source_presence).
    ranking, missing_sources = _split_by_source_presence(db, material["ranking"])
    if not ranking:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "reason": "no usable material: every matching video's source file is missing",
                "missing_sources": missing_sources,
                "source": material["source"],
            },
        )
    material = {**material, "ranking": ranking}

    from ..short_creator.providers import config_warnings, resolve_from_env

    config = resolve_from_env()
    decision: ScoutDecision = run_scout(
        db, config, project_id=project_id, topic=body.topic, material=material
    )

    # decision["asset_id"] is always one of material["ranking"]'s asset ids: run_scout only ever
    # adopts a reply after validating asset_id against the ranking, and its fallback picks the
    # ranking's own top entry — so this lookup can never miss.
    chosen = next(e for e in material["ranking"] if e["asset_id"] == decision["asset_id"])
    snippets = [hit["snippet"] for hit in chosen["scene_hits"]]
    task = (
        f"{body.topic}\n\n"
        f"Material scout: use asset '{chosen['display_name']}'. Focus on scenes "
        f"{', '.join(map(str, decision['scene_numbers']))} — transcript hits: "
        f"{'; '.join(snippets)}. Scout rationale: {decision['rationale']}"
    )

    session_id, job_id = _create_production_session(
        db,
        decision["asset_id"],
        task=task,
        target_seconds=body.target_seconds,
        format=body.format,
        language=body.language,
    )

    warnings = config_warnings(config)
    if missing_sources:
        warnings = [
            *warnings,
            "left out of the material, source file missing: " + ", ".join(missing_sources),
        ]

    return {
        "session_id": session_id,
        "job_id": job_id,
        "asset_id": decision["asset_id"],
        "scene_numbers": decision["scene_numbers"],
        "rationale": decision["rationale"],
        "fallback": decision["fallback"],
        "ranking": material["ranking"],
        "warnings": warnings,
    }


# --- v2 project-scoped auto-overview endpoint (spec 2026-07-31) ---------------------------------


def _overview_scene_bounds(
    db: Database, project_id: str, ranking: list[dict[str, Any]]
) -> dict[tuple[str, int], tuple[int, int]]:
    """``(asset_id, scene_number) -> (src_start, src_end_exclusive)`` for every ranked asset.

    Built on :func:`discovery._scene_ranges`, the same READ-ONLY lookup the ranking itself
    used — probing must never create a rough cut.
    """
    from ..short_creator import discovery

    bounds: dict[tuple[str, int], tuple[int, int]] = {}
    for entry in ranking:
        asset_id = str(entry["asset_id"])
        ranges = discovery._scene_ranges(db, project_id, asset_id)
        for scene_number, start, end_exclusive in ranges or []:
            bounds[(asset_id, scene_number)] = (start, end_exclusive)
    return bounds


def _overview_fps(
    db: Database, project_id: str, ranking: list[dict[str, Any]]
) -> dict[str, tuple[int, int]]:
    """``asset_id -> (rate_num, rate_den)``, falling back to the project's rate for an asset
    that has none (probe failures leave the columns empty)."""
    project = repos.get_project(db, project_id)
    fallback = (
        int((project or {}).get("sequence_rate_num") or 25),
        int((project or {}).get("sequence_rate_den") or 1),
    )
    out: dict[str, tuple[int, int]] = {}
    for entry in ranking:
        asset_id = str(entry["asset_id"])
        asset = repos.get_asset(db, asset_id)
        if asset is None:
            out[asset_id] = fallback
            continue
        out[asset_id] = (
            int(asset.get("rate_num") or fallback[0]),
            int(asset.get("rate_den") or fallback[1]),
        )
    return out


@router.post("/projects/{project_id}/auto-overview", status_code=status.HTTP_202_ACCEPTED)
def create_project_auto_overview(
    project_id: str,
    body: ProjectAutoOverviewRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """Topic in, a watchable overview cut across several videos out.

    Phase 2 of the auto-short arc: where ``POST /projects/{id}/auto-short`` scouts ONE asset
    and runs the board production on it, this route mixes SEVERAL videos through the sequence
    machinery — a new sequence (never the project's own) plus an enqueued render.

    404 unknown project; 503 preflight (missing extra / unusable agent config); 422 when the
    topic finds no material, no window survives, or the target is shorter than every
    candidate — all BEFORE anything is written.
    """
    db = _db(request)
    if repos.get_project(db, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    _require_autoshort()
    _require_usable_agent_config()

    material = search_material(db, project_id, body.topic)
    if not material["ranking"]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "reason": "no material found for topic",
                "skipped": material["skipped"],
                "source": material["source"],
            },
        )

    # An asset whose source file is gone can be ranked, scouted and assembled — and only fails
    # at render time, after everything is built. Drop those here, before the scout ever sees
    # them, and tell the caller which ones (see _split_by_source_presence).
    ranking, missing_sources = _split_by_source_presence(db, material["ranking"])
    if not ranking:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "reason": "no usable material: every matching video's source file is missing",
                "missing_sources": missing_sources,
                "source": material["source"],
            },
        )

    fps_by_asset = _overview_fps(db, project_id, ranking)
    candidates = build_candidates(
        ranking,
        scene_bounds=_overview_scene_bounds(db, project_id, ranking),
        fps_by_asset=fps_by_asset,
    )
    if not candidates:
        # Material matched, but every hit was too short (or its scene had vanished) to become
        # a watchable clip. Still nothing written — same corpse rule as above.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "reason": "no usable windows for topic",
                "source": material["source"],
            },
        )

    from ..short_creator.providers import config_warnings, resolve_from_env

    config = resolve_from_env()
    decision: OverviewDecision = run_overview_scout(
        config,
        topic=body.topic,
        candidates=candidates,
        target_seconds=body.target_seconds,
        fps_by_asset=fps_by_asset,
    )

    if not decision["clips"]:
        # Every candidate window is at least _MIN_S seconds by construction; trim_to_target
        # keeps candidates only while the running total stays under target_seconds * 1.2, so a
        # small enough target empties the selection no matter how much material matched. That
        # is a legitimate request (target_seconds is in-range) hitting a real constraint, not a
        # programming error — build_overview's empty-clips ValueError must never see it. Still
        # nothing written — same corpse rule as the two 422s above.
        shortest_s = min(duration_seconds([c], fps_by_asset=fps_by_asset) for c in candidates)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "reason": (
                    f"target_seconds ({body.target_seconds}) is shorter than the shortest "
                    f"available clip (~{shortest_s:.1f}s) — raise target_seconds to at least "
                    "that length"
                ),
                "target_seconds": body.target_seconds,
                "shortest_candidate_seconds": round(shortest_s, 1),
            },
        )

    built = build_overview(
        db, project_id=project_id, topic=body.topic, clips=decision["clips"]
    )

    export = repos.create_export(
        db,
        project_id=project_id,
        timeline_id=built["sequence_id"],
        format="mp4",
        options={"burn_captions": False, "source": "auto-overview"},
    )
    job_id = enqueue(
        db,
        queue=queue_for("export.render"),
        kind="export.render",
        payload={"export_id": export["id"]},
        idempotency_key=f"render:{export['id']}",
    )

    warnings = config_warnings(config)
    if len({c.asset_id for c in decision["clips"]}) < 2:
        # Two different causes look the same at this point (the final clip list covers one
        # asset) but are not the same story: (a) the search itself only ever found material
        # in one video, versus (b) several videos matched and the scout's selection covered
        # them — run_overview_scout validates that BEFORE trimming — but target_seconds left
        # room for only one clip once trim_to_target ran. Decide from `candidates` (what
        # existed before the scout ran), not from `decision["clips"]` (the final, possibly
        # trimmed answer), so a short target never gets blamed on the wrong video.
        if len({c.asset_id for c in candidates}) < 2:
            names = sorted({c.display_name for c in decision["clips"]})
            warnings = [
                *warnings,
                f"overview covers a single source: only {', '.join(names)} matched the topic",
            ]
        else:
            warnings = [
                *warnings,
                "overview covers a single source: target_seconds "
                f"({body.target_seconds}) left room for only one clip after trimming",
            ]
    if missing_sources:
        warnings = [
            *warnings,
            "left out of the overview, source file missing: " + ", ".join(missing_sources),
        ]

    return {
        "sequence_id": built["sequence_id"],
        "source_timeline_id": built["source_timeline_id"],
        "clips": [
            {
                "asset_id": c.asset_id,
                "display_name": c.display_name,
                "scene_number": c.scene_number,
                "start_frame": c.start_frame,
                "end_frame_exclusive": c.end_frame_exclusive,
                "snippet": c.snippet,
            }
            for c in decision["clips"]
        ],
        "rationale": decision["rationale"],
        "fallback": decision["fallback"],
        # The ranking the SCOUT actually chose from — assets whose source vanished are not in
        # it, so the response never lists material the run could not have used.
        "ranking": ranking,
        "warnings": warnings,
        "export_id": export["id"],
        "job_id": job_id,
    }


# --- v2 production follow-up + status endpoints (Slice 4) -------------------------------------


@router.post("/production/{session_id}/message", status_code=status.HTTP_202_ACCEPTED)
def send_production_message(
    session_id: str,
    body: ProductionMessageRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """Enqueue a follow-up ``production.run`` job on top of *session_id*'s existing board.

    404 if the session is unknown, 503 if the 'autoshort' extra is missing, 404 if the session
    has no board yet (a follow-up assumes a prior production run — there is nothing to follow up
    on otherwise). ``task``/``target_seconds`` are read back from the board's own meta (fixed at
    session creation, Task 5) rather than accepted again here — the follow-up only supplies the
    new ``message`` text.
    """
    db = _db(request)
    session = repos.get_production_session(db, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    _require_autoshort()
    _require_usable_agent_config()
    asset_id = str(session["asset_id"])
    board = _open_board_or_404(db, asset_id, session_id)
    meta = board.meta()
    # LLM-driven production runs are expensive + non-idempotent — do not auto-retry.
    job_id = enqueue(
        db,
        queue=queue_for("production.run"),
        kind="production.run",
        payload={
            "asset_id": asset_id,
            "session_id": session_id,
            "task": meta.task,
            "target_seconds": int(meta.target_seconds),
            "message": body.text,
        },
        max_attempts=1,
    )
    repos.set_production_session_job(db, session_id, job_id)
    from ..short_creator.providers import config_warnings, resolve_from_env

    return {
        "session_id": session_id,
        "job_id": job_id,
        "warnings": config_warnings(resolve_from_env()),
    }


def _production_status_payload(
    db: Database,
    *,
    asset_id: str,
    board: Board,
    job_view: dict[str, Any] | None,
) -> dict[str, Any]:
    """The enriched board-status payload shared by GET /production/{sid} and the revert
    endpoint — extracted so the two responses can never drift apart."""
    from ..short_creator.board_models import ContactSheet

    result = board.status()
    result["resume_point"] = board.resume_point(_expected_scenes_for(db, asset_id))
    result["job"] = job_view
    result["board_ready"] = True
    sheet = board.load("contact_sheet")
    if isinstance(sheet, ContactSheet):
        result["artifacts"]["contact_sheet"].update(
            png_path=sheet.png_path,
            labeled=sheet.labeled,
            tiles=[t.model_dump() for t in sheet.tiles],
        )
    return result


@router.get("/production/{session_id}")
def get_production_status(
    session_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("read"))],
) -> dict[str, Any]:
    """Read-only status for *session_id*: the running job's liveness, plus the board when ready.

    404 only if the session is unknown. A session whose board does not exist yet — queued, or
    died before building one — returns ``{"job": ..., "board_ready": false}`` rather than 404,
    because that dead-before-a-board run is exactly the case liveness has to surface. Never
    enqueues anything and never requires the 'autoshort' extra — a pure read of what is on disk.
    A present contact_sheet artifact additionally carries its ``png_path``/``labeled``/``tiles``
    inside the artifacts block (next to the chain-standard version fields), so a client can
    show the checkpoint without a second lookup; the image bytes come from
    ``GET /production/{session_id}/contact-sheet``.
    """
    from ..short_creator.board import Board
    from ..short_creator.production_orchestrator import board_root_for

    db = _db(request)
    session = repos.get_production_session(db, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    asset_id = str(session["asset_id"])

    # Liveness: the one field the incident needed and did not have. The job is the authority on
    # whether the run is alive — a hanging run is a "running" job with an expired lease, a dead
    # run is a "failed" job — and it is looked up BEFORE the board, because the very failure
    # this closes is a run that died before a readable board existed. The board alone kept
    # reading "active" for 55 minutes; requiring it to answer would hide exactly the dead run.
    job_id = session.get("latest_job_id")
    job = repos.get_job(db, str(job_id)) if job_id else None
    job_view = _job_view(job)

    try:
        board = Board.open(board_root_for(db, asset_id, session_id))
    except (ValueError, FileNotFoundError):
        # No board yet — the run is queued, or it died before building one. That is a state to
        # report, not a 404: the job view carries the answer.
        return {"session_id": session_id, "job": job_view, "board_ready": False}

    return _production_status_payload(db, asset_id=asset_id, board=board, job_view=job_view)


@router.get("/production/{session_id}/contact-sheet")
def get_production_contact_sheet(
    session_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("read"))],
) -> FileResponse:
    """Serve the session's current contact-sheet PNG (the visual pre-render checkpoint).

    DB-lookup-before-fs guard (mirrors ``GET /shots/{shot_id}/thumbnail``): *session_id* only
    ever resolves through the sessions table and the board's own artifact record — the served
    path is the one ``save_contact_sheet`` wrote into the board, never one derived from client
    input. 404 for an unknown session, a board without a contact_sheet yet, or a png missing
    on disk.
    """
    from ..short_creator.board_models import ContactSheet

    db = _db(request)
    session = repos.get_production_session(db, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    board = _open_board_or_404(db, str(session["asset_id"]), session_id)
    sheet = board.load("contact_sheet")
    if not isinstance(sheet, ContactSheet):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no contact sheet on the board yet")
    path = Path(sheet.png_path)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "contact sheet missing on disk")
    return FileResponse(path, media_type="image/png")


@router.post("/production/{session_id}/revert")
def revert_production_artifact(
    session_id: str,
    body: ProductionRevertRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """Revert one board artifact to an archived version and heal the suffix — synchronously,
    no job and no agent turn. Mirrors the revert_artifact tool's validation; then
    ``board.revert`` + ``restore_coherent_suffix``. 409 while a job is queued/running (a
    revert under a live team run would race it). Returns the same enriched status payload
    as ``GET /production/{sid}`` so the UI updates without a second fetch.

    Accepted TOCTOU: the job-status check above races an in-flight enqueue by design — the
    worst case is a benign reorder (every run reads the board fresh at its own start), which
    is fine for a local single-user tool.
    """
    from ..short_creator.board import Board, downstream_of
    from ..short_creator.production_orchestrator import board_root_for

    db = _db(request)
    session = repos.get_production_session(db, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    job_id = session.get("latest_job_id")
    job = repos.get_job(db, str(job_id)) if job_id else None
    if job is not None and str(job["status"]) in ("queued", "running"):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "run in progress — revert would race the team"
        )
    asset_id = str(session["asset_id"])
    try:
        board = Board.open(board_root_for(db, asset_id, session_id))
    except (ValueError, FileNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session has no board") from None

    valid_names = downstream_of("scene_reviews")
    if body.artifact not in valid_names:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"unknown artifact '{body.artifact}'; valid: {', '.join(valid_names)}",
        )
    invalidated = [d for d in downstream_of(body.artifact) if board.load(d) is not None]
    try:
        board.revert(body.artifact, body.version)
    except FileNotFoundError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"no archived {body.artifact} v{body.version}",
        ) from None
    restored = board.restore_coherent_suffix()

    job_view = _job_view(job)
    return {
        "ok": True,
        "artifact": body.artifact,
        "version": body.version,
        "invalidated": invalidated,
        "restored": restored,
        "status": _production_status_payload(
            db, asset_id=asset_id, board=board, job_view=job_view
        ),
    }
