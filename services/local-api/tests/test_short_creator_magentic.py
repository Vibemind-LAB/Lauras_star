"""Primary Magentic-One team assembly (Iteration 5).

``build_magentic_team`` wires the roster + an orchestrator model client into a
``MagenticOneGroupChat``. Tested with fake autogen modules — no real LLM/extra.
"""

from __future__ import annotations

import sys
import types

import pytest

from laura.db.database import Database
from laura.short_creator import magentic, providers


def test_build_magentic_missing_extra_raises(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "autogen_agentchat", None)
    with pytest.raises(RuntimeError, match="autoshort"):
        magentic.build_magentic_team(db, providers.resolve_from_env({}))


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

    class FakeMagenticOneGroupChat:
        def __init__(
            self, *, participants: tuple[object, ...], model_client: object, max_turns: int = 0
        ) -> None:
            # Mirrors the real autogen_agentchat MagenticOneGroupChat, which only exposes
            # these as private attributes (set by BaseGroupChat.__init__ / this __init__).
            self._participants = list(participants)
            self._model_client = model_client
            self._max_turns = max_turns
            created["team"] = self

    ollama = types.ModuleType("autogen_ext.models.ollama")
    ollama.OllamaChatCompletionClient = FakeClient  # type: ignore[attr-defined]
    core_tools = types.ModuleType("autogen_core.tools")
    core_tools.FunctionTool = FakeFunctionTool  # type: ignore[attr-defined]
    ac_agents = types.ModuleType("autogen_agentchat.agents")
    ac_agents.AssistantAgent = FakeAssistantAgent  # type: ignore[attr-defined]
    ac_teams = types.ModuleType("autogen_agentchat.teams")
    ac_teams.MagenticOneGroupChat = FakeMagenticOneGroupChat  # type: ignore[attr-defined]
    for name, mod in {
        "autogen_ext": types.ModuleType("autogen_ext"),
        "autogen_ext.models": types.ModuleType("autogen_ext.models"),
        "autogen_ext.models.ollama": ollama,
        "autogen_core": types.ModuleType("autogen_core"),
        "autogen_core.tools": core_tools,
        "autogen_agentchat": types.ModuleType("autogen_agentchat"),
        "autogen_agentchat.agents": ac_agents,
        "autogen_agentchat.teams": ac_teams,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return created


def test_build_magentic_team_wires_roster(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_autogen(monkeypatch)
    team = magentic.build_magentic_team(db, providers.resolve_from_env({}))
    # autogen's MagenticOneGroupChat only exposes these privately (no public accessor).
    assert len(team._participants) == 7
    assert team._model_client is not None
    assert team._max_turns == magentic.MAX_TURNS
