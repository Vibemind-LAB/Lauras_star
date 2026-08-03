"""Chat router: one turn, one tool call, never a crash (spec 2026-08-03).

Same seam design as scout.run_scout: runner is injectable, a bad reply gets exactly one
retry with the validation error appended, and a runner exception goes straight to the
deterministic fallback (a reply asking to rephrase) — the thread must never 500 on a turn.
"""
from __future__ import annotations

import json

from laura.chat.router import TOOLS, compose_context, run_router
from laura.short_creator.providers import AgentConfig


def _config() -> AgentConfig:
    return AgentConfig(
        provider="openai-compat",
        agent_model="test",
        orchestrator_model="test",
        orchestration="magentic",
        escalate_provider="openai-compat",
        escalate_model="test",
        auto_escalate=False,
        qa_max_rounds=2,
        nine_router_base_url="http://localhost:20128/v1",
        nine_router_api_key=None,
        openai_base_url=None,
        openai_api_key="k",
    )


def test_valid_tool_call_passes_through() -> None:
    reply = json.dumps({"tool": "start_short", "args": {"topic": "Automatisierung"}})
    decision = run_router(
        _config(), context="", user_text="bau mir was", runner=lambda _t: reply
    )
    assert decision == {
        "tool": "start_short",
        "args": {"topic": "Automatisierung"},
        "fallback": False,
    }


def test_unknown_tool_gets_one_retry_then_fallback() -> None:
    calls: list[str] = []

    def runner(task: str) -> str:
        calls.append(task)
        return json.dumps({"tool": "explode", "args": {}})

    decision = run_router(_config(), context="", user_text="x", runner=runner)
    assert len(calls) == 2, "exactly one retry"
    assert "explode" in calls[1], "the retry names the validation error"
    assert decision["tool"] == "reply" and decision["fallback"] is True


def test_missing_required_arg_is_a_validation_error() -> None:
    replies = iter([
        json.dumps({"tool": "propose_import", "args": {"urls": []}}),
        json.dumps({"tool": "propose_import", "args": {"urls": ["https://x/a.mp4"]}}),
    ])
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: next(replies))
    assert decision["tool"] == "propose_import" and decision["fallback"] is False


def test_runner_exception_goes_straight_to_fallback() -> None:
    calls: list[str] = []

    def runner(task: str) -> str:
        calls.append(task)
        raise TimeoutError("model down")

    decision = run_router(_config(), context="", user_text="x", runner=runner)
    assert len(calls) == 1, "exception path never retries"
    assert decision["tool"] == "reply" and decision["fallback"] is True
    assert decision["args"]["text"], "the fallback reply carries readable text"


def test_compose_context_compacts_cards_and_caps_at_20() -> None:
    messages = [
        {"role": "user", "kind": "text", "content": {"text": f"m{i}"}} for i in range(25)
    ] + [
        {"role": "assistant", "kind": "approval_request",
         "content": {"action_type": "import_urls", "status": "pending",
                     "payload": {"urls": ["https://x/a"]}}},
        {"role": "assistant", "kind": "action",
         "content": {"tool": "start_short", "outcome": "done",
                     "refs": {"session_id": "s9"}, "args": {}}},
    ]
    ctx = compose_context(project={"name": "Drive-Test", "id": "p1"}, running_jobs=1,
                          messages=messages)
    assert "m4" not in ctx and "m24" in ctx, "only the last 20 messages"
    assert "import_urls" in ctx and "pending" in ctx
    assert "s9" in ctx, "action refs survive compaction (follow_up needs them)"
    assert "Drive-Test" in ctx


def test_every_tool_is_reachable() -> None:
    assert frozenset({
        "reply", "create_project", "switch_project", "propose_import",
        "start_short", "start_overview", "follow_up", "revert",
    }) == TOOLS
