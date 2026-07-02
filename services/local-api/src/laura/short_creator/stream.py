"""Streaming variant of the escalation ladder for the Live-Agent-Chat.

``run_short_creator_stream`` drives the ladder as an async event stream: Stage A (magentic → graph
on hard-fail) then, if too bad, escalate to Stage B — yielding normalized events (``stage`` /
``agent`` / ``tool_call`` / ``tool_result`` / ``artifact`` / ``escalated`` / ``done`` / ``error``).
Team execution is injectable (``execute_stream``, an async generator) so the ladder is tested
without an LLM; the default executor drives the real AutoGen ``team.run_stream`` (manual-to-verify).

Internal ``_outcome`` events carry a team's status/weak between the ladder helpers and are NEVER
forwarded to the client.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

from ..db.database import Database
from . import graph, magentic
from .orchestrator import TeamKind, _task_prompt
from .providers import AgentConfig, Stage

logger = logging.getLogger(__name__)

_OUTCOME = "_outcome"

AsyncExecuteStream = Callable[
    [Database, AgentConfig, Stage, TeamKind, str], AsyncIterator[dict[str, Any]]
]


def _outcome_event(status: str, weak: bool, *, team: TeamKind, stage: Stage, summary: str = "") -> (
    dict[str, Any]
):
    return {
        "type": _OUTCOME,
        "status": status,
        "weak": weak,
        "team": team,
        "stage": stage,
        "summary": summary,
    }


def _done(outcome: dict[str, Any], *, escalated: bool) -> dict[str, Any]:
    return {
        "type": "done",
        "ok": outcome["status"] == "ok",
        "stage": outcome["stage"],
        "team": outcome["team"],
        "weak": outcome["weak"],
        "escalated": escalated,
        "summary": outcome.get("summary", ""),
    }


async def _safe_stream(
    run: AsyncExecuteStream,
    db: Database,
    config: AgentConfig,
    stage: Stage,
    kind: TeamKind,
    task: str,
) -> AsyncIterator[dict[str, Any]]:
    """Run one team's event stream; convert any exception into an ``error`` + hard-fail outcome."""
    try:
        async for event in run(db, config, stage, kind, task):
            yield event
    except Exception as exc:
        logger.warning("%s stream raised at stage %s: %s", kind, stage, exc)
        yield {"type": "error", "message": str(exc)}
        yield _outcome_event("hard_fail", False, team=kind, stage=stage, summary=str(exc))


async def _run_stage_stream(
    db: Database, config: AgentConfig, stage: Stage, task: str, run: AsyncExecuteStream
) -> AsyncIterator[dict[str, Any]]:
    """Magentic-One for this stage, falling back to GraphFlow on a hard failure.

    Emits a ``stage`` event per team run and forwards exactly one terminal ``_outcome`` (the
    stage's final one).
    """
    if config.orchestration == "graph":
        yield {"type": "stage", "stage": stage, "team": "graph"}
        async for event in _safe_stream(run, db, config, stage, "graph", task):
            yield event
        return

    yield {"type": "stage", "stage": stage, "team": "magentic"}
    magentic_outcome: dict[str, Any] | None = None
    async for event in _safe_stream(run, db, config, stage, "magentic", task):
        if event.get("type") == _OUTCOME:
            magentic_outcome = event
            if event["status"] != "hard_fail":
                yield event  # magentic succeeded → its outcome is the stage's
        else:
            yield event
    if magentic_outcome is not None and magentic_outcome["status"] == "hard_fail":
        yield {"type": "stage", "stage": stage, "team": "graph"}
        async for event in _safe_stream(run, db, config, stage, "graph", task):
            yield event


