"""Export family: render timelines, list/get exports, auto-produce overview/short."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import LauraClient, LauraError

_VALID_KINDS = {"overview", "short"}


def _render_timeline(client: LauraClient, *, timeline_id: str) -> Any:
    return client.request("POST", f"/timelines/{timeline_id}/render", json={})


def _list_exports(client: LauraClient, *, project_id: str) -> Any:
    return client.request("GET", f"/projects/{project_id}/exports")


def _get_export(client: LauraClient, *, export_id: str) -> Any:
    return client.request("GET", f"/exports/{export_id}")


def _auto_produce(
    client: LauraClient,
    *,
    kind: str,
    project_id: str,
    topic: str,
    target_seconds: int = 60,
) -> Any:
    if kind not in _VALID_KINDS:
        raise LauraError(f"unknown kind: {kind!r}")
    return client.request(
        "POST",
        f"/projects/{project_id}/auto-{kind}",
        json={"topic": topic, "target_seconds": target_seconds},
    )


TOOLS: dict[str, Callable[..., Any]] = {
    "render_timeline": _render_timeline,
    "list_exports": _list_exports,
    "get_export": _get_export,
    "auto_produce": _auto_produce,
}


def register(mcp: FastMCP, client: LauraClient) -> None:
    @mcp.tool()
    def render_timeline(timeline_id: str) -> Any:
        """Render a timeline to a new export. Returns {export_id, job_id} — poll
        job_status(job_id) for progress.
        """
        return _render_timeline(client, timeline_id=timeline_id)

    @mcp.tool()
    def list_exports(project_id: str) -> Any:
        """List a project's exports."""
        return _list_exports(client, project_id=project_id)

    @mcp.tool()
    def get_export(export_id: str) -> Any:
        """Get one export's status and metadata."""
        return _get_export(client, export_id=export_id)

    @mcp.tool()
    def auto_produce(
        kind: str,
        project_id: str,
        topic: str,
        target_seconds: int = 60,
    ) -> Any:
        """Auto-produce a video from existing project material without manual editing.
        kind is "overview" (broad recap) or "short" (tight highlight cut around topic).
        """
        return _auto_produce(
            client, kind=kind, project_id=project_id, topic=topic,
            target_seconds=target_seconds,
        )
