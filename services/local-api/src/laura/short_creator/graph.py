"""Fallback orchestration: a deterministic ``GraphFlow`` over the short-creator roster.

The graph steers the order — the LLM only judges per node — so it is robust with a weak local
model. Used when Magentic-One fails/under-performs (see ``orchestrator.py``). Shape::

    scout -> {describer, transcript_analyst} -> director -> editor -> qa
    qa --("weak")--> director            (bounded overall by MAX_MESSAGES)

The Director joins the Describer + Transcript-Analyst branches; the QA gate loops back to the
Director only when its verdict reads as "weak". Runtime join/loop semantics are manual-to-verify
(no LLM in CI); the graph STRUCTURE is asserted in tests.
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


def _qa_weak(message: Any) -> bool:
    """QA verdict routes back to the Director only when it reads as 'weak'."""
    text = message.to_model_text() if hasattr(message, "to_model_text") else str(message)
    return "weak" in text.lower()


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

    agents = build_agents(db, config, stage=stage)
    by_name = {agent.name: agent for agent in agents}
    builder = DiGraphBuilder()
    for agent in agents:
        builder.add_node(agent)
    builder.add_edge(by_name["scout"], by_name["describer"])
    builder.add_edge(by_name["scout"], by_name["transcript_analyst"])  # fan-out
    builder.add_edge(by_name["describer"], by_name["director"])
    builder.add_edge(by_name["transcript_analyst"], by_name["director"])  # join
    builder.add_edge(by_name["director"], by_name["editor"])
    builder.add_edge(by_name["editor"], by_name["qa"])
    builder.add_edge(by_name["qa"], by_name["director"], condition=_qa_weak)  # conditional loop
    builder.set_entry_point(by_name["scout"])
    return GraphFlow(
        participants=agents,
        graph=builder.build(),
        termination_condition=MaxMessageTermination(MAX_MESSAGES),
    )
