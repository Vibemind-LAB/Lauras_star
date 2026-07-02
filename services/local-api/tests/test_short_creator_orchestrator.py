"""The escalation ladder (Iteration 7).

The ladder policy is tested with an injected ``execute`` that returns scripted
outcomes per (stage, team) — no autogen, no LLM. The default executor (build +
run the real AutoGen team) is manual-to-verify.
"""

from __future__ import annotations

from typing import cast

from laura.db.database import Database
from laura.short_creator import orchestrator, providers

# script maps (stage, team) -> ("ok"|"hard_fail", weak) or an Exception to raise.
Script = dict[tuple[str, str], "tuple[str, bool] | Exception"]


def _make_execute(script: Script) -> tuple[orchestrator.ExecuteFn, list[tuple[str, str]]]:
    calls: list[tuple[str, str]] = []

    def execute(
        db: Database,
        config: providers.AgentConfig,
        stage: str,
        kind: str,
        task: str,
    ) -> orchestrator.StageOutcome:
        calls.append((stage, kind))
        spec = script[(stage, kind)]
        if isinstance(spec, Exception):
            raise spec
        status, weak = spec
        return orchestrator.StageOutcome(
            status=cast(orchestrator.Status, status),
            weak=weak,
            summary="",
            team=cast(orchestrator.TeamKind, kind),
            stage=cast(providers.Stage, stage),
        )

    return execute, calls


def _run(
    db: Database, script: Script, **env: str
) -> tuple[dict[str, object], list[tuple[str, str]]]:
    execute, calls = _make_execute(script)
    config = providers.resolve_from_env(env)
    result = orchestrator.run_short_creator(
        db, config, asset_id="a", topic="cats", execute=execute
    )
    return result, calls


def test_stage_a_ok_no_escalation(db: Database) -> None:
    result, calls = _run(db, {("A", "magentic"): ("ok", False)})
    assert result["ok"] is True
    assert result["stage"] == "A"
    assert result["team"] == "magentic"
    assert result["escalated"] is False
    assert calls == [("A", "magentic")]


def test_magentic_hardfail_falls_back_to_graph_same_stage(db: Database) -> None:
    result, calls = _run(
        db, {("A", "magentic"): ("hard_fail", False), ("A", "graph"): ("ok", False)}
    )
    assert result["ok"] is True
    assert result["team"] == "graph"
    assert result["stage"] == "A"
    assert result["escalated"] is False
    assert calls == [("A", "magentic"), ("A", "graph")]


def test_magentic_exception_falls_back_to_graph(db: Database) -> None:
    result, calls = _run(
        db, {("A", "magentic"): RuntimeError("boom"), ("A", "graph"): ("ok", False)}
    )
    assert result["team"] == "graph"
    assert calls == [("A", "magentic"), ("A", "graph")]


def test_both_hardfail_escalates_to_stage_b(db: Database) -> None:
    result, calls = _run(
        db,
        {
            ("A", "magentic"): ("hard_fail", False),
            ("A", "graph"): ("hard_fail", False),
            ("B", "magentic"): ("ok", False),
        },
    )
    assert result["ok"] is True
    assert result["stage"] == "B"
    assert result["escalated"] is True
    assert ("B", "magentic") in calls


def test_graph_exception_at_stage_a_becomes_hardfail_and_escalates(db: Database) -> None:
    # Both teams RAISE at Stage A — _safe_execute converts each to hard_fail so the ladder still
    # escalates to Stage B (previously the graph exception propagated and bypassed escalation).
    result, calls = _run(
        db,
        {
            ("A", "magentic"): RuntimeError("m"),
            ("A", "graph"): RuntimeError("g"),
            ("B", "magentic"): ("ok", False),
        },
    )
    assert result["ok"] is True
    assert result["stage"] == "B"
    assert result["escalated"] is True
    assert calls == [("A", "magentic"), ("A", "graph"), ("B", "magentic")]


def test_soft_weak_without_auto_escalate_stays_stage_a(db: Database) -> None:
    result, calls = _run(db, {("A", "magentic"): ("ok", True)})
    assert result["ok"] is True
    assert result["weak"] is True
    assert result["stage"] == "A"
    assert result["escalated"] is False
    assert calls == [("A", "magentic")]  # no escalation


def test_soft_weak_with_auto_escalate_goes_to_b(db: Database) -> None:
    result, calls = _run(
        db,
        {("A", "magentic"): ("ok", True), ("B", "magentic"): ("ok", False)},
        LAURA_AGENT_AUTO_ESCALATE="1",
    )
    assert result["stage"] == "B"
    assert result["escalated"] is True
    assert result["weak"] is False


def test_orchestration_graph_forces_graph(db: Database) -> None:
    result, calls = _run(
        db, {("A", "graph"): ("ok", False)}, LAURA_AGENT_ORCHESTRATION="graph"
    )
    assert result["team"] == "graph"
    assert calls == [("A", "graph")]  # magentic skipped
