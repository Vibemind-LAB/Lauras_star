"""Auto-pilot orchestration (Axis 1, Slice C) — drive an asset through the pipeline.

The pure core (``plan_step`` / ``run_autopilot``) decides and sequences steps from the
``next_action`` state machine, with ``resolve`` (re-query current state) and ``execute`` (run one
tool) injected so it is testable without a DB, jobs, or sleeps. Target gating: ``roughcut`` stops
before the render step; ``render`` drives through to a finished export.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..db.database import Database

# The next_action tool that renders — the boundary the "roughcut" target stops before.
_RENDER_TOOL = "render_reel"

ResolveFn = Callable[[], dict[str, Any]]
ExecuteFn = Callable[[str, dict[str, Any]], dict[str, Any]]


def plan_step(action: dict[str, Any], target: str) -> str | None:
    """The next_action tool to execute next, or None to stop.

    Stops when next_action is blocked or done (``tool`` is None), or when the suggested tool would
    go past *target* (``roughcut`` stops before the render step)."""
    tool = action.get("tool")
    if tool is None:
        return None
    if target == "roughcut" and tool == _RENDER_TOOL:
        return None
    return str(tool)


def run_autopilot(
    *,
    resolve: ResolveFn,
    execute: ExecuteFn,
    target: str = "roughcut",
    max_steps: int = 12,
) -> dict[str, Any]:
    """Drive an asset toward *target* by resolving next_action and executing its tool in a loop.

    ``resolve()`` returns the CURRENT next_action (re-queried each iteration — ``execute`` is
    expected to advance state, e.g. run a job to completion). ``execute(tool, args)`` runs one
    tool and returns its result dict (with an ``ok`` flag). The loop stops at the target, when
    blocked/done, on an execute error, or after ``max_steps`` (a safety bound against a state that
    never advances).

    Returns ``{"status": ..., "steps": [...], "final_action": {...}}`` where status is one of
    ``done`` | ``target_reached`` | ``blocked`` | ``error`` | ``max_steps``.
    """
    steps: list[dict[str, Any]] = []
    action = resolve()
    for _ in range(max_steps):
        tool = plan_step(action, target)
        if tool is None:
            if action.get("blocked_by"):
                status = "blocked"
            elif action.get("tool") is not None:
                status = "target_reached"
            else:
                status = "done"
            return {"status": status, "steps": steps, "final_action": action}
        result = execute(tool, dict(action.get("args", {})))
        steps.append({"tool": tool, "ok": bool(result.get("ok", True)), "result": result})
        if not result.get("ok", True):
            return {"status": "error", "steps": steps, "final_action": action}
        action = resolve()
    return {"status": "max_steps", "steps": steps, "final_action": action}


def execute_tool(db: Database, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one ``next_action`` tool to its executable MCP tool — the real ``execute`` for
    :func:`run_autopilot`. Synchronous tools (build_roughcut) run inline; async tools
    (analysis, render) enqueue a job and return. After an async enqueue the next ``resolve`` sees
    a *running* state (``tool`` None), so the loop stops without blocking a worker."""
    from ..mcp import tool_build_roughcut, tool_render_timeline, tool_start_analysis

    if tool == "analysis_run":
        return tool_start_analysis(db, str(args["asset_id"]))
    if tool == "roughcut_from_shots":
        return tool_build_roughcut(db, str(args["asset_id"]))
    if tool == _RENDER_TOOL:
        return tool_render_timeline(db, str(args["timeline_id"]))
    return {"ok": False, "error": f"unknown autopilot tool: {tool}"}
