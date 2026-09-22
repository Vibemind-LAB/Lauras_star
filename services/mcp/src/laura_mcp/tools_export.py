"""Export family: render timelines, list/get exports, auto-produce overview/short,
build narrated-reel collages."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import LauraClient, LauraError

_VALID_KINDS = {"overview", "short"}


def _render_timeline(
    client: LauraClient,
    *,
    timeline_id: str,
    captions: bool = False,
    caption_source: str = "auto",
    caption_preset: str = "wide",
) -> Any:
    return client.request(
        "POST",
        f"/timelines/{timeline_id}/render",
        json={
            "captions": captions,
            "caption_source": caption_source,
            "caption_preset": caption_preset,
        },
    )


def _list_exports(client: LauraClient, *, project_id: str) -> Any:
    return client.request("GET", f"/projects/{project_id}/exports")


def _get_export(client: LauraClient, *, export_id: str) -> Any:
    return client.request("GET", f"/exports/{export_id}")


def _export_download_url(client: LauraClient, *, export_id: str) -> Any:
    return client.request("GET", f"/exports/{export_id}/download-url")


def _asset_download_url(client: LauraClient, *, asset_id: str) -> Any:
    return client.request("GET", f"/assets/{asset_id}/download-url")


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


def _build_narrated_reel(
    client: LauraClient,
    *,
    project_id: str,
    beats: list[dict[str, Any]],
    name: str | None = None,
    backend: str | None = None,
    voice_id: str | None = None,
    language: str | None = None,
    render: bool = True,
    caption_preset: str = "wide",
    crossfade_frames: int = 8,
    final_fade_frames: int = 12,
) -> Any:
    body: dict[str, Any] = {
        "name": name,
        "beats": beats,
        "crossfade_frames": crossfade_frames,
        "final_fade_frames": final_fade_frames,
        "backend": backend,
        "voice_id": voice_id,
        "language": language,
        "render": render,
        "caption_preset": caption_preset,
    }
    result = client.request("POST", f"/projects/{project_id}/narrated-reel", json=body)
    if isinstance(result, dict):
        return {
            **result,
            "hint": "Poll job_status(job_id) for the collage build (and export, if render=True).",
        }
    return result


TOOLS: dict[str, Callable[..., Any]] = {
    "render_timeline": _render_timeline,
    "list_exports": _list_exports,
    "get_export": _get_export,
    "auto_produce": _auto_produce,
    "build_narrated_reel": _build_narrated_reel,
}


def register(mcp: FastMCP, client: LauraClient) -> None:
    @mcp.tool()
    def render_timeline(
        timeline_id: str,
        captions: bool = False,
        caption_source: str = "auto",
        caption_preset: str = "wide",
    ) -> Any:
        """Render a timeline to a new export. Returns {export_id, job_id} — poll
        job_status(job_id) for progress.

        captions=True burns styled karaoke captions into the export (independent of
        the plain-SRT burn_captions path). caption_source picks the word source:
        "auto" (default) prefers voiceover-authored words (from a narrated-reel/
        voiceover build) and falls back to the source-video transcript when none
        exist; "voiceover" and "transcript" pin one source with no fallback.
        caption_preset is one of "reels", "tiktok", "shorts", "wide".
        """
        return _render_timeline(
            client,
            timeline_id=timeline_id,
            captions=captions,
            caption_source=caption_source,
            caption_preset=caption_preset,
        )

    @mcp.tool()
    def list_exports(project_id: str) -> Any:
        """List a project's exports."""
        return _list_exports(client, project_id=project_id)

    @mcp.tool()
    def get_export(export_id: str) -> Any:
        """Get one export's status and metadata."""
        return _get_export(client, export_id=export_id)

    @mcp.tool()
    def download_url_for_asset(asset_id: str) -> Any:
        """Get a ready-to-use download link for a SOURCE video.

        USE THIS whenever someone asks how to obtain a video file. The `object_key`
        you see on an asset is an address, not a link -- it cannot be fetched on its
        own, because doing so needs a service key that agents never get. This tool
        exchanges the address for a link that carries its own authorisation: it
        works with no headers, no key, and expires after a while.

        Do NOT re-render or re-export a video just to obtain a file. If the asset
        already exists, this is the way to get it.

        Returns url, expires_in_seconds and object_key. A 404 means there is no copy
        in object storage yet (material imported before 2026-09-16 has none).
        """
        return _asset_download_url(client, asset_id=asset_id)

    @mcp.tool()
    def download_url_for_export(export_id: str) -> Any:
        """Get a ready-to-use download link for a FINISHED RENDER.

        Same idea as download_url_for_asset, for rendered exports. Note that an
        export reaching status 'ready' does not yet mean the copy is in storage --
        the render is recorded as finished first and uploaded afterwards. A 404 on a
        fresh export usually means "wait a moment and ask again".
        """
        return _export_download_url(client, export_id=export_id)

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

    @mcp.tool()
    def build_narrated_reel(
        project_id: str,
        beats: list[dict[str, Any]],
        name: str | None = None,
        backend: str | None = None,
        voice_id: str | None = None,
        language: str | None = None,
        render: bool = True,
        caption_preset: str = "wide",
        crossfade_frames: int = 8,
        final_fade_frames: int = 12,
    ) -> Any:
        """Build a narrated-reel collage: a beat list of spoken lines over source-video
        slices, stitched into a fresh timeline with crossfades and a synthesized voice
        track. 1-64 beats, each: {"text": str, "asset_id": str, "src_in_frame": int,
        "pad_frames": int (optional, default 12)}. Example:

            beats = [
                {"text": "Rowboat turns tickets into shipped code.",
                 "asset_id": "a1", "src_in_frame": 1320},
                {"text": "Every agent run is reviewable, end to end.",
                 "asset_id": "a1", "src_in_frame": 4200, "pad_frames": 20},
            ]

        A beat's clip length is DERIVED from the synthesized narration's measured
        speech duration plus that beat's pad_frames — you do not set clip duration
        directly; a beat longer than its source asset is clamped to the asset's end.

        Verify each beat's src_in_frame by frame index first with get_frame — never
        by time-seek; VFR sources drift.

        render=True (default) chains a captioned export once the collage is built
        (caption_source="voiceover", caption_preset as given). Returns
        {timeline_id, job_id, hint} — poll job_status(job_id) for completion.
        """
        return _build_narrated_reel(
            client,
            project_id=project_id,
            beats=beats,
            name=name,
            backend=backend,
            voice_id=voice_id,
            language=language,
            render=render,
            caption_preset=caption_preset,
            crossfade_frames=crossfade_frames,
            final_fade_frames=final_fade_frames,
        )
