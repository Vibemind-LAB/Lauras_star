"""laura_api — the escape hatch for every endpoint without a first-class tool."""
from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from .client import LauraClient


def register(mcp: FastMCP, client: LauraClient) -> None:
    @mcp.tool()
    def laura_api(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        """Raw call against the local Laura API (127.0.0.1:8765) for endpoints without a
        dedicated tool (reels, per-scene music, audio clips, voiceover voices, batch shorts,
        sequence transitions, deletes ...). method is GET/POST/PUT/PATCH/DELETE; path starts
        with '/'. DESTRUCTIVE CALLS (any DELETE, anything ending in a removal): always ask
        the user for confirmation first — deleting productions/assets/projects is permanent.
        """
        return client.request(method.upper(), path, json=body)
