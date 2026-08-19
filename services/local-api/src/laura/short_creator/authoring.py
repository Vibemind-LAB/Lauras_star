"""Author-mode dispatch: outside callers (MCP endpoints) write creative artifacts through
the SAME tool closures the AutoGen team uses — one guard source, zero duplicated logic.

Only the three creative writes are author-callable. Everything downstream of the script
gate is deterministic and runs via approve, never through this module.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, status

from ..db import repos
from ..db.database import Database

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
