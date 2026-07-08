"""Streaming escalation ladder (Live-Agent-Chat, Task 1).

The ladder is driven by an injectable async ``execute_stream``; tests script its
events + terminal outcome and collect the full normalized event stream via
``asyncio.run`` — no autogen, no LLM, no pytest-asyncio. The real
``_default_execute_stream`` (AutoGen ``run_stream`` mapping) is manual-to-verify.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from laura.db.database import Database
from laura.short_creator import providers, stream

Script = dict[tuple[str, str], "tuple[list[dict[str, Any]], tuple[str, bool]] | Exception"]


def _make_execute_stream(script: Script) -> stream.AsyncExecuteStream:
    async def execute_stream(
        db: Database, config: providers.AgentConfig, stage: str, kind: str, task: str
    ) -> AsyncIterator[dict[str, Any]]:
        spec = script[(stage, kind)]
        if isinstance(spec, Exception):
            raise spec
        events, (status, weak) = spec
        for event in events:
            yield event
        yield {
            "type": "_outcome",
            "status": status,
            "weak": weak,
            "team": kind,
            "stage": stage,
            "summary": "",
        }

    return execute_stream


def _collect(db: Database, script: Script, **env: str) -> list[dict[str, Any]]:
    execute_stream = _make_execute_stream(script)
    config = providers.resolve_from_env(env)

    async def run() -> list[dict[str, Any]]:
        return [
            e
            async for e in stream.run_short_creator_stream(
                db, config, asset_id="a", topic="cats", target_seconds=60,
                execute_stream=execute_stream,
            )
        ]

    return asyncio.run(run())


def _types(events: list[dict[str, Any]]) -> list[str]:
    return [str(e["type"]) for e in events]


def test_stage_a_ok_streams_stage_then_done(db: Database) -> None:
    events = _collect(
        db, {("A", "magentic"): ([{"type": "agent", "agent": "scout"}], ("ok", False))}
    )
    assert _types(events) == ["stage", "agent", "done"]
    assert events[0] == {"type": "stage", "stage": "A", "team": "magentic"}
    done = events[-1]
    assert done["ok"] is True and done["team"] == "magentic" and done["escalated"] is False
    # internal _outcome events are never forwarded to the client
    assert "_outcome" not in _types(events)


def test_magentic_hardfail_streams_graph_fallback(db: Database) -> None:
    events = _collect(
        db,
        {
            ("A", "magentic"): ([{"type": "agent", "agent": "m"}], ("hard_fail", False)),
            ("A", "graph"): ([{"type": "agent", "agent": "g"}], ("ok", False)),
        },
    )
    assert _types(events) == ["stage", "agent", "stage", "agent", "done"]
    assert events[2] == {"type": "stage", "stage": "A", "team": "graph"}
    assert events[-1]["team"] == "graph" and events[-1]["ok"] is True


def test_both_hardfail_escalates_to_stage_b(db: Database) -> None:
    events = _collect(
        db,
        {
            ("A", "magentic"): ([], ("hard_fail", False)),
            ("A", "graph"): ([], ("hard_fail", False)),
            ("B", "magentic"): ([], ("ok", False)),
        },
    )
    assert "escalated" in _types(events)
    stage_bs = [e for e in events if e["type"] == "stage" and e["stage"] == "B"]
    assert stage_bs and events[-1]["stage"] == "B" and events[-1]["escalated"] is True


def test_soft_weak_without_auto_stays_stage_a(db: Database) -> None:
    events = _collect(db, {("A", "magentic"): ([], ("ok", True))})
    assert "escalated" not in _types(events)
    assert events[-1]["stage"] == "A" and events[-1]["weak"] is True
    assert events[-1]["escalated"] is False


def test_soft_weak_with_auto_escalates(db: Database) -> None:
    events = _collect(
        db,
        {("A", "magentic"): ([], ("ok", True)), ("B", "magentic"): ([], ("ok", False))},
        LAURA_AGENT_AUTO_ESCALATE="1",
    )
    assert "escalated" in _types(events)
    assert events[-1]["stage"] == "B" and events[-1]["escalated"] is True


def test_orchestration_graph_forces_graph(db: Database) -> None:
    events = _collect(
        db,
        {("A", "graph"): ([{"type": "agent"}], ("ok", False))},
        LAURA_AGENT_ORCHESTRATION="graph",
    )
    assert events[0] == {"type": "stage", "stage": "A", "team": "graph"}
    assert events[-1]["team"] == "graph"


def test_exception_emits_error_event_then_falls_back(db: Database) -> None:
    events = _collect(
        db,
        {
            ("A", "magentic"): RuntimeError("boom"),
            ("A", "graph"): ([], ("ok", False)),
        },
    )
    assert "error" in _types(events)
    assert events[-1]["ok"] is True and events[-1]["team"] == "graph"


# --- _terminal_outcome: turn/message exhaustion is a hard fail, not success ------------------


def test_terminal_outcome_max_turns_is_hard_fail() -> None:
    out = stream._terminal_outcome(
        "Maximum number of turns 30 reached.",
        [("orchestrator", "planning"), ("orchestrator", "looping")],
        team="magentic",
        stage="A",
        artifacts=0,
    )
    assert out["status"] == "hard_fail"


def test_terminal_outcome_max_messages_is_hard_fail() -> None:
    out = stream._terminal_outcome(
        "Maximum number of messages 60 reached, current message count: 60",
        [],
        team="graph",
        stage="A",
        artifacts=0,
    )
    assert out["status"] == "hard_fail"


def test_terminal_outcome_weak_reads_only_qa_verdict() -> None:
    # The task echo contains the word "weak" — it must NOT trip the detector; only qa's
    # verdict counts.
    out = stream._terminal_outcome(
        "The task has been completed.",
        [("user", "say 'weak' if it does not match"), ("qa", "good — matches the topic")],
        team="magentic",
        stage="A",
        artifacts=1,
    )
    assert out["status"] == "ok"
    assert out["weak"] is False


def test_terminal_outcome_qa_weak_verdict_is_weak() -> None:
    out = stream._terminal_outcome(
        None, [("qa", "the short is WEAK and off-topic")], team="magentic", stage="A", artifacts=1
    )
    assert out["weak"] is True


def test_terminal_outcome_no_qa_verdict_is_weak() -> None:
    # qa never spoke (e.g. the orchestrator hallucinated completion) -> never validated -> weak.
    out = stream._terminal_outcome(
        None,
        [("orchestrator", "Great job! Here is your short: ...")],
        team="magentic",
        stage="A",
        artifacts=1,
    )
    assert out["status"] == "ok"
    assert out["weak"] is True


# --- _map_event: real tool names + tool results (duck-typed like autogen 0.4) ----------------


class _FunctionCall:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FunctionExecutionResult:
    def __init__(self, content: str, is_error: bool = False, name: str = "") -> None:
        self.content = content
        self.is_error = is_error
        self.name = name


class ToolCallRequestEvent:  # class NAME is what _map_event dispatches on
    def __init__(self, source: str, content: list[_FunctionCall]) -> None:
        self.source = source
        self.content = content


class ToolCallExecutionEvent:
    def __init__(self, source: str, content: list[_FunctionExecutionResult]) -> None:
        self.source = source
        self.content = content


def test_map_event_tool_call_uses_real_tool_name_and_args() -> None:
    raw = ToolCallRequestEvent(
        "scout", [_FunctionCall("search_visual_moments", '{"query": "ipod"}')]
    )
    mapped = stream._map_event(raw, "magentic")
    assert mapped == {
        "type": "tool_call",
        "agent": "scout",
        "tool": "search_visual_moments",
        "args": {"query": "ipod"},
    }


def test_map_event_tool_call_bad_args_fall_back_to_empty() -> None:
    raw = ToolCallRequestEvent("scout", [_FunctionCall("extract_shorts", "not-json")])
    mapped = stream._map_event(raw, "magentic")
    assert mapped is not None
    assert mapped["tool"] == "extract_shorts"
    assert mapped["args"] == {}


def test_map_event_tool_execution_becomes_tool_result() -> None:
    raw = ToolCallExecutionEvent(
        "scout",
        [_FunctionExecutionResult("{'ok': True, 'count': 512}", name="list_short_candidates")],
    )
    mapped = stream._map_event(raw, "magentic")
    assert mapped is not None
    assert mapped["type"] == "tool_result"
    assert mapped["tool"] == "list_short_candidates"
    assert mapped["ok"] is True
    assert "512" in mapped["summary"]


def test_map_event_tool_execution_error_flags_not_ok() -> None:
    raw = ToolCallExecutionEvent("scout", [_FunctionExecutionResult("boom", is_error=True)])
    mapped = stream._map_event(raw, "magentic")
    assert mapped is not None
    assert mapped["type"] == "tool_result"
    assert mapped["ok"] is False


# --- artifact derivation + artifact-grounded outcome -----------------------------------------


def test_artifact_events_derived_from_tool_result_summaries() -> None:
    assert stream._artifact_events(
        "{'ok': True, 'timeline_id': 'abc123', 'scene_count': 4}"
    ) == [{"type": "artifact", "kind": "timeline", "id": "abc123"}]
    assert stream._artifact_events("{'export_id': 'e9', 'job_id': 'j1'}") == [
        {"type": "artifact", "kind": "render", "id": "e9"}
    ]
    assert stream._artifact_events("{'ok': False, 'reason': 'nope'}") == []


def test_terminal_outcome_without_artifact_is_weak_even_if_qa_approves() -> None:
    # qa said "good" but the editor never produced a timeline/render — nothing exists, so the
    # run must read weak (observed live: a 7B team talked through the task without acting).
    out = stream._terminal_outcome(
        None, [("qa", "good — matches the topic")], team="graph", stage="A", artifacts=0
    )
    assert out["weak"] is True


def test_terminal_outcome_with_artifact_and_qa_ok_is_not_weak() -> None:
    out = stream._terminal_outcome(
        None, [("qa", "good — matches the topic")], team="graph", stage="A", artifacts=2
    )
    assert out["weak"] is False
