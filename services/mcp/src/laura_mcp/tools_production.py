"""Production family: author-mode sessions, scene gates, storyline, script, approval."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import LauraClient


def _start_production(
    client: LauraClient,
    *,
    asset_id: str,
    task: str,
    target_seconds: int = 60,
    format: str = "insta",
    language: str = "German",
) -> Any:
    body: dict[str, Any] = {
        "task": task,
        "target_seconds": target_seconds,
        "format": format,
        "language": language,
        "author": "external",
    }
    return client.request("POST", f"/assets/{asset_id}/production", json=body)


def _production_status(client: LauraClient, *, session_id: str) -> Any:
    status = client.request("GET", f"/production/{session_id}")
    events = client.request("GET", f"/production/{session_id}/events")
    return {"status": status, "events": events}


def _propose_scenes(
    client: LauraClient, *, session_id: str, candidates: list[dict[str, Any]]
) -> Any:
    body: dict[str, Any] = {"candidates": candidates}
    return client.request("PUT", f"/production/{session_id}/scene-proposal", json=body)


def _confirm_scenes(
    client: LauraClient,
    *,
    session_id: str,
    scene_numbers: list[int],
    selection_version: int,
) -> Any:
    body: dict[str, Any] = {
        "scene_numbers": scene_numbers,
        "selection_version": selection_version,
    }
    return client.request(
        "POST", f"/production/{session_id}/scene-selection:confirm", json=body
    )


def _save_storyline(
    client: LauraClient,
    *,
    session_id: str,
    red_thread: str,
    chapters: list[dict[str, Any]],
) -> Any:
    body: dict[str, Any] = {"red_thread": red_thread, "chapters": chapters}
    return client.request("PUT", f"/production/{session_id}/storyline", json=body)


def _save_script_chapter(
    client: LauraClient,
    *,
    session_id: str,
    chapter: int,
    lines: list[dict[str, Any]],
) -> Any:
    body: dict[str, Any] = {"lines": lines}
    return client.request(
        "PUT", f"/production/{session_id}/script/chapters/{chapter}", json=body
    )


def _approve_script(client: LauraClient, *, session_id: str) -> Any:
    return client.request("POST", f"/production/{session_id}/script:approve", json={})


TOOLS: dict[str, Callable[..., Any]] = {
    "start_production": _start_production,
    "production_status": _production_status,
    "propose_scenes": _propose_scenes,
    "confirm_scenes": _confirm_scenes,
    "save_storyline": _save_storyline,
    "save_script_chapter": _save_script_chapter,
    "approve_script": _approve_script,
}


def register(mcp: FastMCP, client: LauraClient) -> None:
    @mcp.tool()
    def start_production(
        asset_id: str,
        task: str,
        target_seconds: int = 60,
        format: str = "insta",
        language: str = "German",
    ) -> Any:
        """Start an author-mode production session on an asset: creates the board with
        scene gate + script gate armed; no team job runs — YOU write the creative
        artifacts. Flow: propose_scenes → user confirms → save_storyline →
        save_script_chapter per chapter → approve_script."""
        return _start_production(
            client,
            asset_id=asset_id,
            task=task,
            target_seconds=target_seconds,
            format=format,
            language=language,
        )

    @mcp.tool()
    def production_status(session_id: str) -> Any:
        """Board state (artifacts, gates incl. selection_version, resume_point) plus the
        event tail. Read this before every write."""
        return _production_status(client, session_id=session_id)

    @mcp.tool()
    def propose_scenes(session_id: str, candidates: list[dict[str, Any]]) -> Any:
        """Propose scenes for the short (arms the scene gate, bumps selection_version).
        candidates: [{scene_number, reason}]. THE USER picks — present the proposal
        and wait for their answer before confirm_scenes."""
        return _propose_scenes(client, session_id=session_id, candidates=candidates)

    @mcp.tool()
    def confirm_scenes(
        session_id: str, scene_numbers: list[int], selection_version: int
    ) -> Any:
        """Confirm the user's scene pick. selection_version must match
        production_status's scene_gate.selection_version — a changed proposal 409s;
        re-read and re-present."""
        return _confirm_scenes(
            client,
            session_id=session_id,
            scene_numbers=scene_numbers,
            selection_version=selection_version,
        )

    @mcp.tool()
    def save_storyline(
        session_id: str, red_thread: str, chapters: list[dict[str, Any]]
    ) -> Any:
        """Write the storyline (red_thread + chapters referencing confirmed
        scenes/windows). Server-side guards validate window references."""
        return _save_storyline(
            client, session_id=session_id, red_thread=red_thread, chapters=chapters
        )

    @mcp.tool()
    def save_script_chapter(
        session_id: str, chapter: int, lines: list[dict[str, Any]]
    ) -> Any:
        """Write one script chapter's lines [{chapter, scene_number, text}]. The
        capacity guard measures real speech rate — respect its rejections and shorten
        instead of arguing. Ground every claim in the transcript of the chosen
        material."""
        return _save_script_chapter(
            client, session_id=session_id, chapter=chapter, lines=lines
        )

    @mcp.tool()
    def approve_script(session_id: str) -> Any:
        """Approve the current script (content-hash-bound). Everything after — voice,
        cutlist, contact sheet, render, QA — runs deterministically without you. Only
        call after the user said yes to the script."""
        return _approve_script(client, session_id=session_id)
