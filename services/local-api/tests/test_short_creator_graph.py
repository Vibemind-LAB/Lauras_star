"""Deterministic GraphFlow fallback (Iteration 6).

The QA-weak routing condition is pure and unit-tested. ``build_graph_team`` is
exercised with a fake DiGraphBuilder/GraphFlow that records nodes + edges, so
the graph STRUCTURE (fan-out, join, conditional loop, entry point) is asserted
without real autogen.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from laura.db.database import Database
from laura.short_creator import graph, providers


class _Msg:
    def __init__(self, text: str) -> None:
        self._text = text

    def to_model_text(self) -> str:
        return self._text


def test_qa_weak_routes_only_on_weak() -> None:
    assert graph._qa_weak(_Msg("The short is WEAK and off-topic")) is True
    assert graph._qa_weak(_Msg("good — matches the topic and flows well")) is False


def test_build_graph_missing_extra_raises(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "autogen_agentchat", None)
    with pytest.raises(RuntimeError, match="autoshort"):
        graph.build_graph_team(db, providers.resolve_from_env({}))


def _install_fake_autogen(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    created: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kw: object) -> None:
            pass

    class FakeFunctionTool:
        def __init__(self, func: object, *, name: str = "", description: str = "") -> None:
            self.name = name

    class FakeAssistantAgent:
        def __init__(self, *, name: str, model_client: object, **kw: object) -> None:
            self.name = name

    class FakeDiGraph:
        pass

    class FakeDiGraphBuilder:
        def __init__(self) -> None:
            self.nodes: list[object] = []
            self.edges: list[tuple[object, object, object]] = []
            self.entry: object = None
            created["builder"] = self

        def add_node(self, node: object, **kw: object) -> FakeDiGraphBuilder:
            self.nodes.append(node)
            return self

        def add_edge(
            self, source: object, target: object, *, condition: object = None
        ) -> FakeDiGraphBuilder:
            self.edges.append((source, target, condition))
            return self

        def set_entry_point(self, node: object) -> FakeDiGraphBuilder:
            self.entry = node
            return self

        def build(self) -> FakeDiGraph:
            return FakeDiGraph()

    class FakeMaxMessageTermination:
        def __init__(self, n: int) -> None:
            self.n = n

    class FakeGraphFlow:
        def __init__(
            self, *, participants: tuple[object, ...], graph: object, termination_condition: object
        ) -> None:
            self.participants = list(participants)
            self.graph = graph
            self.termination_condition = termination_condition
            created["team"] = self

    ollama = types.ModuleType("autogen_ext.models.ollama")
    ollama.OllamaChatCompletionClient = FakeClient  # type: ignore[attr-defined]
    core_tools = types.ModuleType("autogen_core.tools")
    core_tools.FunctionTool = FakeFunctionTool  # type: ignore[attr-defined]
    ac_agents = types.ModuleType("autogen_agentchat.agents")
    ac_agents.AssistantAgent = FakeAssistantAgent  # type: ignore[attr-defined]
    ac_teams = types.ModuleType("autogen_agentchat.teams")
    ac_teams.DiGraphBuilder = FakeDiGraphBuilder  # type: ignore[attr-defined]
    ac_teams.GraphFlow = FakeGraphFlow  # type: ignore[attr-defined]
    ac_conditions = types.ModuleType("autogen_agentchat.conditions")
    ac_conditions.MaxMessageTermination = FakeMaxMessageTermination  # type: ignore[attr-defined]
    for name, mod in {
        "autogen_ext": types.ModuleType("autogen_ext"),
        "autogen_ext.models": types.ModuleType("autogen_ext.models"),
        "autogen_ext.models.ollama": ollama,
        "autogen_core": types.ModuleType("autogen_core"),
        "autogen_core.tools": core_tools,
        "autogen_agentchat": types.ModuleType("autogen_agentchat"),
        "autogen_agentchat.agents": ac_agents,
        "autogen_agentchat.teams": ac_teams,
        "autogen_agentchat.conditions": ac_conditions,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return created


def test_build_graph_team_encodes_pipeline(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = _install_fake_autogen(monkeypatch)
    team: Any = graph.build_graph_team(db, providers.resolve_from_env({}))
    builder: Any = created["builder"]
    edges = {(s.name, t.name) for (s, t, _c) in builder.edges}
    assert ("scout", "describer") in edges
    assert ("scout", "transcript_analyst") in edges  # fan-out
    assert ("describer", "director") in edges
    assert ("transcript_analyst", "director") in edges  # join
    assert ("director", "editor") in edges
    assert ("editor", "qa") in edges
    assert ("qa", "director") in edges  # conditional loop
    assert builder.entry.name == "scout"
    # The qa -> director loop carries a routing condition; forward edges do not.
    qa_edge = next(e for e in builder.edges if e[0].name == "qa" and e[1].name == "director")
    assert qa_edge[2] is not None
    assert len(team.participants) == 6
