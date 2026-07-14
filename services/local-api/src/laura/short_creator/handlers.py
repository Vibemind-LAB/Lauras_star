"""``short_creator.run`` + ``production.run`` job handlers (Iteration 8, Slice 4 Task 4).

Runs the NL-agent short-creator (escalation ladder) for an asset + topic, and the v2
production-board run (resume-aware board + magentic-only ladder) for an asset + session. AutoGen
is an OPTIONAL extra for both: the orchestrator/provider imports are lazy inside each handler, so
registering these handlers (at ``create_app``) never requires autogen. A missing extra surfaces
as the job's clear RuntimeError.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from ..db import repos
from ..jobs.runner import JobContext, JobHandler

if TYPE_CHECKING:  # annotation only — never imported at runtime
    from ..db.database import Database
    from .orchestrator import ExecuteFn
    from .production_tools import ProductionDeps

logger = logging.getLogger(__name__)


def handle_short_creator_run(
    ctx: JobContext, *, execute: ExecuteFn | None = None
) -> dict[str, Any]:
    """Payload ``{asset_id, topic, target_seconds}`` → the escalation-ladder result dict.

    ``execute`` is injectable for tests; production uses the default (real AutoGen team run).
    """
    payload = ctx.payload
    asset_id = str(payload["asset_id"])
    topic = str(payload["topic"])
    target_seconds = int(payload.get("target_seconds", 60))

    if repos.get_asset(ctx.db, asset_id) is None:
        return {"ok": False, "error": "asset not found", "asset_id": asset_id}

    from .orchestrator import run_short_creator  # lazy — pulls the optional autogen chain at run
    from .providers import resolve_from_env

    config = resolve_from_env()
    return run_short_creator(
        ctx.db,
        config,
        asset_id=asset_id,
        topic=topic,
        target_seconds=target_seconds,
        execute=execute,
    )


def _append_run_log_line(
    log_file: IO[str] | None, log_path: Path | None, event: dict[str, Any]
) -> None:
    """Append one NDJSON line to the session run log.

    Never raises (mirrors ``api/short_creator.py``'s streaming run-log ``write_line``): a write
    failure is logged and swallowed so it can never affect the job's result. A ``None`` log_file
    (the log could not be opened) is a silent no-op.
    """
    if log_file is None:
        return
    try:
        log_file.write(json.dumps(event, ensure_ascii=False) + "\n")
        log_file.flush()
    except OSError as exc:
        logger.warning("production.run session log write failed (%s): %s", log_path, exc)


def _open_run_log(
    db: Database, asset_id: str, session_id: str
) -> tuple[IO[str] | None, Path | None]:
    """Open a fresh NDJSON session-run-log file for *session_id*, or ``(None, None)`` on failure.

    The directory is ``board_root_for(...).parent / "runs"`` (``board_root_for`` returns
    ``.../board``), so a missing asset or project — ``board_root_for``'s own ``ValueError`` — is
    one more reason this can legitimately fail, same as a filesystem error opening the file.
    Either way this is logged, never raised: the coarse run log is purely for post-hoc debugging
    and must never be able to affect whether the job's actual result comes back.
    """
    from .production_orchestrator import board_root_for

    try:
        board_root = board_root_for(db, asset_id, session_id)
        log_dir = board_root.parent / "runs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        log_path = log_dir / f"{stamp}.ndjson"
        return log_path.open("a", encoding="utf-8"), log_path
    except Exception as exc:  # session log is best-effort — never block the run
        logger.warning("production.run session log unavailable (session=%s): %s", session_id, exc)
        return None, None


def handle_production_run(
    ctx: JobContext, *, execute: ExecuteFn | None = None, deps: ProductionDeps | None = None
) -> dict[str, Any]:
    """Payload ``{asset_id, session_id, task, target_seconds?, message?}`` → the v2 production
    run's result dict (:func:`production_orchestrator.run_production`), unchanged.

    Also writes a coarse two-line NDJSON session run log (a ``meta`` line before the run, a
    ``done`` line after) to ``<workspace>/agent-runs/<session_id>/runs/<UTC>Z.ndjson`` for
    post-hoc debugging. Any failure setting it up or writing to it is logged and swallowed, never
    raised — ``run_production``'s own result (which reports problems like a missing asset as an
    ``ok: False`` dict, never a raise) is always returned unchanged either way.
    """
    payload = ctx.payload
    asset_id = str(payload["asset_id"])
    session_id = str(payload["session_id"])
    task = str(payload["task"])
    target_seconds = int(payload.get("target_seconds", 60))
    # Whitespace-only messages must behave like no message (a stripped-blank follow-up is not a
    # real follow-up request).
    message = (payload.get("message") or "").strip() or None

    from .production_orchestrator import run_production
    from .providers import resolve_from_env

    config = resolve_from_env()

    log_file, log_path = _open_run_log(ctx.db, asset_id, session_id)
    meta_event: dict[str, Any] = {
        "type": "meta",
        "asset_id": asset_id,
        "session_id": session_id,
        "task": task,
    }
    if message is not None:
        meta_event["message"] = message
    _append_run_log_line(log_file, log_path, meta_event)

    result = run_production(
        ctx.db,
        config,
        asset_id=asset_id,
        session_id=session_id,
        task=task,
        target_seconds=target_seconds,
        message=message,
        execute=execute,
        deps=deps,
    )

    _append_run_log_line(
        log_file,
        log_path,
        {
            "type": "done",
            "ok": result.get("ok"),
            "stage": result.get("stage"),
            "weak": result.get("weak"),
            "escalated": result.get("escalated"),
            "export_id": result.get("export_id"),
            "resume_point": result.get("resume_point"),
        },
    )
    if log_file is not None:
        try:
            log_file.close()
        except OSError as exc:
            logger.warning("production.run session log close failed (%s): %s", log_path, exc)

    return result


def register_short_creator_handlers(registry: dict[str, JobHandler]) -> None:
    """Register the ``short_creator.run`` and ``production.run`` handlers on the job registry."""
    registry["short_creator.run"] = handle_short_creator_run
    registry["production.run"] = handle_production_run
