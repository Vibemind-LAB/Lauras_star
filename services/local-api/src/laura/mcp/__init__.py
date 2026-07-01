"""laura.mcp — MCP surface for the Laura build graph and auto-shorts pipeline.

Exports all testable tool handlers.  Importing this package does NOT
require the ``mcp`` SDK to be installed; only ``laura.mcp.server`` pulls in
the MCP transport (guarded import, optional extra).
"""

from .tools import (
    tool_batch_plan,
    tool_batch_status,
    tool_build_roughcut,
    tool_deduplicate_shorts,
    tool_explain_candidate,
    tool_extract_shorts,
    tool_job_status,
    tool_list_short_candidates,
    tool_next_action,
    tool_recipe_from_trace,
    tool_render_short,
    tool_render_timeline,
    tool_search_visual_moments,
    tool_similar_segments,
    tool_start_analysis,
    tool_visual_hook,
)

__all__ = [
    "tool_batch_plan",
    "tool_batch_status",
    "tool_build_roughcut",
    "tool_deduplicate_shorts",
    "tool_explain_candidate",
    "tool_extract_shorts",
    "tool_job_status",
    "tool_list_short_candidates",
    "tool_next_action",
    "tool_recipe_from_trace",
    "tool_render_short",
    "tool_render_timeline",
    "tool_search_visual_moments",
    "tool_similar_segments",
    "tool_start_analysis",
    "tool_visual_hook",
]
