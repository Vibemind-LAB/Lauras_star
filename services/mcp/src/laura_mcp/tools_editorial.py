"""Editorial family: timelines, frame-exact operations, scenes, undo/redo."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import LauraClient, LauraError

_VALID_OPS = {
    "trim",
    "move",
    "delete",
    "delete_words",
    "lift",
    "insert",
    "append",
    "place_clip",
    "set_speed",
    "set_audio_offset",
}


def _get_timeline(
    client: LauraClient,
    *,
    timeline_id: str | None = None,
    project_id: str | None = None,
) -> Any:
    if (timeline_id is None) == (project_id is None):
        raise LauraError("pass exactly one of timeline_id or project_id")
    if timeline_id is not None:
        timeline = client.request("GET", f"/timelines/{timeline_id}")
        history = client.request("GET", f"/timelines/{timeline_id}/history")
        return {"timeline": timeline, "history": history}
    return client.request("GET", f"/projects/{project_id}/timelines")


def _edit_timeline(client: LauraClient, *, timeline_id: str, operation: dict[str, Any]) -> Any:
    op = operation.get("op")
    if op not in _VALID_OPS:
        raise LauraError(f"unknown op: {op!r}")
    return client.request("POST", f"/timelines/{timeline_id}/operations", json=operation)


def _edit_scenes(
    client: LauraClient,
    *,
    timeline_id: str,
    action: str,
    args: dict[str, Any],
) -> Any:
    if action == "generate":
        return client.request("POST", f"/timelines/{timeline_id}/scenes:generate", json=args)
    if action == "split":
        scene_id = args["scene_id"]
        body = {k: v for k, v in args.items() if k != "scene_id"}
        return client.request(
            "POST", f"/timelines/{timeline_id}/scenes/{scene_id}/split", json=body,
        )
    if action == "merge":
        return client.request("POST", f"/timelines/{timeline_id}/scenes/merge", json=args)
    if action == "cut_at_frame":
        return client.request("POST", f"/timelines/{timeline_id}/cut-at-frame", json=args)
    if action == "rename":
        scene_id = args["scene_id"]
        body = {k: v for k, v in args.items() if k != "scene_id"}
        return client.request("PATCH", f"/scenes/{scene_id}", json=body)
    raise LauraError(f"unknown action: {action!r}")


def _timeline_undo(client: LauraClient, *, timeline_id: str, redo: bool = False) -> Any:
    verb = "redo" if redo else "undo"
    return client.request("POST", f"/timelines/{timeline_id}/{verb}", json={})


TOOLS: dict[str, Callable[..., Any]] = {
    "get_timeline": _get_timeline,
    "edit_timeline": _edit_timeline,
    "edit_scenes": _edit_scenes,
    "timeline_undo": _timeline_undo,
}


def register(mcp: FastMCP, client: LauraClient) -> None:
    @mcp.tool()
    def get_timeline(
        timeline_id: str | None = None,
        project_id: str | None = None,
    ) -> Any:
        """Get a timeline (with its edit history) by timeline_id, or list a project's
        timelines by project_id. Pass exactly one of the two.
        """
        return _get_timeline(client, timeline_id=timeline_id, project_id=project_id)

    @mcp.tool()
    def edit_timeline(timeline_id: str, operation: dict[str, Any]) -> Any:
        """Apply one frame-exact operation to a timeline. operation is a dict with an "op"
        field (trim, move, delete, delete_words, lift, insert, append, place_clip,
        set_speed, set_audio_offset) plus op-specific fields, passed through verbatim.

        All frames are integers; ranges are end-exclusive (out_frame_exclusive). OTIO is the source of truth — never edit exports, always the timeline.
        """
        return _edit_timeline(client, timeline_id=timeline_id, operation=operation)

    @mcp.tool()
    def edit_scenes(timeline_id: str, action: str, args: dict[str, Any]) -> Any:
        """Manage a timeline's scenes. action is one of generate, split, merge,
        cut_at_frame, rename; args carries the action's fields (e.g. scene_id, frame,
        name).

        All frames are integers; ranges are end-exclusive (out_frame_exclusive). OTIO is the source of truth — never edit exports, always the timeline.
        """
        return _edit_scenes(client, timeline_id=timeline_id, action=action, args=args)

    @mcp.tool()
    def timeline_undo(timeline_id: str, redo: bool = False) -> Any:
        """Undo the last timeline operation, or redo it if redo=True."""
        return _timeline_undo(client, timeline_id=timeline_id, redo=redo)
