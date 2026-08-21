from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from laura_mcp import tools_export
from laura_mcp.client import LauraError

from .conftest import make_client


def call(tool: str, handler: Any, /, **kwargs: Any) -> Any:
    client = make_client(handler)
    return tools_export.TOOLS[tool](client, **kwargs)


def test_render_timeline_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/timelines/t1/render"
        body = json.loads(request.content)
        assert body == {"captions": False, "caption_source": "auto", "caption_preset": "wide"}
        return httpx.Response(202, json={"export_id": "e1", "job_id": "j1"})

    out = call("render_timeline", handler, timeline_id="t1")
    assert out == {"export_id": "e1", "job_id": "j1"}


def test_render_timeline_forwards_caption_options() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/timelines/t1/render"
        body = json.loads(request.content)
        assert body == {
            "captions": True,
            "caption_source": "voiceover",
            "caption_preset": "reels",
        }
        return httpx.Response(202, json={"export_id": "e1", "job_id": "j1"})

    out = call(
        "render_timeline",
        handler,
        timeline_id="t1",
        captions=True,
        caption_source="voiceover",
        caption_preset="reels",
    )
    assert out == {"export_id": "e1", "job_id": "j1"}


def test_list_exports_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/projects/p1/exports"
        return httpx.Response(200, json=[{"id": "e1"}, {"id": "e2"}])

    out = call("list_exports", handler, project_id="p1")
    assert out == [{"id": "e1"}, {"id": "e2"}]


def test_get_export_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/exports/e1"
        return httpx.Response(200, json={"id": "e1", "status": "succeeded"})

    out = call("get_export", handler, export_id="e1")
    assert out == {"id": "e1", "status": "succeeded"}


def test_auto_produce_overview_posts_topic() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/projects/p1/auto-overview"
        body = json.loads(request.content)
        assert body == {"topic": "product launch", "target_seconds": 60}
        return httpx.Response(202, json={"job_id": "j1"})

    out = call(
        "auto_produce", handler, kind="overview", project_id="p1", topic="product launch",
    )
    assert out == {"job_id": "j1"}


def test_auto_produce_short_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/projects/p1/auto-short"
        body = json.loads(request.content)
        assert body == {"topic": "highlight reel", "target_seconds": 30}
        return httpx.Response(202, json={"job_id": "j2"})

    out = call(
        "auto_produce",
        handler,
        kind="short",
        project_id="p1",
        topic="highlight reel",
        target_seconds=30,
    )
    assert out == {"job_id": "j2"}


def test_auto_produce_rejects_unknown_kind() -> None:
    call_count = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        call_count[0] += 1
        raise AssertionError("no HTTP call expected")

    with pytest.raises(LauraError, match="unknown kind"):
        call("auto_produce", handler, kind="trailer", project_id="p1", topic="x")

    assert call_count[0] == 0


def test_build_narrated_reel_posts_beats_with_defaults() -> None:
    beats = [
        {"text": "Rowboat turns tickets into shipped code.", "asset_id": "a1", "src_in_frame": 1320},
        {
            "text": "Every agent run is reviewable, end to end.",
            "asset_id": "a1",
            "src_in_frame": 4200,
            "pad_frames": 20,
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/projects/p1/narrated-reel"
        body = json.loads(request.content)
        assert body == {
            "name": None,
            "beats": beats,
            "crossfade_frames": 8,
            "final_fade_frames": 12,
            "backend": None,
            "voice_id": None,
            "language": None,
            "render": True,
            "caption_preset": "wide",
        }
        return httpx.Response(202, json={"timeline_id": "t1", "job_id": "j1"})

    out = call("build_narrated_reel", handler, project_id="p1", beats=beats)
    assert out["timeline_id"] == "t1"
    assert out["job_id"] == "j1"
    assert "job_status" in out["hint"]


def test_build_narrated_reel_forwards_all_options() -> None:
    beats = [{"text": "Hello", "asset_id": "a1", "src_in_frame": 0}]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/projects/p9/narrated-reel"
        body = json.loads(request.content)
        assert body == {
            "name": "Rowboat Produktvideo",
            "beats": beats,
            "crossfade_frames": 6,
            "final_fade_frames": 10,
            "backend": "elevenlabs",
            "voice_id": "v1",
            "language": "German",
            "render": False,
            "caption_preset": "reels",
        }
        return httpx.Response(202, json={"timeline_id": "t9", "job_id": "j9"})

    out = call(
        "build_narrated_reel",
        handler,
        project_id="p9",
        beats=beats,
        name="Rowboat Produktvideo",
        backend="elevenlabs",
        voice_id="v1",
        language="German",
        render=False,
        caption_preset="reels",
        crossfade_frames=6,
        final_fade_frames=10,
    )
    assert out == {
        "timeline_id": "t9",
        "job_id": "j9",
        "hint": "Poll job_status(job_id) for the collage build (and export, if render=True).",
    }


def test_build_narrated_reel_error_returns_laura_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "beat 0: asset not found"})

    with pytest.raises(LauraError) as exc:
        call(
            "build_narrated_reel",
            handler,
            project_id="p1",
            beats=[{"text": "Hi", "asset_id": "ghost", "src_in_frame": 0}],
        )
    assert "beat 0: asset not found" in str(exc.value)
    assert "{" not in str(exc.value)
