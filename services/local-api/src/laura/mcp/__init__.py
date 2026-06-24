"""laura.mcp — MCP surface for the Laura build graph and auto-shorts pipeline.

Exports all testable tool handlers.  Importing this package does NOT
require the ``mcp`` SDK to be installed; only ``laura.mcp.server`` pulls in
the MCP transport (guarded import, optional extra).
"""

from .tools import (
    tool_batch_plan,
    tool_batch_status,
    tool_explain_candidate,
    tool_extract_shorts,
    tool_job_status,
    tool_list_short_candidates,
    tool_next_action,
    tool_recipe_from_trace,
    tool_start_analysis,
)

__all__ = [
    "tool_batch_plan",
    "tool_batch_status",
    "tool_explain_candidate",
    "tool_extract_shorts",
    "tool_job_status",
    "tool_list_short_candidates",
    "tool_next_action",
    "tool_recipe_from_trace",
    "tool_start_analysis",
]
