"""Deterministic post-gate production tail (spec 2026-08-05-modular-production-design.md).

After the user approves the script (Gate B), nothing creative remains: voice, cutlist,
contact sheet and render are plain tool calls. This module runs them WITHOUT the agent
team, so an approved script can never be rewritten by a resumed run. Skip semantics come
from ``Board.resume_point``: the chain starts at the first missing artifact and each
successful step must advance it (a step that reports ok but does not advance ends the run
as a failure — never a spin). Each step gets exactly one retry (spec decision 3), then the
run fails honestly with the step name. QA is a separate, bounded agent stage (MP2) — this
module never holds a write-capable creative tool.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .toolset import ToolSpec

if TYPE_CHECKING:  # annotation only (from __future__ import annotations defers evaluation) —
    # avoids a real import-time cycle between this module and .orchestrator.
    from .orchestrator import ExecuteFn, StageOutcome

logger = logging.getLogger(__name__)

# resume_point -> tool that produces that artifact. Order IS the chain order; the exact
# tuple is pinned by test_tail_tool_menu_is_pinned_exactly (structural no-rewrite
# guarantee: nothing outside these four names is ever callable from the tail).
_STEP_BY_RESUME_POINT: dict[str, str] = {
    "voice": "synthesize_script_voice",
    "cutlist": "build_cutlist",
    "contact_sheet": "save_contact_sheet",
    "render_report": "render_production",
}

# Defensive cap on the outer loop (fix round, MP1 review). The per-step no-progress
# guard below only compares against the IMMEDIATELY prior resume point, so a
# multi-step oscillation (voice -> cutlist -> voice -> ...) — possible under
# concurrent/downstream invalidation on a live board — would slip past it and spin
# forever. Two full passes over the chain is generous headroom for legitimate runs
# (which visit each step at most once) while still catching a cycle promptly.
_MAX_TAIL_ITERATIONS = 2 * len(_STEP_BY_RESUME_POINT)


@dataclass
class TailOutcome:
    """How the deterministic chain ended (QA not included — MP2 reports separately)."""

    ok: bool
    failed_step: str | None
    reason: str | None
    summary: str


def _emit(sink: Callable[[dict[str, Any]], None] | None, event: dict[str, Any]) -> None:
    if sink is None:
        return
    try:
        sink(event)
    except Exception:  # noqa: BLE001 — observability must never break the run
        logger.warning("deterministic tail event sink failed", exc_info=True)


def run_deterministic_tail(
    board: Any,
    specs: list[ToolSpec],
    *,
    expected_scenes: list[int],
    event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> TailOutcome:
    """Run the post-approval chain from the board's current resume point to the render.

    ``specs`` is the FULL production ToolSpec list (the caller builds it once via
    ``build_production_tool_specs``); only the four chain tools are ever looked up. The
    board/spec split keeps this function pure enough to test with fakes.
    """
    funcs = {s.name: s.func for s in specs if s.name in _STEP_BY_RESUME_POINT.values()}
    done: list[str] = []

    for _ in range(_MAX_TAIL_ITERATIONS):
        point = board.resume_point(expected_scenes)
        tool_name = _STEP_BY_RESUME_POINT.get(point)
        if tool_name is None:
            # Past the chain (qa_report/done) or before it (creative work missing —
            # the eligibility predicate should have prevented that; stop either way).
            return TailOutcome(True, None, None, _summary(done))
        func = funcs.get(tool_name)
        if func is None:
            return TailOutcome(False, tool_name, "tool not available", _summary(done))

        result = _call_with_retry(func, tool_name, event_sink)
        if not result.get("ok", False):
            reason = str(result.get("reason", "tool failed"))[:300]
            return TailOutcome(False, tool_name, reason, _summary(done))

        if board.resume_point(expected_scenes) == point:
            return TailOutcome(
                False, tool_name, "no progress after ok result", _summary(done)
            )
        done.append(tool_name)

    # Cap exceeded: the resume point kept oscillating among the chain steps without
    # ever landing past them. Report the step current at the moment of the cap so the
    # failure is actionable, not just "gave up".
    point = board.resume_point(expected_scenes)
    tool_name = _STEP_BY_RESUME_POINT.get(point) or (done[-1] if done else "unknown")
    return TailOutcome(False, tool_name, "resume point cycled", _summary(done))


def _call_with_retry(
    func: Callable[..., dict[str, Any]],
    tool_name: str,
    event_sink: Callable[[dict[str, Any]], None] | None,
) -> dict[str, Any]:
    """One call plus exactly one retry (spec decision 3). Exceptions count as failures
    (the tools' own contract is to return ``{"ok": False}`` instead of raising, but the
    tail must survive a raising seam too)."""
    result: dict[str, Any] = {"ok": False, "reason": "not called"}
    for attempt in (1, 2):
        _emit(event_sink, {
            "type": "tool_call", "agent": "pipeline", "tool": tool_name,
            "args": {"attempt": attempt},
        })
        try:
            result = func()
        except Exception as exc:  # noqa: BLE001 — a raising tool is a failed attempt
            result = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"[:300]}
        _emit(event_sink, {
            "type": "tool_result", "tool": tool_name,
            "ok": bool(result.get("ok", False)),
            "summary": str(result)[:300],
        })
        if result.get("ok", False):
            return result
    return result


def _summary(done: list[str]) -> str:
    return "deterministic tail: " + (", ".join(done) if done else "nothing to do")


_QA_TASK = (
    "QA only. The creative chain is complete and the script is user-approved: judge the "
    "RENDERED export on the board (board_status -> get_storyline/get_script -> "
    "review_export) and save your verdict via save_qa_report. Report findings; never "
    "request script or storyline rewrites — the deterministic pipeline owns the chain."
)


def run_tail_with_qa(
    db: Any,
    board: Any,
    config: Any,
    *,
    asset_id: str,
    deps: Any,
    event_sink: Callable[[dict[str, Any]], None] | None,
    expected_scenes: list[int],
    specs: list[ToolSpec] | None = None,
    qa_execute: ExecuteFn | None = None,
) -> tuple[TailOutcome, StageOutcome | None]:
    """Chain first, bounded QA second. QA runs only after a fully successful chain, and only
    once: a single ``_safe_execute`` call, no retry — an exception inside the QA execute becomes
    a clean ``hard_fail`` ``StageOutcome`` (``_safe_execute``'s own contract) rather than
    propagating, but a ``hard_fail`` QA outcome is reported to the caller as-is, never retried
    into a loop. A failed chain never reaches QA at all: the second element of the return tuple
    is ``None`` in that case.

    ``db``/``board``/``config``/``deps`` stay ``Any`` (not ``Database``/``Board``/``AgentConfig``/
    ``ProductionDeps``): this is a test seam as much as a real entrypoint — the pipeline tests
    drive it with ``None`` for everything the injected ``specs``/``qa_execute`` make unnecessary,
    and a stricter annotation would fail mypy on those calls. ``qa_execute`` and the return type
    stay precisely typed (``ExecuteFn | None`` / ``tuple[TailOutcome, StageOutcome | None]``)
    since MP3 consumes this function's contract directly.
    """
    if specs is None:
        from .production_tools import build_production_tool_specs

        specs = build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)

    tail = run_deterministic_tail(
        board, specs, expected_scenes=expected_scenes, event_sink=event_sink
    )
    if not tail.ok:
        return tail, None

    from .orchestrator import _safe_execute

    execute = qa_execute if qa_execute is not None else _make_qa_execute(
        board, asset_id, deps, event_sink
    )
    qa_outcome = _safe_execute(execute, db, config, "A", "magentic", _QA_TASK)
    return tail, qa_outcome


def _make_qa_execute(
    board: Any,
    asset_id: str,
    deps: Any,
    event_sink: Callable[[dict[str, Any]], None] | None,
) -> ExecuteFn:
    """The real QA ``ExecuteFn``: a one-agent team (``qa_reviewer`` only). Mirrors
    :func:`production_orchestrator._make_default_execute`, narrowed via ``agent_names``."""
    from .production_orchestrator import _make_default_execute

    return _make_default_execute(
        board, asset_id, deps, event_sink, agent_names=("qa_reviewer",)
    )
