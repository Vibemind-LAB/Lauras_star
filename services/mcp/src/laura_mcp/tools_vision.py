"""Vision family: get frames, contact sheets."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP, Image

from .client import LauraClient


def _get_frame(client: LauraClient, *, asset_id: str, frame: int) -> bytes:
    return client.get_bytes(f"/assets/{asset_id}/frame/{frame}")


def _get_contact_sheet(client: LauraClient, *, session_id: str) -> bytes:
    return client.get_bytes(f"/production/{session_id}/contact-sheet")


TOOLS: dict[str, Callable[..., Any]] = {
    "get_frame": _get_frame,
    "get_contact_sheet": _get_contact_sheet,
}


def register(mcp: FastMCP, client: LauraClient) -> None:
    @mcp.tool()
    def get_frame(asset_id: str, frame: int) -> Image:
        """One video frame as a PNG image — look before you cut. Frames are integers."""
        png_bytes = _get_frame(client, asset_id=asset_id, frame=frame)
        return Image(data=png_bytes, format="png")

    @mcp.tool()
    def get_contact_sheet(session_id: str) -> Image:
        """The production's current contact sheet as a PNG image."""
        png_bytes = _get_contact_sheet(client, session_id=session_id)
        return Image(data=png_bytes, format="png")
