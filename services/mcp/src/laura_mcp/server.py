"""stdio MCP server for the Laura app. stdout is the protocol channel — log to stderr only."""
from __future__ import annotations

import logging
import os

from mcp.server.fastmcp import FastMCP

from .client import LauraClient, LauraError

logging.basicConfig(level=logging.INFO)  # stderr by default; NEVER print to stdout

mcp = FastMCP("laura")


def build_client() -> LauraClient:
    token = os.environ.get("LAURA_TOKEN", "")
    if not token:
        raise LauraError("LAURA_TOKEN is not set — configure it in the MCP registration env.")
    return LauraClient(token=token)


def main() -> None:
    from . import (
        tools_analysis,
        tools_editorial,
        tools_export,
        tools_jobs,
        tools_media,
        tools_production,
        tools_raw,
        tools_vision,
    )
    # Task 2: tools_analysis
    # Task 3: tools_editorial
    # Task 4: tools_export
    # Task 5: tools_jobs
    # Task 6: tools_media
    # Task 7: tools_production
    # Task 8: tools_vision

    client = build_client()
    tools_raw.register(mcp, client)
    tools_media.register(mcp, client)
    tools_analysis.register(mcp, client)  # Task 2
    tools_editorial.register(mcp, client)  # Task 3
    tools_export.register(mcp, client)  # Task 4
    tools_jobs.register(mcp, client)  # Task 5
    tools_production.register(mcp, client)  # Task 7
    tools_vision.register(mcp, client)  # Task 8
    mcp.run()
