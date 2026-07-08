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
    """One team member: a name, a one-line description, a system prompt, and tool names.

    ``max_tool_iterations`` — tool rounds per turn. AssistantAgent defaults to ONE, which cannot
    chain build_roughcut → render_timeline within a single graph turn (live-run finding).
    """

    name: str
    description: str
    system_message: str
    tool_names: tuple[str, ...]
    max_tool_iterations: int = 1


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
            max_tool_iterations=4,
        ),
        AgentSpec(
            name="describer",
            description="Describes what is visually happening in each candidate.",
            system_message=(
                "You are the Describer. For each candidate moment, describe what is visually "
                "happening. Use describe_moment on a representative frame to get a VLM "
                "description, and score_visual_hook to gauge the visual opening. Return a short "
                "visual description per candidate id."
            ),
            tool_names=("describe_moment", "score_visual_hook"),
            max_tool_iterations=4,
        ),
        AgentSpec(
            name="transcript_analyst",
            description="Summarizes what is said around each candidate (±15s).",
            system_message=(
                "You are the Transcript Analyst. For each candidate, use transcript_window on its "
                "center frame to read what is being said around it. window_frames is in FRAMES, "
                "not seconds — leave it at the default 450 (±15s at 30fps). Report concisely what "
                "happens in words per candidate id."
            ),
            tool_names=("transcript_window",),
            max_tool_iterations=4,
        ),
        AgentSpec(
            name="director",
            description="Selects and orders the segments into a coherent short.",
            system_message=(
                "You are the Director. Given the visual descriptions and transcript summaries, "
                "select and order the best segments into a coherent short about the topic within "
                "the target length. Use list_short_candidates, explain_candidate, "
                "score_visual_hook and get_similar_segments to compare and choose. End with "
                "exactly one line: CHOSEN: <candidate_id>, <candidate_id>, ... in play order."
            ),
            tool_names=(
                "list_short_candidates",
                "explain_candidate",
                "score_visual_hook",
                "get_similar_segments",
            ),
            max_tool_iterations=4,
        ),
        AgentSpec(
            name="editor",
            description="Assembles the chosen segments and renders the short.",
            system_message=(
                "You are the Editor. You MUST use your tools — never answer in prose only. "
                "Step 1: call build_roughcut with the asset_id from the task. Step 2: take the "
                "timeline_id from its result and call render_timeline with it. Only after BOTH "
                "calls succeeded, reply with exactly: EDITED timeline_id=<id> export_id=<id>. If "
                "a call fails, report the error instead — do not pretend."
            ),
            tool_names=("build_roughcut", "render_timeline", "job_status"),
            max_tool_iterations=8,
        ),
        AgentSpec(
            name="qa",
            description="Judges whether the short matches the topic and is coherent.",
            system_message=(
                "You are the QA gate. Judge whether the produced short matches the topic and is "
                "coherent. If the Editor did NOT report a real timeline_id and export_id, the "
                "verdict is weak — nothing was produced. Respond with a verdict (good or weak) "
                "and a short reason; if weak, say what to improve."
            ),
            tool_names=("explain_candidate", "score_visual_hook"),
            max_tool_iterations=2,
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
            max_tool_iterations=spec.max_tool_iterations,
        )
        for spec in agent_specs()
    ]
