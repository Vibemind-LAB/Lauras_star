"""Cooperative cancellation across production tools, teams, and the orchestrator."""

from __future__ import annotations

import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.jobs import JobContext, enqueue
from laura.short_creator import handlers, production_agents, production_orchestrator
from laura.short_creator.board import Board
from laura.short_creator.board_models import BoardMeta
from laura.short_creator.orchestrator import StageOutcome, TeamKind
from laura.short_creator.production_tools import ProductionDeps, build_production_tool_specs
from laura.short_creator.providers import AgentConfig, Stage


def _seed_board(tmp_path: Path) -> tuple[Database, str, Board]:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db: Database = SqliteDatabase(settings.db_path)
    db.migrate()
    workspace_root = tmp_path / "ws" / "project"
    project = repos.create_project(
        db,
        name="cancel-test",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(workspace_root),
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="source",
        source_path=str(workspace_root / "source.mp4"),
    )
    asset_id = str(asset["id"])
    board = Board.create(
        workspace_root / "agent-runs" / "cancel-session" / "board",
        BoardMeta(
            session_id="cancel-session",
            asset_id=asset_id,
            created_utc="2026-08-09T00:00:00+00:00",
            task="cancel test",
            language="English",
            target_seconds=10.0,
        ),
    )
    return db, asset_id, board


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


def test_cancel_immediately_before_mutating_tool_prevents_board_write(tmp_path: Path) -> None:
    """Removing the ToolSpec guard would let the language mutation reach meta.json."""
    db, asset_id, board = _seed_board(tmp_path)
    deps = ProductionDeps(cancel_requested=lambda: True)
    tools = {
        spec.name: spec.func
        for spec in build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)
    }

    result = tools["set_board_language"]("German")

    assert result == {
        "ok": False,
        "status": "cancelled",
        "reason": "user requested cancellation",
    }
    assert board.meta().language == "English"
    assert tools["board_status"]()["ok"] is True


