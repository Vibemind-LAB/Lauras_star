"""The short-creator agent roster (AutoGen ``AssistantAgent``s).

``agent_specs`` is pure data — the roster, system prompts and tool assignment,
testable without autogen or a db. ``build_agents`` is the autogen-touching
constructor: it builds one shared agent-role model client (from
:mod:`providers`) and the in-process tools (from :mod:`toolset`), then wires an
``AssistantAgent`` per spec.

Iteration 4 wires the agents to the existing 10 tools. The Describer's dedicated
VLM frame-description tool and the Transcript-Analyst's ±15s transcript-window
tool land in Iteration 4b; until then those two agents reason from the context
the team passes them (Describer also has the visual-signal tools below).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..db.database import Database
from .providers import AgentConfig, Stage, build_model_client
from .toolset import build_function_tools

if TYPE_CHECKING:  # annotation only — never imported at runtime
    from autogen_agentchat.agents import AssistantAgent


@dataclass(frozen=True)
class AgentSpec:
    """One team member: a name, a one-line description, a system prompt, and tool names."""

    name: str
    description: str
    system_message: str
    tool_names: tuple[str, ...]


def agent_specs() -> list[AgentSpec]:
    """The fixed short-creator roster. Pure — no autogen, no db.

    Tool names must exist in :func:`toolset.build_tool_specs` (cross-checked in tests).
    """
    return [
        AgentSpec(
            name="scout",
            description="Finds candidate moments in the analyzed video for the topic.",
            system_message=(
                "You are the Scout. Given a topic, find the most relevant candidate moments in "
                "the analyzed video. Use search_visual_moments (text->frame) and extract_shorts to "
                "surface candidates, then list_short_candidates to read them back. Return the "
                "candidate ids and frame ranges that best match the topic."
            ),
            tool_names=("search_visual_moments", "extract_shorts", "list_short_candidates"),
        ),
        AgentSpec(
            name="describer",
            description="Describes what is visually happening in each candidate.",
            system_message=(
                "You are the Describer. For each candidate moment, describe what is visually "
                "happening. Use score_visual_hook and explain_candidate to gauge each candidate's "
                "visual character. Return a short visual description per candidate id."
            ),
            tool_names=("score_visual_hook", "explain_candidate"),
        ),
        AgentSpec(
            name="transcript_analyst",
            description="Summarizes what is said around each candidate (±15s).",
            system_message=(
                "You are the Transcript Analyst. For each candidate, summarize what is being said "
                "around it (about ±15 seconds) so the Director knows the spoken context. Report "
                "concisely what happens in words per candidate id."
            ),
            tool_names=(),
        ),
        AgentSpec(
            name="director",
            description="Selects and orders the segments into a coherent short.",
            system_message=(
                "You are the Director. Given the visual descriptions and transcript summaries, "
                "select and order the best segments into a coherent short about the topic within "
                "the target length. Use list_short_candidates, explain_candidate, "
                "score_visual_hook and get_similar_segments to compare and choose. Output an "
                "ordered list of chosen candidate ids."
            ),
            tool_names=(
                "list_short_candidates",
                "explain_candidate",
                "score_visual_hook",
                "get_similar_segments",
            ),
        ),
        AgentSpec(
            name="editor",
            description="Assembles the chosen segments and renders the short.",
            system_message=(
                "You are the Editor. Assemble the chosen segments into a cut and render it. Use "
                "build_roughcut to create the timeline and render_timeline to produce the final "
                "vertical short; poll job_status for completion. Report the resulting timeline id "
                "and export id."
            ),
            tool_names=("build_roughcut", "render_timeline", "job_status"),
        ),
        AgentSpec(
            name="qa",
            description="Judges whether the short matches the topic and is coherent.",
            system_message=(
                "You are the QA gate. Judge whether the produced short matches the topic and is "
                "coherent. Use explain_candidate and score_visual_hook to assess quality. Respond "
                "with a verdict (good or weak) and a short reason; if weak, say what to improve."
            ),
            tool_names=("explain_candidate", "score_visual_hook"),
        ),
    ]


def build_agents(
    db: Database, config: AgentConfig, *, stage: Stage = "A"
) -> list[AssistantAgent]:
    """Construct one ``AssistantAgent`` per spec (lazy autogen import).

    All agents share one agent-role model client for *stage*; tools are the
    in-process FunctionTools selected by each spec's ``tool_names``. Raises a
    clear :class:`RuntimeError` if the optional ``autoshort`` extra is missing.
    """
    try:
        from autogen_agentchat.agents import AssistantAgent
    except ImportError as exc:
        raise RuntimeError(
            "The short-creator needs the optional 'autoshort' extra. "
            "Install it with: uv sync --extra autoshort"
        ) from exc

    tools_by_name = {tool.name: tool for tool in build_function_tools(db)}
    model_client = build_model_client(config, role="agent", stage=stage)
    return [
        AssistantAgent(
            name=spec.name,
            model_client=model_client,
            tools=[tools_by_name[n] for n in spec.tool_names if n in tools_by_name],
            description=spec.description,
            system_message=spec.system_message,
        )
        for spec in agent_specs()
    ]
