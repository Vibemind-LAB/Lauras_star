"""Primary orchestration: ``MagenticOneGroupChat`` over the short-creator roster.

Magentic-One's orchestrator plans, delegates to the agents, tracks a progress ledger and re-plans
on stalls. It needs a capable orchestrator model (``LAURA_ORCHESTRATOR_MODEL``, or escalation to
9router); on a weak local model it may under-perform — exactly what the GraphFlow fallback
(``graph.py``) and the escalation ladder (``orchestrator.py``) exist to catch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..db.database import Database
from .agents import build_agents
from .providers import AgentConfig, Stage, build_model_client

if TYPE_CHECKING:  # annotation only — never imported at runtime
    from autogen_agentchat.teams import MagenticOneGroupChat

# Hard cap on the orchestrator's turn budget — a runaway-loop backstop.
MAX_TURNS = 30


def build_magentic_team(
    db: Database, config: AgentConfig, *, stage: Stage = "A"
) -> MagenticOneGroupChat:
    """Assemble the primary Magentic-One team (roster + orchestrator model client).

    Lazy autogen import; raises a clear :class:`RuntimeError` if the optional ``autoshort`` extra
    is missing. *stage* selects the provider tier (A local / B escalated).
    """
    try:
        from autogen_agentchat.teams import MagenticOneGroupChat
    except ImportError as exc:
        raise RuntimeError(
            "The short-creator needs the optional 'autoshort' extra. "
            "Install it with: uv sync --extra autoshort"
        ) from exc

    agents = build_agents(db, config, stage=stage)
    orchestrator = build_model_client(config, role="orchestrator", stage=stage)
    return MagenticOneGroupChat(
        participants=agents, model_client=orchestrator, max_turns=MAX_TURNS
    )
