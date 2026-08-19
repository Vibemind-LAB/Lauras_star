"""Author-mode dispatch: outside callers (MCP endpoints) write creative artifacts through
the SAME tool closures the AutoGen team uses — one guard source, zero duplicated logic.

Only the three creative writes are author-callable. Everything downstream of the script
gate is deterministic and runs via approve, never through this module.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status

from ..db import repos
from ..db.database import Database
from ..util import utcnow_iso
from .board_models import BestWindow, SceneReview

if TYPE_CHECKING:
    from .board import Board

logger = logging.getLogger(__name__)

_AUTHOR_CALLABLE = frozenset({"propose_scene_selection", "save_storyline", "save_script_chapter"})
_TEAM_SESSION_DETAIL = "team session — this production is written by the in-app team chat"
_BUSY_DETAIL = "a production job is running on this session — wait for it to finish"


def call_production_tool(
    db: Database, session_id: str, tool_name: str, /, **kwargs: Any
) -> dict[str, Any]:
    if tool_name not in _AUTHOR_CALLABLE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown author tool: {tool_name}")
    session = repos.get_production_session(db, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    asset_id = str(session["asset_id"])

    from .board import Board
    from .production_orchestrator import board_root_for
    from .production_tools import build_production_tool_specs

    try:
        board = Board.open(board_root_for(db, asset_id, session_id))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session board not found") from exc

    if board.meta().author != "external":
        raise HTTPException(status.HTTP_409_CONFLICT, _TEAM_SESSION_DETAIL)
    if _job_busy(db, session):
        raise HTTPException(status.HTTP_409_CONFLICT, _BUSY_DETAIL)

    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}
    spec = specs.get(tool_name)
    if spec is None:  # defensive: the factory always builds these three
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"tool not available: {tool_name}")

    result = spec.func(**kwargs)
    if not isinstance(result, dict):  # tools return dicts by contract
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "tool returned no result")
    if result.get("ok") is False:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, _reason_of(result))
    return result


def _job_busy(db: Database, session: dict[str, Any]) -> bool:
    job_id = session.get("latest_job_id")
    if not job_id:
        return False
    job = repos.get_job(db, str(job_id))
    return job is not None and str(job["status"]) in ("queued", "running")


def _reason_of(result: dict[str, Any]) -> str:
    for key in ("reason", "error", "errors"):
        value = result.get(key)
        if value:
            return str(value)[:500]
    return "the tool rejected the write"


_DEFAULT_REVIEW_NOTE = "author-mode default review: full-scene window"
# Midline of SceneReview.hook_score's own 0..10 range — the same neutral value review_scene's
# own no-VLM ("degraded") construction uses (production_tools._DEFAULT_HOOK_SCORE), kept as a
# literal here rather than importing that private module constant across the package boundary.
_DEFAULT_HOOK_SCORE = 5


def materialize_default_scene_reviews(db: Database, board: Board, asset_id: str) -> list[int]:
    """Gate-S confirm on an author-mode board (C2+C3, 2026-08-19 final review): stamp a
    default, full-scene-window :class:`SceneReview` for every EXPECTED scene that does not
    already have one.

    An author-mode board never runs the team, so ``review_scene`` (production_tools.py) never
    runs either — without this, ``Board.resume_point`` would sit forever at
    ``scene_reviews:N``, ``save_storyline``'s "scenes without review" check would refuse every
    confirmed scene, and ``build_cutlist`` would have no window to cut. The default review
    mirrors ``review_scene``'s own no-VLM ("degraded") construction exactly (same
    ``_resolve_scene``/``_fps`` source-of-truth calls, same neutral hook_score, ``windows``
    left for :class:`SceneReview`'s own validator to default to ``[best_window]`` — the same
    "windows[0] must equal best_window" idiom every other reviewer path relies on) with ONE
    deliberate difference: the window spans the WHOLE scene rather than review_scene's short
    default — an author has not picked a highlight yet, so the honest default is "the whole
    thing", not a guess at one.

    Idempotent by construction: only scenes missing a review get one, so a re-confirm (the
    healing branch in :func:`laura.api.short_creator.confirm_scene_selection`) never
    duplicates or clobbers a review the team wrote earlier, or one this same helper already
    wrote on a prior call.
    """
    # _expected_scenes_for: the same helper confirm_scene_selection's own docstring points at —
    # imported locally from the api layer (chat/executor.py does the identical import for the
    # identical reason) rather than duplicated a fourth time in this package.
    from ..api.short_creator import _expected_scenes_for

    # _fps/_resolve_scene: private to production_tools.py, imported anyway (rather than
    # re-implemented, as production_orchestrator._expected_scene_numbers does for the much
    # smaller expected-scenes helper above) because _resolve_scene's real logic — rough-cut
    # clip mapping, scene source-range resolution, transcript lookup — is exactly what a
    # default review must be honest about, and duplicating it risks silently drifting from
    # what review_scene itself resolves.
    from .production_tools import _fps, _resolve_scene

    expected = _expected_scenes_for(db, asset_id)
    have = {r.scene_number for r in board.scene_reviews()}
    asset = repos.get_asset(db, asset_id)
    fps = _fps(db, asset) if asset is not None else 30.0
    created: list[int] = []
    for scene_number in expected:
        if scene_number in have:
            continue
        resolved = _resolve_scene(db, asset_id, scene_number)
        if resolved is None:
            # The rough cut and the expected-scenes read disagree (should not happen in
            # practice — both ultimately read the same rough cut) — skip rather than crash
            # a Gate-S confirm over an artifact this helper cannot honestly construct.
            continue
        src_start, src_end_exclusive, _text = resolved
        duration_s = max(0.01, (src_end_exclusive - src_start) / fps)
        review = SceneReview(
            scene_number=scene_number,
            src_start_frame=src_start,
            src_end_frame_exclusive=src_end_exclusive,
            description=_DEFAULT_REVIEW_NOTE,
            whats_happening=_DEFAULT_REVIEW_NOTE,
            hook_score=_DEFAULT_HOOK_SCORE,
            best_window=BestWindow(offset_s=0.0, duration_s=duration_s),
            degraded=True,
            created_utc=utcnow_iso(),
        )
        board.save_scene_review(review)
        created.append(scene_number)
    return created
