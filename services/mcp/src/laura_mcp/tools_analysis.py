"""Analysis family: analyze asset, get transcript, get shots/scenes, semantic search."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import LauraClient


def _analyze_asset(client: LauraClient, *, asset_id: str) -> Any:
    return client.request("POST", f"/assets/{asset_id}/analysis", json={})


def _get_transcript(
    client: LauraClient,
    *,
    asset_id: str,
    start_frame: int | None = None,
    end_frame_exclusive: int | None = None,
) -> Any:
    response = client.request("GET", f"/assets/{asset_id}/transcript")

    # Handle both bare list and wrapped response
    if isinstance(response, list):
        segments = response
        result = response
    else:
        segments = response.get("segments", [])
        result = response

    # Filter segments if frame range is specified
    if start_frame is not None and end_frame_exclusive is not None:
        filtered_segments = [
            seg for seg in segments
            if seg.get("end_frame", 0) > start_frame
            and seg.get("start_frame", 0) < end_frame_exclusive
        ]

        # Update the result with filtered segments
        if isinstance(result, dict):
            result["segments"] = filtered_segments
        else:
            result = filtered_segments

    return result


def _get_shots_and_scenes(
    client: LauraClient,
    *,
    project_id: str,
    asset_id: str,
) -> Any:
    shots = client.request("GET", f"/assets/{asset_id}/shots")
    rough_cut = client.request("GET", f"/projects/{project_id}/assets/{asset_id}/rough-cut")
    return {"shots": shots, "rough_cut": rough_cut}


def _search_material(
    client: LauraClient,
    *,
    project_id: str,
    query: str,
    mode: str = "semantic",
    limit: int = 10,
) -> Any:
    return client.request(
        "POST",
        "/search",
        json={
            "project_id": project_id,
            "query": query,
            "mode": mode,
            "limit": limit,
        },
    )


TOOLS: dict[str, Callable[..., Any]] = {
    "analyze_asset": _analyze_asset,
    "get_transcript": _get_transcript,
    "get_shots_and_scenes": _get_shots_and_scenes,
    "search_material": _search_material,
}


def register(mcp: FastMCP, client: LauraClient) -> None:
    @mcp.tool()
    def analyze_asset(asset_id: str) -> Any:
        """Analyze an asset: detect shots, scenes, and run automatic analysis."""
        return _analyze_asset(client, asset_id=asset_id)

    @mcp.tool()
    def get_transcript(
        asset_id: str,
        start_frame: int | None = None,
        end_frame_exclusive: int | None = None,
    ) -> Any:
        """Get transcript segments for an asset. Frames are integers; ranges are end-exclusive.
        If start_frame and end_frame_exclusive are set, only segments overlapping that range are returned.
        """
        return _get_transcript(
            client,
            asset_id=asset_id,
            start_frame=start_frame,
            end_frame_exclusive=end_frame_exclusive,
        )

    @mcp.tool()
    def get_shots_and_scenes(project_id: str, asset_id: str) -> Any:
        """Get detected shots and rough-cut scenes for an asset (two queries merged)."""
        return _get_shots_and_scenes(client, project_id=project_id, asset_id=asset_id)

    @mcp.tool()
    def search_material(
        project_id: str,
        query: str,
        mode: str = "semantic",
        limit: int = 10,
    ) -> Any:
        """Search transcripts in a project. Semantic search over qdrant with lexical fallback.
        Returns segments with asset_id, start_frame, end_frame, text, score (all frames are integers, ranges end-exclusive).
        """
        return _search_material(
            client,
            project_id=project_id,
            query=query,
            mode=mode,
            limit=limit,
        )
