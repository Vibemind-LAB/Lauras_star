"""laura-deck MCP tool handlers — testable plain functions, NO import mcp.

Each function takes an explicit ``db: Database`` (testable with in-memory DB)
and returns a JSON-serialisable dict.  All functions are pure reads; none write
to the database.

These are thin wrappers over the existing pure resolvers:
- ``resolve_next_action``  (api.shorts)
- ``plan_batch``           (api.batch)
- ``batch_status``         (api.batch)
- ``recipe_from_trace``    (api.batch)
"""

from __future__ import annotations

import logging
from typing import Any

from ..api.batch import batch_status, plan_batch, recipe_from_trace
from ..api.shorts import resolve_next_action
from ..db.database import Database

logger = logging.getLogger(__name__)

__all__ = [
    "tool_next_action",
    "tool_batch_plan",
    "tool_batch_status",
    "tool_recipe_from_trace",
]


def tool_next_action(db: Database, short_id: str) -> dict[str, Any]:
    """Return the next action for a short (asset).

    Returns the same shape as ``NextActionOut.model_dump()`` when found, or
    ``{"found": False, "short_id": short_id}`` when the asset does not exist.
    """
    action = resolve_next_action(db, short_id)
    if action is None:
        logger.debug("tool_next_action: short_id=%r not found", short_id)
        return {"found": False, "short_id": short_id}
    result: dict[str, Any] = action.model_dump()
    result["found"] = True
    return result


def tool_batch_plan(db: Database, short_ids: list[str]) -> dict[str, Any]:
    """Resolve next_action for each short_id and return a batch plan dict.

    Returns the dict produced by ``plan_batch`` with Pydantic models serialised
    to plain dicts (``NextActionOut`` → dict).
    """
    raw = plan_batch(db, short_ids)
    # plan_batch already returns dicts, but action values are NextActionOut objects
    plans: list[dict[str, Any]] = []
    for entry in raw["plans"]:
        action_val = entry.get("action")
        serialised_action: dict[str, Any] | None
        if action_val is not None and hasattr(action_val, "model_dump"):
            serialised_action = action_val.model_dump()
        else:
            serialised_action = action_val  # already None or dict
        plans.append(
            {
                "short_id": entry["short_id"],
                "found": entry["found"],
                "action": serialised_action,
                "hash": entry["hash"],
            }
        )
    return {"plans": plans, "batch_hash": raw["batch_hash"]}


def tool_batch_status(db: Database, short_ids: list[str]) -> dict[str, Any]:
    """Roll up next_action across a manifest into stage counts.

    Returns the dict produced by ``batch_status`` directly — it is already a
    plain JSON-serialisable dict.
    """
    return batch_status(db, short_ids)


def tool_recipe_from_trace(db: Database, run_id: str) -> dict[str, Any]:
    """Reconstruct and verify a short_run's recipe from its export trace.

    Returns the dict produced by ``recipe_from_trace``.  Empty dict ``{}``
    signals "not found" (the run_id does not exist in the ledger).
    """
    return recipe_from_trace(db, run_id)
