"""Fallback orchestration: a deterministic ``GraphFlow`` over the short-creator roster.

The graph steers the order — the LLM only judges per node — so it is robust with a weak local
model. Used when Magentic-One fails/under-performs (see ``orchestrator.py``). Shape::

    scout -> {describer, transcript_analyst} -> director -> transcript_master -> editor -> qa
    (qa is the leaf; the transcript_master SKIPs itself unless the task asks to re-voice)

The Director joins the Describer + Transcript-Analyst branches; the QA gate's verdict lands in
the transcript, where the LADDER reads it (weak -> manual/auto escalation) — a qa->director loop
edge is deliberately absent: AutoGen's DiGraph validation requires at least one leaf node, and a
cyclic graph has none (live-run finding). Runtime join semantics are manual-to-verify (no LLM in
CI); the graph STRUCTURE is asserted in tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..db.database import Database
from .agents import build_agents
from .providers import AgentConfig, Stage

if TYPE_CHECKING:  # annotation only — never imported at runtime
    from autogen_agentchat.teams import GraphFlow

# Overall message budget across the (cyclic) graph — a runaway-loop backstop.
MAX_MESSAGES = 60


def build_graph_team(db: Database, config: AgentConfig, *, stage: Stage = "A") -> GraphFlow:
    """Assemble the deterministic fallback team.

    Lazy autogen import; raises a clear :class:`RuntimeError` if the optional ``autoshort`` extra
    is missing. *stage* selects the provider tier (A local / B escalated).
    """
    try:
        from autogen_agentchat.conditions import MaxMessageTermination
        from autogen_agentchat.teams import DiGraphBuilder, GraphFlow
    except ImportError as exc:
        raise RuntimeError(
            "The short-creator needs the optional 'autoshort' extra. "
            "Install it with: uv sync --extra autoshort"
        ) from exc

    # list[Any]: participants wants list[ChatAgent]; AssistantAgent subclasses it, but list is
    # invariant (and ChatAgent is only importable with the extra installed).
    agents: list[Any] = list(build_agents(db, config, stage=stage))
    by_name = {agent.name: agent for agent in agents}
    builder = DiGraphBuilder()
    for agent in agents:
        builder.add_node(agent)
    builder.add_edge(by_name["scout"], by_name["describer"])
    builder.add_edge(by_name["scout"], by_name["transcript_analyst"])  # fan-out
    builder.add_edge(by_name["describer"], by_name["director"])
    builder.add_edge(by_name["transcript_analyst"], by_name["director"])  # join
    builder.add_edge(by_name["director"], by_name["transcript_master"])
    builder.add_edge(by_name["transcript_master"], by_name["editor"])
    builder.add_edge(by_name["editor"], by_name["qa"])  # qa is the leaf (verdict read by ladder)
    builder.set_entry_point(by_name["scout"])
    return GraphFlow(
        participants=agents,
        graph=builder.build(),
        termination_condition=MaxMessageTermination(MAX_MESSAGES),
    )
