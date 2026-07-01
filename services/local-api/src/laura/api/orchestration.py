"""Auto-pilot orchestration endpoint (Axis 1, Slice C).

``POST /assets/{asset_id}/auto-pilot`` drives an asset toward a target by executing next_action
tools as far as possible WITHOUT blocking a worker: synchronous steps run inline; the next async
step (analysis/render) is enqueued and the call returns. Re-invoke after that job completes to
continue. ``target``: ``roughcut`` (stop before render) | ``render`` (drive to a finished export).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth import Principal, require_permission
from ..db import repos
from ..db.database import Database
from ..mcp import tool_next_action
from ..orchestration.autopilot import execute_tool, run_autopilot

router = APIRouter(tags=["orchestration"])

_TARGETS = ("roughcut", "render")


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


@router.post("/assets/{asset_id}/auto-pilot")
def auto_pilot(
    asset_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
    target: str = "roughcut",
) -> dict[str, Any]:
    """Drive *asset_id* toward *target* (``roughcut`` | ``render``).

    Advances through synchronous steps and enqueues the next async job, returning
    ``{status, steps, final_action}``. ``status`` is ``done`` | ``target_reached`` | ``blocked``
    | ``error`` | ``max_steps``. Poll the enqueued job (``/jobs/{id}``) and re-invoke to continue
    past an async step.
    """
    db = _db(request)
    if repos.get_asset(db, asset_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    if target not in _TARGETS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "target must be 'roughcut' or 'render'"
        )
    return run_autopilot(
        resolve=lambda: tool_next_action(db, asset_id),
        execute=lambda tool, args: execute_tool(db, tool, args),
        target=target,
    )