async def run_short_creator_stream(
    db: Database,
    config: AgentConfig,
    *,
    asset_id: str,
    topic: str,
    target_seconds: int = 60,
    execute_stream: AsyncExecuteStream | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream the escalation ladder as normalized events (see the design spec's event model)."""
    run = execute_stream if execute_stream is not None else _default_execute_stream
    task = _task_prompt(asset_id, topic, target_seconds)

    a_outcome = _outcome_event("hard_fail", False, team="magentic", stage="A")
    async for event in _run_stage_stream(db, config, "A", task, run):
        if event.get("type") == _OUTCOME:
            a_outcome = event
        else:
            yield event

    if a_outcome["status"] == "ok" and not a_outcome["weak"]:
        yield _done(a_outcome, escalated=False)
        return
    if a_outcome["status"] == "hard_fail" or (config.auto_escalate and a_outcome["weak"]):
        yield {"type": "escalated", "to": config.escalate_provider}
        b_outcome = _outcome_event("hard_fail", False, team="magentic", stage="B")
        async for event in _run_stage_stream(db, config, "B", task, run):
            if event.get("type") == _OUTCOME:
                b_outcome = event
            else:
                yield event
        yield _done(b_outcome, escalated=True)
        return
    yield _done(a_outcome, escalated=False)  # soft-weak: manual escalation left to the user


def _tool_call_event(source: Any, calls: list[Any]) -> dict[str, Any]:
    """A ``tool_call`` event from an AutoGen ToolCallRequestEvent's FunctionCall list."""
    import json as _json

    names = [str(getattr(c, "name", "") or "") for c in calls]
    args: dict[str, Any] = {}
    raw_args = getattr(calls[0], "arguments", None) if calls else None
    if isinstance(raw_args, str):
        try:
            parsed = _json.loads(raw_args)
            if isinstance(parsed, dict):
                args = parsed
        except ValueError:
            pass
    return {
        "type": "tool_call",
        "agent": str(source or ""),
        "tool": "+".join(n for n in names if n) or "tool",
        "args": args,
    }


def _tool_result_event(results: list[Any]) -> dict[str, Any]:
    """A ``tool_result`` event from an AutoGen ToolCallExecutionEvent's result list."""
    first = results[0] if results else None
    summary = str(getattr(first, "content", "") or "")[:200]
    ok = not any(bool(getattr(r, "is_error", False)) for r in results)
    return {
        "type": "tool_result",
        "tool": str(getattr(first, "name", "") or "") or "tool",
        "ok": ok,
        "summary": summary,
    }


def _map_event(raw: Any, kind: TeamKind) -> dict[str, Any] | None:
    """Best-effort map of an AutoGen ``run_stream`` message to a normalized event.

    Recognizes agent text messages and tool call/execution events; the final ``TaskResult`` is
    handled by the caller (its ``stop_reason`` decides the outcome). Unknown raw events are
    dropped (never passed through raw). Duck-typed on class name + attributes.
    """
    name = type(raw).__name__
    if name == "TaskResult":
        return None
    source = getattr(raw, "source", None)
    content = getattr(raw, "content", None)
    if isinstance(content, str) and source is not None:
        return {"type": "agent", "agent": str(source), "text": content}
    if name == "ToolCallRequestEvent" and isinstance(content, list):
        return _tool_call_event(source, content)
    if name == "ToolCallExecutionEvent" and isinstance(content, list):
        return _tool_result_event(content)
    if "ToolCall" in name:
        return {"type": "tool_call", "agent": str(source or ""), "tool": name, "args": {}}
    return None


def _terminal_outcome(
    stop_reason: str | None, texts: list[str], *, team: TeamKind, stage: Stage
) -> dict[str, Any]:
    """The stage outcome from a finished team run.

    A run that ended because it exhausted its turn/message budget produced nothing useful —
    that is a hard fail (so the ladder falls back / escalates), NOT a success. ``weak`` reads
    the QA gate's verdict from the transcript.
    """
    joined = " ".join(texts)
    exhausted = "maximum number of" in (stop_reason or "").lower()
    return _outcome_event(
        "hard_fail" if exhausted else "ok",
        "weak" in joined.lower(),
        team=team,
        stage=stage,
        summary=(joined or (stop_reason or ""))[:2000],
    )


async def _default_execute_stream(
    db: Database, config: AgentConfig, stage: Stage, kind: TeamKind, task: str
) -> AsyncIterator[dict[str, Any]]:
    """Drive the real AutoGen ``team.run_stream`` for *kind*, mapping raw events → normalized ones
    plus a terminal ``_outcome``. Manual-to-verify (no model in CI; tests inject a fake)."""
    team = (
        magentic.build_magentic_team(db, config, stage=stage)
        if kind == "magentic"
        else graph.build_graph_team(db, config, stage=stage)
    )
    texts: list[str] = []
    stop_reason: str | None = None
    async for raw in team.run_stream(task=task):
        if type(raw).__name__ == "TaskResult":
            reason = getattr(raw, "stop_reason", None)
            stop_reason = str(reason) if reason is not None else None
            continue
        mapped = _map_event(raw, kind)
        if mapped is None:
            continue
        if mapped.get("type") == "agent":
            texts.append(str(mapped.get("text", "")))
        yield mapped
    yield _terminal_outcome(stop_reason, texts, team=kind, stage=stage)
