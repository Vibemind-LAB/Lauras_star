"""Auto-pilot orchestration core (Axis 1, Slice C) — pure loop logic.

``run_autopilot`` drives an asset toward a target by repeatedly resolving ``next_action`` and
executing the suggested tool via INJECTED ``resolve`` / ``execute`` callables — no DB, jobs, or
sleeps here. Target gating: ``roughcut`` stops before the render step; ``render`` drives to done.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from laura.orchestration.autopilot import plan_step, run_autopilot


def _action(
    tool: str | None, args: dict[str, Any] | None = None, blocked_by: list[str] | None = None
) -> dict[str, Any]:
    return {"tool": tool, "args": args or {}, "blocked_by": blocked_by or []}


def _seq_resolver(states: list[dict[str, Any]]) -> Callable[[], dict[str, Any]]:
    """A resolve() that yields *states* in order, then repeats the last (state advances)."""
    box = {"i": 0}

    def resolve() -> dict[str, Any]:
        i = min(box["i"], len(states) - 1)
        box["i"] += 1
        return states[i]

    return resolve


def test_plan_step_stops_at_render_for_roughcut_target() -> None:
    assert plan_step(_action("render_reel"), "roughcut") is None
    assert plan_step(_action("render_reel"), "render") == "render_reel"


def test_plan_step_runs_pipeline_tools() -> None:
    assert plan_step(_action("analysis_run"), "roughcut") == "analysis_run"
    assert plan_step(_action("roughcut_from_shots"), "roughcut") == "roughcut_from_shots"


def test_plan_step_stops_when_no_tool() -> None:
    assert plan_step(_action(None), "render") is None


def test_run_autopilot_roughcut_stops_before_render() -> None:
    states = [
        _action("analysis_run", {"asset_id": "a"}),
        _action("roughcut_from_shots", {"asset_id": "a"}),
        _action("render_reel", {"timeline_id": "t"}),
    ]
    executed: list[str] = []

    def execute(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        executed.append(tool)
        return {"ok": True}

    out = run_autopilot(resolve=_seq_resolver(states), execute=execute, target="roughcut")
    assert out["status"] == "target_reached"
    assert executed == ["analysis_run", "roughcut_from_shots"]
    assert len(out["steps"]) == 2


def test_run_autopilot_render_drives_to_done() -> None:
    states = [
        _action("analysis_run", {"asset_id": "a"}),
        _action("roughcut_from_shots", {"asset_id": "a"}),
        _action("render_reel", {"timeline_id": "t"}),
        _action(None),  # done
    ]
    executed: list[str] = []

    def execute(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        executed.append(tool)
        return {"ok": True}

    out = run_autopilot(resolve=_seq_resolver(states), execute=execute, target="render")
    assert out["status"] == "done"
    assert executed == ["analysis_run", "roughcut_from_shots", "render_reel"]


def test_run_autopilot_reports_blocked() -> None:
    out = run_autopilot(
        resolve=lambda: _action(None, blocked_by=["PROXY_PENDING"]),
        execute=lambda t, a: {"ok": True},
        target="roughcut",
    )
    assert out["status"] == "blocked"
    assert out["steps"] == []


def test_run_autopilot_stops_on_execute_error() -> None:
    out = run_autopilot(
        resolve=lambda: _action("analysis_run", {"asset_id": "a"}),
        execute=lambda t, a: {"ok": False, "error": "boom"},
        target="render",
        max_steps=5,
    )
    assert out["status"] == "error"
    assert len(out["steps"]) == 1


def test_run_autopilot_bounded_by_max_steps() -> None:
    # resolve never advances past a tool → the loop must stop at max_steps, not spin forever.
    out = run_autopilot(
        resolve=lambda: _action("analysis_run", {"asset_id": "a"}),
        execute=lambda t, a: {"ok": True},
        target="render",
        max_steps=3,
    )
    assert out["status"] == "max_steps"
    assert len(out["steps"]) == 3
