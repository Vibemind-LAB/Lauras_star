"""NL-agent short-creator endpoint (Iteration 8).

``POST /assets/{asset_id}/auto-short`` enqueues a ``short_creator.run`` job that builds a short
about *topic* from the asset via the multi-agent escalation ladder. Requires the optional
``autoshort`` extra; without it the endpoint returns 503. Poll ``/jobs/{id}`` for completion.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, TYPE_CHECKING, Annotated, Any, Literal, Self

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..auth import Principal, require_permission
from ..db import repos
from ..db.database import Database
from ..jobs.queues import queue_for
from ..jobs.runner import enqueue

# Pure-pydantic leaf: safe at runtime even without the optional 'autoshort' extra, unlike
# short_creator.board below.
from ..short_creator.board_models import (
    ContactSheet,
    Format,
    SceneSelection,
    Script,
    VisualPlan,
    VisualRecutRequest,
    VisualSceneSelection,
    VoiceArtifact,
    content_hash,
)

# discovery/scout import nothing from autogen at module load either (Tasks 1-2 of the
# auto-short arc) — safe here too. run_scout is imported at module level (rather than inside
# the endpoint, like the other autogen-touching calls below) specifically so tests can
# monkeypatch laura.api.short_creator.run_scout.
from ..short_creator.discovery import search_material
from ..short_creator.overview_build import build_overview
from ..short_creator.overview_scout import OverviewDecision, run_overview_scout
from ..short_creator.overview_windows import build_candidates, duration_seconds
from ..short_creator.scout import ScoutDecision, run_scout
from ..short_creator.visual_selection_drafts import (
    VisualDraftValidationError,
    VisualSelectionDraftView,
    default_visual_selections,
    draft_view_from_row,
    validate_draft_selections,
)
from ..short_creator.visual_selection_state import (
    SourceMediaStaleError,
    validate_source_media_snapshot,
)
from ..short_creator.visual_timeline import VisualSelectionError, apply_scene_selections
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
    # "external": create the session + board for an outside author (MCP) — both gates on,
    # no team job. "team" keeps the existing behavior byte-identical.
    author: Literal["team", "external"] = "team"


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


class SceneSelectionConfirmRequest(BaseModel):
    scene_numbers: list[int]
    # The proposal version the picker was showing — see confirm_scene_selection. Optional so
    # older clients (and agent-side confirms, which have no screen to read a version off) keep
    # working exactly as before.
    selection_version: int | None = None


class VisualSceneSelectionRequest(BaseModel):
    """Strict HTTP transport shape for one v2 Rough-Cut decision."""

    model_config = ConfigDict(extra="forbid")

    rough_cut_order: int = Field(ge=0, strict=True)
    candidate_id: str = Field(min_length=1, strict=True)
    included: bool = Field(strict=True)
    requested_duration_s: int = Field(ge=1, le=10, strict=True)


class VisualSelectionConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    selections: list[VisualSceneSelectionRequest] | None = None
    selected_candidate_ids: list[str] | None = None

    @model_validator(mode="after")
    def _exactly_one_payload_shape(self) -> Self:
        if (self.selections is None) == (self.selected_candidate_ids is None):
            raise ValueError("provide selections for v2 or selected_candidate_ids for v1")
        return self


class VisualSelectionDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_revision: int | None = Field(default=None, ge=1, strict=True)
    selections: list[VisualSceneSelectionRequest]


class ContactSheetConfirmRequest(BaseModel):
    contact_sheet_hash: str = Field(min_length=64, max_length=64)


# --- author-mode write bodies (Task 8) — map 1:1 onto the team tool closures'  kwargs ---------


class SceneProposalRequest(BaseModel):
    candidates: list[dict[str, Any]] = Field(min_length=1)


class StorylineRequest(BaseModel):
    red_thread: str = Field(min_length=1)
    chapters: list[dict[str, Any]] = Field(min_length=1)


class ScriptChapterRequest(BaseModel):
    lines: list[dict[str, Any]] = Field(min_length=1)


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
    script_gate: bool = False,
    scene_gate: bool = False,
) -> tuple[str, str]:
    """Create a v2 production session row for *asset_id* and enqueue its ``production.run`` job.

    Extracted verbatim from ``create_production`` (Task 3 of the auto-short arc) so a second
    caller — the project-scoped auto-short endpoint — creates sessions identically. The session
    row is created before the job is enqueued: a session without a job is harmless (visible,
    just never progresses), while a job without a session row would reference an entity that
    doesn't exist. Returns ``(session_id, job_id)``.

    ``script_gate`` (Gate B, opt-in) and ``scene_gate`` (Gate S, opt-in) are only ever added to
    the job payload when True — a caller that never asks for either (every caller except
    ``run_project_auto_short``) enqueues the exact payload shape this always had, unchanged.
    """
    session_id = new_id()
    created_utc = datetime.now(UTC).isoformat(timespec="seconds")
    repos.create_production_session(
        db,
        session_id=session_id,
        asset_id=asset_id,
        created_utc=created_utc,
        brief_text=task,
    )
    payload: dict[str, Any] = {
        "asset_id": asset_id,
        "session_id": session_id,
        "task": task,
        "target_seconds": target_seconds,
        "format": format,
        "language": language,
    }
    if script_gate:
        payload["script_gate"] = True
    if scene_gate:
        payload["scene_gate"] = True
    # LLM-driven production runs are expensive + non-idempotent — do not auto-retry.
    job_id = enqueue(
        db,
        queue=queue_for("production.run"),
        kind="production.run",
        payload=payload,
        max_attempts=1,
    )
    repos.set_production_session_job(db, session_id, job_id)
    return session_id, job_id


# 409 detail for the team chat path refusing an author-mode session (Task 7). The matching
# "team session" refusal for the author endpoints refusing a team-mode session is Task 8.
_AUTHOR_SESSION_DETAIL = "author session — write via the author endpoints, not the team chat"


def _create_author_production_session(
    db: Database,
    asset_id: str,
    *,
    task: str,
    target_seconds: float,
    format: Format,
    language: str,
) -> str:
    """Author-mode session: row + board synchronously, gates forced, NO production.run job.

    The board must exist before the create call returns (the author writes against it next),
    unlike the team path where run_production creates it at job time.
    """
    session_id = new_id()
    created_utc = datetime.now(UTC).isoformat(timespec="seconds")
    repos.create_production_session(
        db, session_id=session_id, asset_id=asset_id,
        created_utc=created_utc, brief_text=task,
    )
    from ..short_creator.board import Board
    from ..short_creator.board_models import BoardMeta
    from ..short_creator.production_orchestrator import board_root_for

    meta = BoardMeta(
        session_id=session_id, asset_id=asset_id, created_utc=created_utc,
        task=task, format=format, language=language,
        target_seconds=float(target_seconds),
        script_gate=True, scene_gate=True, author="external",
    )
    Board.create(board_root_for(db, asset_id, session_id), meta)
    return session_id


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

    ``body.author == "external"`` takes the author-mode branch instead: session + board created
    synchronously with both gates forced, no team job (see
    :func:`_create_author_production_session`). Both prerequisite checks below still run first
    for that branch too — the board's later gated approval takes the deterministic tail, which
    needs a usable agent config for its QA stage.
    """
    db = _db(request)
    _get_asset_or_404(db, asset_id)
    _require_autoshort()
    _require_usable_agent_config()
    if body.author == "external":
        session_id = _create_author_production_session(
            db, asset_id, task=body.task, target_seconds=float(body.target_seconds),
            format=body.format, language=body.language,
        )
        from ..short_creator.providers import config_warnings, resolve_from_env

        return {
            "session_id": session_id, "job_id": None, "board_ready": True,
            "warnings": config_warnings(resolve_from_env()),
        }
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