def _install_fake_autogen(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    created: dict[str, object] = {}

    class FakeFunctionTool:
        def __init__(self, func: object, *, name: str = "", description: str = "") -> None:
            self.func = func
            self.name = name

    class FakeAssistantAgent:
        def __init__(self, *, name: str, **_kwargs: object) -> None:
            self.name = name

    class FakeFunctionalTermination:
        def __init__(self, func: object) -> None:
            created["termination_predicate"] = func

    class FakeMagenticOneGroupChat:
        def __init__(self, *, termination_condition: object, **_kwargs: object) -> None:
            self._termination_condition = termination_condition

    modules = {
        "autogen_agentchat": types.ModuleType("autogen_agentchat"),
        "autogen_agentchat.agents": types.ModuleType("autogen_agentchat.agents"),
        "autogen_agentchat.conditions": types.ModuleType("autogen_agentchat.conditions"),
        "autogen_agentchat.teams": types.ModuleType("autogen_agentchat.teams"),
        "autogen_core": types.ModuleType("autogen_core"),
        "autogen_core.tools": types.ModuleType("autogen_core.tools"),
    }
    modules["autogen_agentchat.agents"].AssistantAgent = FakeAssistantAgent  # type: ignore[attr-defined]
    modules["autogen_agentchat.conditions"].FunctionalTermination = (  # type: ignore[attr-defined]
        FakeFunctionalTermination
    )
    modules["autogen_agentchat.teams"].MagenticOneGroupChat = (  # type: ignore[attr-defined]
        FakeMagenticOneGroupChat
    )
    modules["autogen_core.tools"].FunctionTool = FakeFunctionTool  # type: ignore[attr-defined]
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return created


def test_cancel_between_agent_turns_terminates_team(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dropping cancel_requested from FunctionalTermination would schedule the next turn."""
    db, asset_id, board = _seed_board(tmp_path)
    cancelled = iter([False, True])
    deps = ProductionDeps(cancel_requested=lambda: next(cancelled, True))
    created = _install_fake_autogen(monkeypatch)
    monkeypatch.setattr(production_agents, "build_model_client", lambda *a, **k: object())

    production_agents.build_production_team(
        db,
        board,
        _config(),
        asset_id=asset_id,
        deps=deps,
    )

    predicate = cast(Callable[[list[object]], bool], created["termination_predicate"])
    assert predicate([]) is False
    assert predicate([]) is True


def test_post_team_cancel_is_terminal_without_stage_b(tmp_path: Path) -> None:
    """A cancellation observed after Stage A must not be escalated as an agent failure."""
    db, asset_id, board = _seed_board(tmp_path)
    cancelled = False
    stages: list[str] = []
    deps = ProductionDeps(cancel_requested=lambda: cancelled)

    def execute(
        _db: Database, _config: AgentConfig, stage: Stage, _kind: TeamKind, _task: str
    ) -> StageOutcome:
        nonlocal cancelled
        stages.append(stage)
        cancelled = True
        return StageOutcome(
            status="hard_fail",
            weak=True,
            summary="provider failed after cancellation",
            team="magentic",
            stage=stage,
        )

    result = production_orchestrator.run_production(
        db,
        _config(),
        asset_id=asset_id,
        session_id="cancel-session",
        task="cancel test",
        execute=execute,
        deps=deps,
    )

    assert stages == ["A"]
    assert result["status"] == "cancelled"
    assert result["escalated"] is False
    assert board.meta().status == "cancelled"


def test_cancel_before_run_skips_board_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-existing cancel flag must be observed before restore can write artifacts."""
    db, asset_id, board = _seed_board(tmp_path)
    restore_calls = 0

    def forbidden_restore(self: Board) -> list[str]:
        nonlocal restore_calls
        restore_calls += 1
        return []

    monkeypatch.setattr(Board, "restore_coherent_suffix", forbidden_restore)
    result = production_orchestrator.run_production(
        db,
        _config(),
        asset_id=asset_id,
        session_id="cancel-session",
        task="cancel test",
        deps=ProductionDeps(cancel_requested=lambda: True),
    )

    assert result["status"] == "cancelled"
    assert restore_calls == 0


def test_cancel_from_config_warning_sink_stops_before_board_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancel arriving through event delivery must win before restore mutates artifacts."""
    db, asset_id, board = _seed_board(tmp_path)
    cancelled = False
    restore_calls = 0

    def request_cancel(event: dict[str, Any]) -> None:
        nonlocal cancelled
        assert event["type"] == "config_warning"
        cancelled = True

    def forbidden_restore(self: Board) -> list[str]:
        nonlocal restore_calls
        restore_calls += 1
        return []

    monkeypatch.setattr(Board, "restore_coherent_suffix", forbidden_restore)
    result = production_orchestrator.run_production(
        db,
        _config(),
        asset_id=asset_id,
        session_id="cancel-session",
        task="cancel test",
        deps=ProductionDeps(cancel_requested=lambda: cancelled),
        event_sink=request_cancel,
    )

    assert result["status"] == "cancelled"
    assert board.meta().status == "cancelled"
    assert restore_calls == 0


def test_cancel_immediately_before_stage_b_prevents_escalation(tmp_path: Path) -> None:
    """The boundary between Stage A classification and Stage B is another agent turn."""
    db, asset_id, _board = _seed_board(tmp_path)
    stages: list[str] = []
    stage_a_done = False
    polls_after_stage_a = 0

    def cancel_requested() -> bool:
        nonlocal polls_after_stage_a
        if not stage_a_done:
            return False
        polls_after_stage_a += 1
        return polls_after_stage_a >= 2

    def execute(
        _db: Database, _config: AgentConfig, stage: Stage, _kind: TeamKind, _task: str
    ) -> StageOutcome:
        nonlocal stage_a_done
        stages.append(stage)
        stage_a_done = True
        return StageOutcome(
            status="hard_fail",
            weak=True,
            summary="failed",
            team="magentic",
            stage=stage,
        )

    result = production_orchestrator.run_production(
        db,
        _config(),
        asset_id=asset_id,
        session_id="cancel-session",
        task="cancel test",
        execute=execute,
        deps=ProductionDeps(cancel_requested=cancel_requested),
    )

    assert stages == ["A"]
    assert result["status"] == "cancelled"
    assert result["escalated"] is False


def test_handler_uses_real_job_cancel_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replacing the handler callback with a constant would ignore the jobs table flag."""
    db, asset_id, board = _seed_board(tmp_path)
    payload: dict[str, Any] = {
        "asset_id": asset_id,
        "session_id": board.meta().session_id,
        "task": "cancel test",
    }
    job_id = enqueue(db, queue="ai", kind="production.run", payload=payload)
    repos.cancel_job(db, job_id)
    captured: dict[str, ProductionDeps] = {}

    def fake_run(*_args: object, **kwargs: object) -> dict[str, Any]:
        captured["deps"] = cast(ProductionDeps, kwargs["deps"])
        return {"status": "cancelled"}

    monkeypatch.setattr(production_orchestrator, "run_production", fake_run)
    monkeypatch.setattr("laura.short_creator.providers.resolve_from_env", lambda: _config())
    ctx = JobContext(
        job_id=job_id,
        kind="production.run",
        queue="ai",
        payload=payload,
        db=db,
    )

    handlers.handle_production_run(ctx, deps=ProductionDeps())

    assert captured["deps"].cancel_requested is not None
    assert captured["deps"].cancel_requested() is True
