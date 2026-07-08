"""Agent roster for the short-creator (Iteration 4/9).

``agent_specs`` is pure (no autogen, no db) — the roster, system prompts and
tool assignment are tested directly. ``build_agents`` is the autogen-touching
constructor (lazy import), tested with fake autogen modules so no real LLM or
extra is needed. A cross-check guards that every assigned tool name really
exists in the in-process toolset.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from laura.db.database import Database
from laura.short_creator import agents, providers, toolset

EXPECTED_AGENTS = {
    "scout",
    "describer",
    "transcript_analyst",
    "director",
    "editor",
    "transcript_master",
    "qa",
}


def test_agent_specs_roster() -> None:
    specs = agents.agent_specs()
    assert {s.name for s in specs} == EXPECTED_AGENTS
    for s in specs:
        assert s.system_message.strip()
        assert s.description.strip()


def test_agent_specs_key_tool_assignments() -> None:
    by_name = {s.name: s for s in agents.agent_specs()}
    assert "search_visual_moments" in by_name["scout"].tool_names
    assert "extract_shorts" in by_name["scout"].tool_names
    assert "build_roughcut" in by_name["editor"].tool_names
    assert "render_timeline" in by_name["editor"].tool_names
    assert "get_similar_segments" in by_name["director"].tool_names
    assert "describe_moment" in by_name["describer"].tool_names
    assert "transcript_window" in by_name["transcript_analyst"].tool_names


def test_editor_must_chain_build_then_render() -> None:
    # Live-run finding: a 7B editor TALKS through the task; its prompt must demand the tool
    # chain, and it needs >1 tool iteration to chain build_roughcut -> render_timeline in one
    # graph turn (AssistantAgent defaults to a single tool round).
    by_name = {s.name: s for s in agents.agent_specs()}
    editor = by_name["editor"]
    assert "MUST" in editor.system_message
    assert editor.system_message.index("build_roughcut") < editor.system_message.index(
        "render_timeline"
    )
    assert "render_short" in editor.tool_names  # short-form path: render the CHOSEN candidate
    assert "render_short" in editor.system_message
    assert editor.max_tool_iterations >= 4
    # Every tool-bearing agent gets at least a couple of iterations.
    for spec in agents.agent_specs():
        if spec.tool_names:
            assert spec.max_tool_iterations >= 2, spec.name


def test_qa_verifies_exports_with_export_status() -> None:
    # Live-run finding: QA fed the EDITED export_id into explain_candidate (candidate ids
    # only) and judged a produced short as "nothing was produced". It must verify exports
    # with export_status and know rendering still counts as produced.
    by_name = {s.name: s for s in agents.agent_specs()}
    qa = by_name["qa"]
    assert "export_status" in qa.tool_names
    assert "EDITED export_id" in qa.system_message
    assert "WAITS for the render" in qa.system_message
    assert "SAME export id" in qa.system_message


def test_transcript_master_triggers_on_new_transcript_phrases() -> None:
    # Live-run finding: the user wrote "transkipt new" and the agent SKIPped — the trigger
    # list must cover new-script/new-transcript phrasings (and common misspellings).
    by_name = {s.name: s for s in agents.agent_specs()}
    msg = by_name["transcript_master"].system_message
    for phrase in (
        "neu einsprechen",
        "new voiceover",
        "neues skript",
        "transkript neu",
        "transcript new",
        "transkipt",
    ):
        assert phrase in msg, phrase
    assert "SKIP" in msg


def test_editor_auto_pick_for_scene_count_tasks() -> None:
    # Live-run finding: "~60s, 15 Szenen à 4s" produced 4 long segments — the editor must
    # forward scene count / per-scene length into render_short's deterministic auto mode.
    by_name = {s.name: s for s in agents.agent_specs()}
    msg = by_name["editor"].system_message
    assert "max_segments" in msg
    assert "max_segment_seconds" in msg


def test_scout_answers_in_task_language() -> None:
    # Live-run finding: the 7B scout drifted into Chinese prose mid-run.
    by_name = {s.name: s for s in agents.agent_specs()}
    assert "task's language" in by_name["scout"].system_message


def test_every_agent_tool_exists_in_toolset(db: Database) -> None:
    toolnames = {s.name for s in toolset.build_tool_specs(db)}
    for spec in agents.agent_specs():
        assert set(spec.tool_names) <= toolnames, spec.name


def test_build_agents_missing_extra_raises(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "autogen_agentchat", None)
    with pytest.raises(RuntimeError, match="autoshort"):
        agents.build_agents(db, providers.resolve_from_env({}))


def _install_fake_autogen(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Fake the autogen surface build_agents touches. Returns the created agent list."""
    created: list[object] = []

    class FakeClient:
        def __init__(self, **kw: object) -> None:
            pass

    class FakeFunctionTool:
        def __init__(self, func: object, *, name: str = "", description: str = "") -> None:
            self.name = name
            self.description = description

    class FakeAssistantAgent:
        def __init__(
            self,
            *,
            name: str,
            model_client: object,
            tools: tuple[object, ...] = (),
            description: str = "",
            system_message: str = "",
            max_tool_iterations: int = 1,
        ) -> None:
            self.name = name
            self.model_client = model_client
            self.tools = list(tools)
            self.description = description
            self.system_message = system_message
            self.max_tool_iterations = max_tool_iterations
            created.append(self)

    ollama = types.ModuleType("autogen_ext.models.ollama")
    ollama.OllamaChatCompletionClient = FakeClient  # type: ignore[attr-defined]
    core_tools = types.ModuleType("autogen_core.tools")
    core_tools.FunctionTool = FakeFunctionTool  # type: ignore[attr-defined]
    agentchat_agents = types.ModuleType("autogen_agentchat.agents")
    agentchat_agents.AssistantAgent = FakeAssistantAgent  # type: ignore[attr-defined]
    for name, mod in {
        "autogen_ext": types.ModuleType("autogen_ext"),
        "autogen_ext.models": types.ModuleType("autogen_ext.models"),
        "autogen_ext.models.ollama": ollama,
        "autogen_core": types.ModuleType("autogen_core"),
        "autogen_core.tools": core_tools,
        "autogen_agentchat": types.ModuleType("autogen_agentchat"),
        "autogen_agentchat.agents": agentchat_agents,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return created


def test_build_agents_constructs_full_roster(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_autogen(monkeypatch)
    built: list[Any] = list(agents.build_agents(db, providers.resolve_from_env({})))
    assert {a.name for a in built} == EXPECTED_AGENTS
    by_name: dict[str, Any] = {a.name: a for a in built}
    # Scout got its three tools; every agent got a model client; the editor can chain tools.
    assert len(by_name["scout"].tools) == 3
    assert all(a.model_client is not None for a in built)
    assert by_name["editor"].max_tool_iterations >= 4