def run_project_auto_short(
    db: Database,
    project_id: str,
    *,
    topic: str,
    target_seconds: int,
    format: Format,
    language: str,
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
    if repos.get_project(db, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    _require_autoshort()
    _require_usable_agent_config()

    material = search_material(db, project_id, topic)
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

    # Check for unconfirmed transcripts in the ranking.
    unconfirmed_assets: list[str] = []
    for entry in ranking:
        asset = repos.get_asset(db, str(entry["asset_id"]))
        if asset is not None and not asset.get("transcript_confirmed_at"):
            unconfirmed_assets.append(str(entry.get("display_name") or ""))

    from ..short_creator.providers import config_warnings, resolve_from_env

    config = resolve_from_env()
    decision: ScoutDecision = run_scout(
        db, config, project_id=project_id, topic=topic, material=material
    )

    # decision["asset_id"] is always one of material["ranking"]'s asset ids: run_scout only ever
    # adopts a reply after validating asset_id against the ranking, and its fallback picks the
    # ranking's own top entry — so this lookup can never miss.
    chosen = next(e for e in material["ranking"] if e["asset_id"] == decision["asset_id"])
    snippets = [hit["snippet"] for hit in chosen["scene_hits"]]
    task = (
        f"{topic}\n\n"
        f"Material scout: use asset '{chosen['display_name']}'. Focus on scenes "
        f"{', '.join(map(str, decision['scene_numbers']))} — transcript hits: "
        f"{'; '.join(snippets)}. Scout rationale: {decision['rationale']}"
    )

    session_id, job_id = _create_production_session(
        db,
        decision["asset_id"],
        task=task,
        target_seconds=target_seconds,
        format=format,
        language=language,
        # Gate B: a chat-driven short pauses after the script for the user to approve it —
        # deliberately NOT set on auto-overview (Phase 2 does not use the production board at
        # all) or on the plain POST /assets/{asset_id}/production endpoint.
        script_gate=True,
        # Gate S (spec 2026-08-06): same opt-in as Gate B above, same exclusions — a chat-driven
        # short pauses after the team proposes scenes for the user to pick before storyline/
        # script work starts.
        scene_gate=True,
    )

    warnings = config_warnings(config)
    if missing_sources:
        warnings = [
            *warnings,
            "left out of the material, source file missing: " + ", ".join(missing_sources),
        ]
    if unconfirmed_assets:
        warnings = [
            *warnings,
            *(f"Transkript unbestätigt: {name}" for name in unconfirmed_assets),
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


@router.post("/projects/{project_id}/auto-short", status_code=status.HTTP_202_ACCEPTED)
def create_project_auto_short(
    project_id: str,
    body: ProjectAutoShortRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """Topic in, scouted v2 production session out. See :func:`run_project_auto_short`."""
    return run_project_auto_short(
        _db(request),
        project_id,
        topic=body.topic,
        target_seconds=body.target_seconds,
        format=body.format,
        language=body.language,
    )


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


def run_project_auto_overview(
    db: Database,
    project_id: str,
    *,
    topic: str,
    target_seconds: int,
    language: str,
) -> dict[str, Any]:
    """Topic in, a watchable overview cut across several videos out.

    Phase 2 of the auto-short arc: where ``POST /projects/{id}/auto-short`` scouts ONE asset
    and runs the board production on it, this route mixes SEVERAL videos through the sequence
    machinery — a new sequence (never the project's own) plus an enqueued render.

    404 unknown project; 503 preflight (missing extra / unusable agent config); 422 when the
    topic finds no material, no window survives, or the target is shorter than every
    candidate — all BEFORE anything is written.

    ``language`` is accepted and echoed for symmetry with the short's service function, but
    changes nothing in v1: the cut runs on the clips' ORIGINAL audio, so there is no script to
    write in any language (mirrors :class:`ProjectAutoOverviewRequest`'s own docstring).
    """
    if repos.get_project(db, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    _require_autoshort()
    _require_usable_agent_config()

    material = search_material(db, project_id, topic)
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

    # Check for unconfirmed transcripts in the ranking.
    unconfirmed_assets: list[str] = []
    for entry in ranking:
        asset = repos.get_asset(db, str(entry["asset_id"]))
        if asset is not None and not asset.get("transcript_confirmed_at"):
            unconfirmed_assets.append(str(entry.get("display_name") or ""))

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
        topic=topic,
        candidates=candidates,
        target_seconds=target_seconds,
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
                    f"target_seconds ({target_seconds}) is shorter than the shortest "
                    f"available clip (~{shortest_s:.1f}s) — raise target_seconds to at least "
                    "that length"
                ),
                "target_seconds": target_seconds,
                "shortest_candidate_seconds": round(shortest_s, 1),
            },
        )

    built = build_overview(db, project_id=project_id, topic=topic, clips=decision["clips"])

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
                f"({target_seconds}) left room for only one clip after trimming",
            ]
    if missing_sources:
        warnings = [
            *warnings,
            "left out of the overview, source file missing: " + ", ".join(missing_sources),
        ]
    if unconfirmed_assets:
        warnings = [
            *warnings,
            *(f"Transkript unbestätigt: {name}" for name in unconfirmed_assets),
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


@router.post("/projects/{project_id}/auto-overview", status_code=status.HTTP_202_ACCEPTED)
def create_project_auto_overview(
    project_id: str,
    body: ProjectAutoOverviewRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """Topic in, an overview cut across several videos out.

    See :func:`run_project_auto_overview`.
    """
    return run_project_auto_overview(
        _db(request),
        project_id,
        topic=body.topic,
        target_seconds=body.target_seconds,
        language=body.language,
    )


# --- v2 production follow-up + status endpoints (Slice 4) -------------------------------------


def _enqueue_production_run(
    db: Database, session_id: str, message: str | None
) -> dict[str, Any]:
    """Shared enqueue for both a text follow-up (``message`` set — a request for a team turn)
    and a pure resume (``message`` is ``None`` — the chat approval flow's recovery/continue
    path, spec 2026-08-05 modular production). 404 if the session is unknown, 503 if the
    'autoshort' extra is missing or the agent provider is not usable, 404 if the session has no
    board yet. ``task``/``target_seconds`` are always read back from the board's own meta (fixed
    at session creation, Task 5) rather than accepted again here.

    The payload OMITS the ``message`` key entirely when *message* is ``None`` — not
    ``"message": None`` — because the job handler and ``production_orchestrator``'s
    ``deterministic_eligible`` (MP3) both key off "no message" to decide whether an eligible
    gated board takes the deterministic tail instead of spinning up an agent team; a
    present-but-null key would be an easy way to accidentally reintroduce a team run through the
    resume path, so the key's mere presence is one bit and it must actually be missing.
    """
    session = repos.get_production_session(db, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    _require_autoshort()
    _require_usable_agent_config()
    asset_id = str(session["asset_id"])
    board = _open_board_or_404(db, asset_id, session_id)
    meta = board.meta()
    payload: dict[str, Any] = {
        "asset_id": asset_id,
        "session_id": session_id,
        "task": meta.task,
        "target_seconds": int(meta.target_seconds),
    }
    if message is not None:
        payload["message"] = message
    # LLM-driven production runs are expensive + non-idempotent — do not auto-retry.
    job_id = enqueue(
        db,
        queue=queue_for("production.run"),
        kind="production.run",
        payload=payload,
        max_attempts=1,
    )
    repos.set_production_session_job(db, session_id, job_id)
    from ..short_creator.providers import config_warnings, resolve_from_env

    return {
        "session_id": session_id,
        "job_id": job_id,
        "warnings": config_warnings(resolve_from_env()),
    }


def run_production_follow_up(db: Database, session_id: str, text: str) -> dict[str, Any]:
    """Enqueue a follow-up ``production.run`` job on top of *session_id*'s existing board.

    404 if the session is unknown, 503 if the 'autoshort' extra is missing, 404 if the session
    has no board yet (a follow-up assumes a prior production run — there is nothing to follow up
    on otherwise). ``task``/``target_seconds`` are read back from the board's own meta (fixed at
    session creation, Task 5) rather than accepted again here — the follow-up only supplies the
    new ``message`` text.

    409 if the session is author-mode (``BoardMeta.author == "external"``, Task 7): those boards
    are written by an outside author through the author endpoints, not by the team here — this
    is the team chat path's half of the lockout, checked before the (agent-config-requiring)
    enqueue below runs at all.
    """
    session = repos.get_production_session(db, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    board = _open_board_or_404(db, str(session["asset_id"]), session_id)
    if board.meta().author == "external":
        raise HTTPException(status.HTTP_409_CONFLICT, _AUTHOR_SESSION_DETAIL)
    return _enqueue_production_run(db, session_id, text)


def run_production_resume(db: Database, session_id: str) -> dict[str, Any]:
    """Enqueue the SAME kind of ``production.run`` job as :func:`run_production_follow_up`, but
    with NO ``message`` — the chat approval flow's resume (spec 2026-08-05 modular production):
    an eligible gated board (``production_orchestrator.deterministic_eligible``) takes the
    deterministic post-gate tail instead of a full agent-team turn.
    """
    return _enqueue_production_run(db, session_id, None)


_NO_SCRIPT_DETAIL = "no script to approve yet — the production pauses at the gate by itself"
_APPROVE_BUSY_DETAIL = "a production job is running on this session — wait for it to finish"


def _production_job_busy(db: Database, session: dict[str, Any]) -> bool:
    """The session's latest production job is still queued/running.

    Lives here — not in ``chat/executor.py``, which already imports a long list of names FROM
    this module — so :func:`approve_production_script` and both of ``chat/executor.py``'s call
    sites share one definition; ``chat.executor`` imports it back from here (the reverse import
    would be circular). Mirrors :func:`run_production_revert`'s own inline guard exactly (same
    ``latest_job_id`` -> ``repos.get_job`` -> status check), so chat, the revert endpoint, and
    this service all agree on what "busy" means.

    Two call sites in ``chat/executor.py``, both BEFORE they touch the board or enqueue
    anything:

    - ``_handle_approve_script`` (via :func:`approve_production_script`, I2, 2026-08-05
      review): a double "Script freigeben" while the first run was still in flight used to
      enqueue a SECOND concurrent ``production.run`` job against the same board files — two
      runs racing each other. Called before either the approval stamp or the resume enqueue,
      on both the fresh-approval and the already-current-but-unfinished-resume paths.
    - ``_handle_follow_up`` (I1, 2026-08-05 final review): a follow_up sent mid-tail used to
      enqueue unconditionally, starting a CONCURRENT team run (workers=3) on the same board
      and overwriting ``latest_job_id`` — hiding the still-running tail from the UI.
    """
    job_id = session.get("latest_job_id")
    job = repos.get_job(db, str(job_id)) if job_id else None
    return job is not None and str(job["status"]) in ("queued", "running")


def approve_production_script(
    db: Database,
    session_id: str,
    *,
    now_utc: str | None = None,
    resume: Callable[[Database, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Gate B core (Task 9 extraction): the exact approval semantics chat's "Script freigeben"
    has always run, now shared with the HTTP ``script:approve`` endpoint. Previously this whole
    block lived only in ``chat/executor.py::_handle_approve_script``; that handler now resolves
    the session and turns this function's outcome/exceptions into chat text, but the actual
    board mutation happens here for both callers.

    Outcome dict: ``{"outcome": "resumed", "session_id", "job_id"}`` on a fresh or re-triggered
    approval; ``{"outcome": "already_done", "session_id"}`` when the stamp is content-current
    AND the board is finished. Raises ``HTTPException`` 404 (unknown session/board), 409
    (:data:`_NO_SCRIPT_DETAIL` / :data:`_APPROVE_BUSY_DETAIL`).

    Approval is bound to the script's CONTENT, not just a bare timestamp
    (:func:`~laura.short_creator.board_models.content_hash`, the same idiom
    ``synthesize_script_voice``/``build_cutlist`` already use for staleness): an
    already-approved board whose stamped hash still matches the CURRENT script is
    content-current (``already_current`` below), but one whose script has since changed (a team
    rewrite, or a revert to a DIFFERENT version) is treated as a FRESH approval — the whole
    point of content-binding is that the old stamp no longer speaks for new text.

    A content-current approval is a genuine no-op (``"already_done"``, no new run) ONLY when the
    board is actually finished (``resume_point == "done"``) — approving twice must not burn a
    second production turn once nothing is left to do. But a content-current approval on an
    UNFINISHED board (a prior tail died mid-chain, or the user is just re-confirming) must
    resume again: the whole recovery path after a failed deterministic tail is a second "Script
    freigeben", so this branch enqueues another :func:`run_production_resume` call instead of
    only reporting done.

    The busy check runs BEFORE either the approval stamp or the enqueue, on BOTH remaining paths
    (the already-current-but-unfinished resume above, and a fresh approval below). Ordering
    matters: stamping the fresh path FIRST and only then discovering the run is busy would open
    the gate for the STILL-RUNNING job mid-flight even though this call never actually starts a
    new one — so the busy check runs first, no stamp either way.

    The approval stamp must land on the board BEFORE the resume run is enqueued (the voice tool
    reads ``meta.script_approved_utc``/``script_approved_script_hash`` at job runtime, which can
    start before the enqueue call here even returns), so a failure to start that run cannot be
    fixed by reordering the two calls — only by a COMPENSATING rollback
    (``board.clear_script_approval()``) once the failure is known. The rollback is conditional
    on ``not already_current``: it must never erase an approval that was ALREADY on the board
    before this call started — only the fresh stamp this call itself just wrote.

    ``now_utc``/``resume`` default to the real clock / this module's own
    :func:`run_production_resume` when omitted — every real caller (the HTTP endpoint, and the
    direct-service tests) omits both. ``chat/executor.py`` passes its own ``now_utc`` (the same
    timestamp it stamps every other message in the turn with) and its own name-imported,
    test-patchable ``run_production_resume`` reference explicitly, so a chat approval's stamp
    and resume call stay exactly what they were before this extraction.
    """
    session = repos.get_production_session(db, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    asset_id = str(session["asset_id"])

    from ..short_creator.board import Board
    from ..short_creator.board_models import Script, content_hash
    from ..short_creator.production_orchestrator import board_root_for

    try:
        board = Board.open(board_root_for(db, asset_id, session_id))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session board not found") from exc

    script = board.load("script")
    if not isinstance(script, Script):
        raise HTTPException(status.HTTP_409_CONFLICT, _NO_SCRIPT_DETAIL)
    current_hash = content_hash(script)

    meta = board.meta()
    already_current = (
        meta.script_approved_utc is not None
        and meta.script_approved_script_hash == current_hash
    )
    if already_current and board.resume_point(_expected_scenes_for(db, asset_id)) == "done":
        return {"outcome": "already_done", "session_id": session_id}

    if _production_job_busy(db, session):
        raise HTTPException(status.HTTP_409_CONFLICT, _APPROVE_BUSY_DETAIL)

    stamp_utc = (
        now_utc if now_utc is not None else datetime.now(UTC).isoformat(timespec="seconds")
    )
    if not already_current:
        board.set_script_approved(stamp_utc, current_hash)

    resume_fn = resume if resume is not None else run_production_resume
    try:
        result = resume_fn(db, session_id)
    except HTTPException:
        if not already_current:
            board.clear_script_approval()
        raise
    except Exception:
        if not already_current:
            board.clear_script_approval()
        raise
    return {"outcome": "resumed", "session_id": session_id, "job_id": result["job_id"]}


def _utc_now_iso() -> str:
    """Current UTC time, ISO-8601 seconds precision — this module's existing
    ``datetime.now(UTC).isoformat(timespec="seconds")`` idiom (see session creation above)
    wrapped for :func:`confirm_scene_selection`'s one call site."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _guard_production_not_busy(
    db: Database, session: dict[str, Any], *, action: str
) -> None:
    """Reject an approval while the session's latest production job is still alive."""
    job_id = session.get("latest_job_id")
    job = repos.get_job(db, str(job_id)) if job_id else None
    if job is not None and str(job["status"]) in ("queued", "running"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"a production run is in progress — wait for it before {action}",
        )


def delete_production_session(db: Database, session_id: str) -> dict[str, Any]:
    """Delete a production and everything it produced. The input footage stays.

    Removed: the run directory (board, its version archive, contact sheets), every export the
    board's render reports name (row and file), every voice track its voice artifacts name, and
    the session row itself (with its visual-selection draft).

    Kept: the media asset and its files, the scenes, the transcript, the analyses, the project,
    and the chat conversation the session was started from — a production is made FROM those,
    it does not own them. The per-line voice cache under ``voiceovers/lines/`` is keyed by
    content hash and shared between sessions, so it is left alone too.

    Refuses while the session's own run is queued or running: deleting a board out from under a
    live job leaves the job writing into a directory that no longer exists.
    """
    from ..short_creator.production_orchestrator import board_root_for
    from ..short_creator.session_cleanup import (
        collect_session_artifacts,
        remove_files,
        remove_tree,
    )

    session = repos.get_production_session(db, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    _guard_production_not_busy(db, session, action="deleting the production")

    asset = repos.get_asset(db, str(session["asset_id"]))
    project = (
        repos.get_project(db, str(asset["project_id"])) if asset is not None else None
    )
    exports_deleted: list[str] = []
    files_deleted: list[str] = []
    board_removed = False
    if project is not None:
        workspace_root = Path(str(project["workspace_root"]))
        run_dir = board_root_for(db, str(session["asset_id"]), session_id).parent
        artifacts = collect_session_artifacts(run_dir)
        for export_id in sorted(artifacts.export_ids):
            row = repos.get_export(db, export_id)
            if row is None:
                continue
            path = row.get("path")
            if isinstance(path, str) and path:
                files_deleted.extend(remove_files({Path(path)}, workspace_root=workspace_root))
            if repos.delete_export(db, export_id):
                exports_deleted.append(export_id)
        files_deleted.extend(
            remove_files(artifacts.media_paths, workspace_root=workspace_root)
        )
        board_removed = remove_tree(run_dir, workspace_root=workspace_root)

    repos.delete_production_session(db, session_id)
    return {
        "session_id": session_id,
        "exports_deleted": exports_deleted,
        "files_deleted": files_deleted,
        "board_removed": board_removed,
    }


def confirm_scene_selection(
    db: Database,
    session_id: str,
    scene_numbers: list[int],
    *,
    selection_version: int | None = None,
) -> dict[str, Any]:
    """Server-side Gate-S confirmation (spec 2026-08-06 §4.4): stamps the user's pick on
    the scene_selection artifact and enqueues the resume run. The ONLY writer of
    ``confirmed_utc`` — chat and HTTP both land here.

    ``selection_version`` is the version of the proposal the caller was LOOKING AT (served as
    ``scene_gate.selection_version``). Passing it closes the window between reading a proposal
    and confirming it: the agent can replace the proposal in between, and scene numbers are
    stable per asset, so a pick made against the old candidate list would otherwise be accepted
    verbatim against the new one — the stray check cannot see the difference. Omitted means
    "whatever is on the board now", which is what a caller with no version to quote (an agent
    acting on the user's behalf) genuinely means.

    The busy check mirrors :func:`run_production_revert`'s own inline guard exactly (same
    ``latest_job_id`` -> ``repos.get_job`` -> status check) rather than calling
    :func:`_production_job_busy` (same module since Task 9, but predates it here) — kept as its
    own inline copy rather than retrofitted, out of scope for that extraction.
    """
    session = repos.get_production_session(db, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    board = _open_board_or_404(db, str(session["asset_id"]), session_id)
    if not board.meta().scene_gate:
        raise HTTPException(status.HTTP_409_CONFLICT, "scene gate is not enabled")
    selection = board.load("scene_selection")
    if not isinstance(selection, SceneSelection):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "no scene proposal on the board yet"
        )
    if selection_version is not None and selection_version != selection.version:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"the scene proposal changed (you confirmed version {selection_version}, the board "
            f"is at {selection.version}) — look at the new proposal and pick again",
        )
    picked = sorted(set(int(n) for n in scene_numbers))
    if not picked:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "pick at least one scene")
    pool = {c.scene_number for c in selection.candidates}
    stray = sorted(set(picked) - pool)
    if stray:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"scenes {stray} are not among the proposed candidates",
        )
    _guard_production_not_busy(db, session, action="changing the selection")
    if selection.confirmed_utc is not None and selection.selected_scene_numbers == picked:
        # Idempotent re-confirm: a fresh timestamp would bump the version and wipe a
        # perfectly valid storyline downstream for nothing (Board.save's own no-op guard
        # compares content EXCLUDING only "version" — confirmed_utc would still differ on
        # every call, so this short-circuit has to happen here, before the save) — this branch
        # NEVER writes to the board, so the version never moves either way.
        #
        # It still HEALS a resume that never actually started: if a PRIOR confirm's stamp
        # landed but run_production_resume then raised (a transient 503, say), every later
        # re-confirm with the same picks used to hit this branch and return without ever
        # retrying the resume — the session got stuck confirmed-but-parked forever. The busy
        # guard above already proved no job is in flight, so calling run_production_resume
        # again here is safe: on a FINISHED board it hits deterministic_eligible's own
        # done-short-circuit (cheap no-op), on a still-parked one it is exactly the healing
        # this needs. No rollback machinery — heal forward instead, same "heals synchronously"
        # philosophy as run_production_revert (controller decision, 2026-08-06).
        return {
            "session_id": session_id,
            "already_current": True,
            **run_production_resume(db, session_id),
        }
    confirm_result = {"session_id": session_id, "selected": picked}
    board.save(
        "scene_selection",
        selection.model_copy(
            update={"selected_scene_numbers": picked, "confirmed_utc": _utc_now_iso()}
        ),
    )
    return {**confirm_result, **run_production_resume(db, session_id)}


def _pending_v2_visual_plan(board: Board, proposal_hash: str | None = None) -> VisualPlan:
    request = board.load("visual_recut_request")
    plan = board.load("visual_plan")
    if not isinstance(request, VisualRecutRequest) or not isinstance(plan, VisualPlan):
        raise HTTPException(status.HTTP_409_CONFLICT, "visual selection gate is not enabled")
    if not plan.scene_choices or plan.confirmed_utc is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no pending v2 visual proposal")
    if proposal_hash is not None and proposal_hash != plan.proposal_hash:
        raise HTTPException(status.HTTP_409_CONFLICT, "stale visual proposal")
    return plan


def _visual_draft_source_fingerprint(
    db: Database,
    *,
    asset_id: str,
    board: Board,
    plan: VisualPlan,
    strong: bool,
) -> str:
    from ..short_creator.production_tools import _fps, _rough_cut_source_hash
    from ..short_creator.visual_timeline import voice_total_frames

    request = board.load("visual_recut_request")
    script = board.load("script")
    voice = board.load("voice")
    if not isinstance(request, VisualRecutRequest):
        raise SourceMediaStaleError("visual_request_missing")
    if not isinstance(script, Script):
        raise SourceMediaStaleError("script_missing")
    if not isinstance(voice, VoiceArtifact):
        raise SourceMediaStaleError("voice_missing")
    rough_cut_hash = _rough_cut_source_hash(db, asset_id)
    if rough_cut_hash is None:
        raise SourceMediaStaleError("rough_cut_missing")
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        raise SourceMediaStaleError("asset_missing")
    fps = _fps(db, asset)
    current_voice_frames = voice_total_frames(voice, fps)
    current_parents = {
        "visual_recut_request": content_hash(request),
        "script": content_hash(script),
        "voice": content_hash(voice),
        "rough_cut": rough_cut_hash,
    }
    for name, parent_hash in current_parents.items():
        if plan.parents.get(name) != parent_hash:
            raise SourceMediaStaleError(f"{name}_changed")
    if plan.fps != fps:
        raise SourceMediaStaleError("frame_rate_changed")
    if plan.voice_total_frames != current_voice_frames:
        raise SourceMediaStaleError("voice_projection_changed")
    quick_hash = plan.parents.get("source_media_quick")
    strong_hash = plan.parents.get("source_media")
    if quick_hash is None or strong_hash is None:
        raise SourceMediaStaleError("source_identity_missing")
    snapshot = validate_source_media_snapshot(
        db,
        asset_id=asset_id,
        rough_cut_hash=rough_cut_hash,
        fps=fps,
        voice_hash=content_hash(voice),
        voice_total_frames=current_voice_frames,
        script_hash=content_hash(script),
        request_hash=content_hash(request),
        expected_quick_hash=quick_hash,
        expected_strong_hash=strong_hash,
        strong=strong,
    )
    return (
        snapshot.strong_hash
        if strong and snapshot.strong_hash is not None
        else snapshot.quick_hash
    )


def _draft_view_for_session(
    db: Database,
    session_id: str,
    *,
    board: Board | None = None,
) -> VisualSelectionDraftView:
    session = repos.get_production_session(db, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    active_board = board or _open_board_or_404(db, str(session["asset_id"]), session_id)
    plan = _pending_v2_visual_plan(active_board)
    row = repos.get_visual_selection_draft(db, session_id)
    if row is None:
        view = VisualSelectionDraftView(
            session_id=session_id,
            proposal_hash=plan.proposal_hash,
            selections=default_visual_selections(plan),
            revision=None,
            updated_utc=None,
        )
    else:
        view = draft_view_from_row(row)
        if view.proposal_hash != plan.proposal_hash:
            return view.model_copy(
                update={"stale": True, "stale_reason": "stale_visual_proposal"}
            )
    try:
        _visual_draft_source_fingerprint(
            db,
            asset_id=str(session["asset_id"]),
            board=active_board,
            plan=plan,
            strong=False,
        )
    except SourceMediaStaleError as exc:
        return view.model_copy(update={"stale": True, "stale_reason": exc.reason})
    return view


def _save_draft_for_session(
    db: Database,
    session_id: str,
    body: VisualSelectionDraftRequest,
) -> VisualSelectionDraftView:
    session = repos.get_production_session(db, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    board = _open_board_or_404(db, str(session["asset_id"]), session_id)
    selections = [
        VisualSceneSelection.model_validate(item.model_dump()) for item in body.selections
    ]
    with board.transaction():
        plan = _pending_v2_visual_plan(board, body.proposal_hash)
        try:
            validate_draft_selections(plan, selections)
            source_fingerprint = _visual_draft_source_fingerprint(
                db,
                asset_id=str(session["asset_id"]),
                board=board,
                plan=plan,
                strong=False,
            )
        except VisualDraftValidationError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        except SourceMediaStaleError as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {"code": "stale_visual_selection", "reason": exc.reason},
            ) from exc
        try:
            row = repos.save_visual_selection_draft(
                db,
                session_id=session_id,
                proposal_hash=body.proposal_hash,
                source_fingerprint=source_fingerprint,
                selections=[selection.model_dump(mode="json") for selection in selections],
                expected_revision=body.expected_revision,
                updated_utc=_utc_now_iso(),
            )
        except repos.DraftRevisionConflict as exc:
            current = draft_view_from_row(exc.current) if exc.current is not None else None
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": "revision_conflict",
                    "current": current.model_dump(mode="json") if current is not None else None,
                },
            ) from exc
    return draft_view_from_row(row)


def confirm_visual_selection(
    db: Database,
    session_id: str,
    proposal_hash: str,
    *,
    selections: list[VisualSceneSelection] | None = None,
    selected_candidate_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Confirm the current v1 beat or v2 Rough-Cut visual proposal.

    The proposal hash is checked before any write, and this is the sole writer of visual-plan
    confirmation state for both HTTP and chat.  A matching re-confirm performs no board write
    but retries the pure resume, healing a prior write-then-enqueue failure forward.
    """
    session = repos.get_production_session(db, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    board = _open_board_or_404(db, str(session["asset_id"]), session_id)
    with board.transaction():
        request = board.load("visual_recut_request")
        if not isinstance(request, VisualRecutRequest):
            raise HTTPException(
                status.HTTP_409_CONFLICT, "visual selection gate is not enabled"
            )
        plan = board.load("visual_plan")
        if not isinstance(plan, VisualPlan):
            raise HTTPException(
                status.HTTP_409_CONFLICT, "no visual proposal on the board yet"
            )
        if proposal_hash != plan.proposal_hash:
            raise HTTPException(status.HTTP_409_CONFLICT, "stale visual proposal")

        if plan.scene_choices:
            if selections is None or selected_candidate_ids is not None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "provide selections for a v2 visual proposal",
                )
            try:
                _visual_draft_source_fingerprint(
                    db,
                    asset_id=str(session["asset_id"]),
                    board=board,
                    plan=plan,
                    strong=True,
                )
            except SourceMediaStaleError as exc:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    {"code": "stale_visual_selection", "reason": exc.reason},
                ) from exc
            try:
                updated_plan = apply_scene_selections(plan, selections, _utc_now_iso())
            except VisualSelectionError as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)
                ) from exc

            current_session = repos.get_production_session(db, session_id)
            if current_session is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
            _guard_production_not_busy(
                db, current_session, action="changing the visual selection"
            )
            already_current = (
                plan.confirmed_utc is not None
                and plan.selection_hash == updated_plan.selection_hash
            )
            if not already_current:
                board.save("visual_plan", updated_plan)
            repos.delete_visual_selection_draft(db, session_id)
            v2_result: dict[str, Any] = {
                "session_id": session_id,
                "selection_hash": updated_plan.selection_hash,
            }
            if already_current:
                v2_result["already_current"] = True
            return {**v2_result, **run_production_resume(db, session_id)}

        if selected_candidate_ids is None or selections is not None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "provide selected_candidate_ids for a v1 visual proposal",
            )
        selected = list(selected_candidate_ids)
        if not selected:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "select exactly one candidate per beat",
            )
        candidate_to_beat = {
            candidate.candidate_id: beat.beat_id
            for beat in plan.beats
            for candidate in beat.candidates
        }
        stray = sorted(set(selected) - set(candidate_to_beat))
        if stray:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"candidate IDs {stray} are not among the current visual proposal",
            )
        selected_set = set(selected)
        if len(selected_set) != len(selected) or any(
            sum(candidate.candidate_id in selected_set for candidate in beat.candidates)
            != 1
            for beat in plan.beats
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "select exactly one candidate per beat",
            )

        current_session = repos.get_production_session(db, session_id)
        if current_session is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
        _guard_production_not_busy(
            db, current_session, action="changing the visual selection"
        )
        selected_in_beat_order = [
            next(
                candidate.candidate_id
                for candidate in beat.candidates
                if candidate.candidate_id in selected_set
            )
            for beat in plan.beats
        ]
        current = [beat.selected_candidate_id for beat in plan.beats]
        already_current = (
            plan.confirmed_utc is not None and current == selected_in_beat_order
        )
        if not already_current:
            updated_beats = [
                beat.model_copy(update={"selected_candidate_id": selected_id})
                for beat, selected_id in zip(
                    plan.beats, selected_in_beat_order, strict=True
                )
            ]
            board.save(
                "visual_plan",
                plan.model_copy(
                    update={"beats": updated_beats, "confirmed_utc": _utc_now_iso()}
                ),
            )
        result: dict[str, Any] = {
            "session_id": session_id,
            "selected_candidate_ids": selected_in_beat_order,
        }
        if already_current:
            result["already_current"] = True
        return {**result, **run_production_resume(db, session_id)}


def confirm_contact_sheet(
    db: Database, session_id: str, contact_sheet_hash: str
) -> dict[str, Any]:
    """Approve the current contact sheet by content hash and enqueue a pure resume."""
    session = repos.get_production_session(db, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    board = _open_board_or_404(db, str(session["asset_id"]), session_id)
    with board.transaction():
        meta = board.meta()
        if not meta.contact_sheet_gate:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "contact sheet gate is not enabled"
            )
        sheet = board.load("contact_sheet")
        if not isinstance(sheet, ContactSheet):
            raise HTTPException(
                status.HTTP_409_CONFLICT, "no contact sheet on the board yet"
            )
        current_hash = content_hash(sheet)
        if contact_sheet_hash != current_hash:
            raise HTTPException(status.HTTP_409_CONFLICT, "stale contact sheet")

        current_session = repos.get_production_session(db, session_id)
        if current_session is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
        _guard_production_not_busy(
            db, current_session, action="approving the contact sheet"
        )
        already_current = (
            meta.contact_sheet_approved_utc is not None
            and meta.contact_sheet_approved_hash == current_hash
        )
        if not already_current:
            board.set_contact_sheet_approved(_utc_now_iso(), current_hash)
        result: dict[str, Any] = {
            "session_id": session_id,
            "contact_sheet_hash": current_hash,
        }
        if already_current:
            result["already_current"] = True
        return {**result, **run_production_resume(db, session_id)}


@router.delete("/production/{session_id}")
def delete_production_session_endpoint(
    session_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """Delete a production and everything it produced. See :func:`delete_production_session`."""
    return delete_production_session(_db(request), session_id)


@router.post(
    "/production/{session_id}/scene-selection:confirm",
    status_code=status.HTTP_202_ACCEPTED,
)
def confirm_scene_selection_endpoint(
    session_id: str,
    body: SceneSelectionConfirmRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """Confirm the user's Gate-S scene pick. See :func:`confirm_scene_selection`."""
    return confirm_scene_selection(
        _db(request),
        session_id,
        body.scene_numbers,
        selection_version=body.selection_version,
    )


@router.post(
    "/production/{session_id}/visual-selection:confirm",
    status_code=status.HTTP_202_ACCEPTED,
)
def confirm_visual_selection_endpoint(
    session_id: str,
    body: VisualSelectionConfirmRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """Confirm the current hash-bound visual proposal and enqueue a pure resume."""
    return confirm_visual_selection(
        _db(request),
        session_id,
        body.proposal_hash,
        selections=(
            [VisualSceneSelection.model_validate(item.model_dump()) for item in body.selections]
            if body.selections is not None
            else None
        ),
        selected_candidate_ids=body.selected_candidate_ids,
    )


@router.get("/production/{session_id}/visual-selection/draft")
def get_visual_selection_draft_endpoint(
    session_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("read"))],
) -> dict[str, Any]:
    """Read the persisted v2 draft or deterministic recommendations without mutation."""
    return _draft_view_for_session(_db(request), session_id).model_dump(mode="json")


@router.put("/production/{session_id}/visual-selection/draft")
def put_visual_selection_draft_endpoint(
    session_id: str,
    body: VisualSelectionDraftRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """Persist one complete intermediate v2 decision set with revision CAS."""
    return _save_draft_for_session(_db(request), session_id, body).model_dump(mode="json")


@router.post(
    "/production/{session_id}/contact-sheet:confirm",
    status_code=status.HTTP_202_ACCEPTED,
)
def confirm_contact_sheet_endpoint(
    session_id: str,
    body: ContactSheetConfirmRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """Approve the current hash-bound contact sheet and enqueue a pure resume."""
    return confirm_contact_sheet(_db(request), session_id, body.contact_sheet_hash)


@router.post("/production/{session_id}/script:approve", status_code=status.HTTP_202_ACCEPTED)
def approve_script_endpoint(
    session_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """Approve the current script (content-hash-bound) and run the deterministic tail.

    The HTTP form of chat's "Script freigeben" — see :func:`approve_production_script`. Serves
    both team and author-mode sessions alike: unlike ``run_production_follow_up``, this is not
    a creative write the author/team lockout needs to arbitrate, it is the same gate either kind
    of session pauses at.
    """
    return approve_production_script(_db(request), session_id)


@router.post("/production/{session_id}/message", status_code=status.HTTP_202_ACCEPTED)
def send_production_message(
    session_id: str,
    body: ProductionMessageRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """Enqueue a follow-up production run. See :func:`run_production_follow_up`."""
    return run_production_follow_up(_db(request), session_id, body.text)


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
    visual_gate = result.get("visual_selection_gate")
    if (
        isinstance(visual_gate, dict)
        and visual_gate.get("pending")
        and visual_gate.get("scene_choices")
    ):
        visual_gate["draft"] = _draft_view_for_session(
            db, board.meta().session_id, board=board
        ).model_dump(mode="json")
    sheet = board.load("contact_sheet")
    if isinstance(sheet, ContactSheet):
        result["artifacts"]["contact_sheet"].update(
            png_path=sheet.png_path,
            labeled=sheet.labeled,
            tiles=[t.model_dump() for t in sheet.tiles],
        )
    return result


def _brief_preview(value: Any, *, limit: int = 160) -> str:
    compact = " ".join(str(value or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _open_production_session_views(db: Database) -> list[dict[str, Any]]:
    """Return resumable sessions without mutating boards, drafts, or jobs."""
    from ..short_creator.board import Board
    from ..short_creator.production_orchestrator import board_root_for

    views: list[dict[str, Any]] = []
    for session in repos.list_production_sessions_by_updated(db):
        session_id = str(session["session_id"])
        asset_id = str(session["asset_id"])
        asset = repos.get_asset(db, asset_id)
        project_id = str(asset["project_id"]) if asset is not None else None
        display_name = str(asset["display_name"]) if asset is not None else "Missing asset"
        job_id = str(session["latest_job_id"]) if session.get("latest_job_id") else None
        job = repos.get_job(db, job_id) if job_id is not None else None
        job_status = str(job["status"]) if job is not None else None
        running = job_status in ("queued", "running")
        draft = repos.get_visual_selection_draft(db, session_id)
        resume_point: str | None = None
        stale = False
        stale_reason: str | None = None

        try:
            if asset is None:
                raise ValueError("asset_missing")
            board = Board.open(board_root_for(db, asset_id, session_id))
            status_payload = board.status()
            resume_point = board.resume_point(_expected_scenes_for(db, asset_id))
            visual_gate = status_payload.get("visual_selection_gate") or {}
            if (
                visual_gate.get("pending")
                and visual_gate.get("scene_choices")
            ):
                draft_view = _draft_view_for_session(db, session_id, board=board)
                stale = draft_view.stale
                stale_reason = draft_view.stale_reason

            if running:
                state = "running"
            elif any(
                (status_payload.get(name) or {}).get("pending")
                for name in (
                    "script_gate",
                    "scene_gate",
                    "visual_selection_gate",
                    "contact_sheet_gate",
                )
            ):
                state = "awaiting-approval"
            elif board.meta().status in ("complete", "failed", "cancelled"):
                continue
            else:
                state = "in-progress"
        except (FileNotFoundError, ValueError):
            if running:
                state = "running"
            else:
                state = "needs-attention"
                stale = True
                stale_reason = "board_unavailable"
        except Exception:  # noqa: BLE001 - one corrupt session must not hide the rest
            logger.warning("open production session could not be inspected", exc_info=True)
            state = "needs-attention"
            stale = True
            stale_reason = "board_unreadable"

        views.append(
            {
                "session_id": session_id,
                "conversation_id": session.get("conversation_id"),
                "project_id": project_id,
                "asset_id": asset_id,
                "asset_display_name": display_name,
                "brief_preview": _brief_preview(session.get("brief_text")),
                "resume_point": resume_point,
                "state": state,
                "updated_utc": str(session["updated_utc"]),
                "draft_updated_utc": str(draft["updated_utc"]) if draft is not None else None,
                "latest_job_id": job_id,
                "stale": stale,
                "stale_reason": stale_reason,
            }
        )
    return views


@router.get("/production-sessions/open")
def list_open_production_sessions(
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("read"))],
) -> list[dict[str, Any]]:
    """List running, resumable, and diagnostic production sessions newest first."""
    return _open_production_session_views(_db(request))


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


@router.get("/production/{session_id}/events")
def get_production_events(
    session_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("read"))],
    after: int = 0,
) -> dict[str, Any]:
    """The session's newest run log as a pollable event stream (spec 2026-08-03).

    Cursor = 0-based line index into the newest ``runs/*.ndjson``; unparsable lines are
    skipped but still advance the cursor, so a client can never loop on a bad line.
    ``done`` mirrors whether a terminal ``{"type": "done"}`` line exists in the file.
    """
    from ..short_creator.production_orchestrator import board_root_for

    db = _db(request)
    session = repos.get_production_session(db, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    try:
        runs_dir = board_root_for(db, str(session["asset_id"]), session_id).parent / "runs"
    except ValueError:
        return {"events": [], "next": max(0, after), "done": False}
    logs = (
        sorted(runs_dir.glob("*.ndjson"), key=lambda p: p.stat().st_mtime)
        if runs_dir.is_dir()
        else []
    )
    if not logs:
        return {"events": [], "next": max(0, after), "done": False}
    lines = logs[-1].read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(0, after)
    events: list[dict[str, Any]] = []
    done = False
    for line in lines:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("type") == "done":
            done = True
    for line in lines[start:]:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return {"events": events, "next": len(lines), "done": done}


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


def run_production_revert(
    db: Database, session_id: str, artifact: str, version: int
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
    if artifact not in valid_names:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"unknown artifact '{artifact}'; valid: {', '.join(valid_names)}",
        )
    invalidated = [d for d in downstream_of(artifact) if board.load(d) is not None]
    try:
        board.revert(artifact, version)
    except FileNotFoundError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"no archived {artifact} v{version}",
        ) from None
    restored = board.restore_coherent_suffix()

    job_view = _job_view(job)
    return {
        "ok": True,
        "artifact": artifact,
        "version": version,
        "invalidated": invalidated,
        "restored": restored,
        "status": _production_status_payload(
            db, asset_id=asset_id, board=board, job_view=job_view
        ),
    }


@router.post("/production/{session_id}/revert")
def revert_production_artifact(
    session_id: str,
    body: ProductionRevertRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """Revert a board artifact and heal the suffix. See :func:`run_production_revert`."""
    return run_production_revert(_db(request), session_id, body.artifact, body.version)


# --- author-mode write endpoints (Task 8) -------------------------------------------------------
#
# An external author writes creative artifacts straight into an author-mode session's board —
# no team job, no agent turn. Each endpoint is a thin body-to-kwargs mapping onto the SAME tool
# closure the AutoGen team calls (``laura.short_creator.authoring.call_production_tool``): one
# guard source (capacity, grounding, gate arming, selection_version) for both callers.


@router.put("/production/{session_id}/scene-proposal")
def put_scene_proposal(
    session_id: str,
    body: SceneProposalRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """Author mode: propose scenes (arms Gate S, bumps selection_version)."""
    from ..short_creator.authoring import call_production_tool

    return call_production_tool(
        _db(request), session_id, "propose_scene_selection", candidates=body.candidates
    )


@router.put("/production/{session_id}/storyline")
def put_storyline(
    session_id: str,
    body: StorylineRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """Author mode: write the storyline (window references validated by the tool core)."""
    from ..short_creator.authoring import call_production_tool

    return call_production_tool(
        _db(request), session_id, "save_storyline",
        red_thread=body.red_thread, chapters=body.chapters,
    )


@router.put("/production/{session_id}/script/chapters/{chapter}")
def put_script_chapter(
    session_id: str,
    chapter: int,
    body: ScriptChapterRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """Author mode: write one script chapter (capacity guard replies as in the team path)."""
    from ..short_creator.authoring import call_production_tool

    return call_production_tool(
        _db(request), session_id, "save_script_chapter", chapter=chapter, lines=body.lines
    )
