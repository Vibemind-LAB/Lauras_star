"""Media family: projects, assets, import (file or URL), import progress."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import LauraClient


def _list_projects(client: LauraClient) -> Any:
    return client.request("GET", "/projects")


def _list_assets(client: LauraClient, *, project_id: str) -> Any:
    return client.request("GET", f"/projects/{project_id}/assets")


def _import_media(
    client: LauraClient,
    *,
    project_id: str,
    source: str,
    display_name: str | None = None,
    format: str | None = None,
    cookies_from_browser: str | None = None,
) -> Any:
    is_url = source.startswith(("http://", "https://"))
    body: dict[str, Any] = {"source_url": source} if is_url else {"source_path": source}
    if display_name:
        body["display_name"] = display_name
    if format:
        body["format"] = format
    if cookies_from_browser:
        body["cookies_from_browser"] = cookies_from_browser
    return client.request("POST", f"/projects/{project_id}/assets/import", json=body)


def _import_status(client: LauraClient, *, asset_id: str) -> Any:
    return client.request("GET", f"/assets/{asset_id}/import-status")


TOOLS: dict[str, Callable[..., Any]] = {
    "list_projects": _list_projects,
    "list_assets": _list_assets,
    "import_media": _import_media,
    "import_status": _import_status,
}


def register(mcp: FastMCP, client: LauraClient) -> None:
    @mcp.tool()
    def list_projects() -> Any:
        """List all Laura projects (id, name, frame rate)."""
        return _list_projects(client)

    @mcp.tool()
    def list_assets(project_id: str) -> Any:
        """List a project's media assets (id, display name, online, duration)."""
        return _list_assets(client, project_id=project_id)

    @mcp.tool()
    def import_media(
        project_id: str,
        source: str,
        display_name: str | None = None,
        format: str | None = None,
        cookies_from_browser: str | None = None,
    ) -> Any:
        """Import media into a project. `source` is a local file path OR a URL (yt-dlp;
        playlist/channel URLs fan out into one asset per entry — the response's
        extra_asset_ids lists the rest). Import runs async: poll import_status(asset_id).
        """
        return _import_media(
            client, project_id=project_id, source=source, display_name=display_name,
            format=format, cookies_from_browser=cookies_from_browser,
        )

    @mcp.tool()
    def import_status(asset_id: str) -> Any:
        """Progress of a running import (phase, percent, error)."""
        return _import_status(client, asset_id=asset_id)
