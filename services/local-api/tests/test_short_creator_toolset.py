"""In-process tool bridge for the short-creator agents (Iteration 3/9).

Decision: the agents run in the same process as the db + Laura's tool_* funcs,
so we wrap those functions as in-process AutoGen tools instead of spawning the
stdio MCP server. ``build_tool_specs`` (db captured) is pure and tested against
a real in-memory db; ``build_function_tools`` is the only autogen-touching
function (lazy import), tested with a fake FunctionTool.
"""

from __future__ import annotations

import sys
import types

import pytest

from laura.db.database import Database
from laura.short_creator import toolset

EXPECTED_TOOLS = {
    "next_action",
    "search_visual_moments",
    "extract_shorts",
    "list_short_candidates",
    "explain_candidate",
    "score_visual_hook",
    "get_similar_segments",
    "build_roughcut",
    "render_timeline",
    "job_status",
    "describe_moment",
    "transcript_window",
    "transcript_overview",
    "render_short",
    "check_voice_alignment",
}


def test_build_tool_specs_exposes_expected_tools(db: Database) -> None:
    specs = toolset.build_tool_specs(db)
    names = {s.name for s in specs}
    assert names >= EXPECTED_TOOLS
    # Every spec carries a non-empty LLM-facing description and a callable.
    for s in specs:
        assert s.description.strip()
        assert callable(s.func)


def test_next_action_wrapper_calls_through_to_real_tool(db: Database) -> None:
    # Smoke: the wrapper injects db and calls the real tool_next_action; an unknown
    # asset must resolve to found=False (a pure DB read — no models, no autogen).
    specs = {s.name: s for s in toolset.build_tool_specs(db)}
    result = specs["next_action"].func("no-such-asset")
    assert result.get("found") is False


def test_job_status_wrapper_calls_through_to_real_tool(db: Database) -> None:
    specs = {s.name: s for s in toolset.build_tool_specs(db)}
    result = specs["job_status"].func("no-such-job")
    assert result.get("found") is False


def test_build_function_tools_missing_extra_raises(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "autogen_core", None)
    with pytest.raises(RuntimeError, match="autoshort"):
        toolset.build_function_tools(db)


def test_build_function_tools_wraps_every_spec(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeFunctionTool:
        def __init__(self, func: object, *, name: str = "", description: str = "") -> None:
            self.func = func
            self.name = name
            self.description = description

    core = types.ModuleType("autogen_core")
    core_tools = types.ModuleType("autogen_core.tools")
    core_tools.FunctionTool = FakeFunctionTool  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "autogen_core", core)
    monkeypatch.setitem(sys.modules, "autogen_core.tools", core_tools)

    tools = toolset.build_function_tools(db)
    built = {t.name for t in tools}
    assert built >= EXPECTED_TOOLS
    assert all(t.description.strip() for t in tools)
