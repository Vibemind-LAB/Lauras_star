"""A production run must leave a trace of WHAT IT DID, not only that it started and ended.

Live finding (runs 4a8624e2, be23992c): the session run log holds exactly two lines — meta
and done. Run I spent 44 minutes in the script phase and saved nothing; the only forensic
evidence was pydantic serializer noise in the backend log hinting at "two failed attempts" in
the orchestrator's ledger. Which tool was called, what it refused, why the author never got a
chapter through — unknowable. Every stalled phase so far was diagnosed by archaeology on the
board's version files, and this one left no versions to dig through.

The stream path (auto-short) already normalizes AutoGen events to ndjson lines. The
production path now does the same: the team runs via ``run_stream`` and every normalized
event goes to an injectable sink, which the job handler points at the session run log.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from laura.short_creator import production_orchestrator as po
from laura.short_creator.board import Board
from laura.short_creator.board_models import BoardMeta
from laura.short_creator.providers import AgentConfig


def _config() -> AgentConfig:
    return AgentConfig(
        provider="ollama",
        agent_model="m",
        orchestrator_model="m",
        orchestration="magentic",
        escalate_provider="ollama",
        escalate_model="m",
        auto_escalate=False,
        qa_max_rounds=2,
        nine_router_base_url="http://localhost:20128/v1",
        nine_router_api_key=None,
        openai_base_url=None,
        openai_api_key=None,
    )


class _Msg:
    def __init__(self, source: str, content: str) -> None:
        self.source = source
        self.content = content


class TaskResult:
    """Duck-typed like autogen's TaskResult: the mapper drops it, _parse_outcome reads it."""

    def __init__(self, messages: list[Any], stop_reason: str = "done") -> None:
        self.messages = messages
        self.stop_reason = stop_reason


class _FakeTeam:
    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def run_stream(self, *, task: str) -> Any:
        for event in self._events:
            yield event


def _board(tmp_path: Path) -> Board:
    return Board.create(
        tmp_path / "board",
        BoardMeta(
            session_id="s1",
            asset_id="a1",
            created_utc="2026-07-19T12:00:00+00:00",
            task="demo",
            target_seconds=174.0,
        ),
    )


def test_every_team_event_reaches_the_sink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 44-minute black box: with a sink, each agent turn leaves a line."""
    events: list[Any] = [
        _Msg("scene_author", "writing chapter 1 now"),
        _Msg("qa_reviewer", "looks short"),
        TaskResult([_Msg("scene_author", "summary text")]),
    ]
    monkeypatch.setattr(po, "build_production_team", lambda *a, **k: _FakeTeam(events))
    seen: list[dict[str, Any]] = []
    execute = po._make_default_execute(_board(tmp_path), "a1", None, seen.append)

    outcome = execute(None, _config(), "A", "magentic", "the task")  # type: ignore[arg-type]

    assert outcome.status == "ok"
    assert outcome.summary == "summary text"
    assert [e["type"] for e in seen] == ["agent", "agent"]
    assert seen[0] == {"type": "agent", "agent": "scene_author", "text": "writing chapter 1 now"}


def test_without_a_sink_the_stream_still_produces_the_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sink is observability, never a dependency."""
    events: list[Any] = [_Msg("a", "t"), TaskResult([_Msg("a", "final")])]
    monkeypatch.setattr(po, "build_production_team", lambda *a, **k: _FakeTeam(events))
    execute = po._make_default_execute(_board(tmp_path), "a1", None, None)

    outcome = execute(None, _config(), "A", "magentic", "the task")  # type: ignore[arg-type]

    assert outcome.status == "ok"
    assert outcome.summary == "final"


def test_stream_turn_exhaustion_is_not_reported_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[Any] = [
        _Msg("story_architect", "Paused."),
        TaskResult(
            [_Msg("story_architect", "Paused.")],
            stop_reason="Max rounds reached.",
        ),
    ]
    monkeypatch.setattr(po, "build_production_team", lambda *a, **k: _FakeTeam(events))
    execute = po._make_default_execute(_board(tmp_path), "a1", None, None)

    outcome = execute(None, _config(), "A", "magentic", "the task")  # type: ignore[arg-type]

    assert outcome.status == "hard_fail"
    assert outcome.summary == "Max rounds reached."


def test_a_crashing_sink_does_not_kill_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Logging must never be the reason a film fails."""
    events: list[Any] = [_Msg("a", "t"), TaskResult([_Msg("a", "final")])]
    monkeypatch.setattr(po, "build_production_team", lambda *a, **k: _FakeTeam(events))

    def broken_sink(_event: dict[str, Any]) -> None:
        raise OSError("disk full")

    execute = po._make_default_execute(_board(tmp_path), "a1", None, broken_sink)

    outcome = execute(None, _config(), "A", "magentic", "the task")  # type: ignore[arg-type]

    assert outcome.status == "ok"


def test_a_team_exception_still_hard_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stream rewrite must not soften the existing failure contract."""

    class _ExplodingTeam:
        async def run_stream(self, *, task: str) -> Any:
            raise RuntimeError("provider down")
            yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(po, "build_production_team", lambda *a, **k: _ExplodingTeam())
    execute = po._make_default_execute(_board(tmp_path), "a1", None, None)

    outcome = execute(None, _config(), "A", "magentic", "the task")  # type: ignore[arg-type]

    assert outcome.status == "hard_fail"
    assert "provider down" in outcome.summary
