from __future__ import annotations

import json
from typing import Any

import httpx

from laura_mcp import tools_production

from .conftest import make_client


def call(tool: str, handler: Any, /, **kwargs: Any) -> Any:
    client = make_client(handler)
    return tools_production.TOOLS[tool](client, **kwargs)


def test_start_production_sends_author_external() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/assets/a1/production"
        body = json.loads(request.content)
        assert body == {
            "task": "short",
            "target_seconds": 60,
            "format": "insta",
            "language": "German",
            "author": "external",
        }
        return httpx.Response(201, json={"session_id": "s1"})

    out = call(
        "start_production",
        handler,
        asset_id="a1",
        task="short",
        target_seconds=60,
        format="insta",
        language="German",
    )
    assert out["session_id"] == "s1"


def test_production_status_merges_status_and_events() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if "/events" in request.url.path:
            assert request.method == "GET"
            assert request.url.path == "/production/s1/events"
            return httpx.Response(200, json=[{"type": "scene_gate_armed"}])
        else:
            assert request.method == "GET"
            assert request.url.path == "/production/s1"
            return httpx.Response(
                200,
                json={
                    "session_id": "s1",
                    "status": "active",
                    "scene_gate": {"selection_version": 1},
                },
            )

    out = call("production_status", handler, session_id="s1")
    assert call_count == 2
    assert "status" in out
    assert "events" in out
    assert out["events"] == [{"type": "scene_gate_armed"}]


def test_propose_scenes_put_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/production/s1/scene-proposal"
        body = json.loads(request.content)
        assert body == {
            "candidates": [
                {"scene_number": 1, "reason": "opening"},
                {"scene_number": 3, "reason": "climax"},
            ]
        }
        return httpx.Response(200, json={"selection_version": 2})

    out = call(
        "propose_scenes",
        handler,
        session_id="s1",
        candidates=[
            {"scene_number": 1, "reason": "opening"},
            {"scene_number": 3, "reason": "climax"},
        ],
    )
    assert out["selection_version"] == 2


def test_confirm_scenes_posts_selection_version() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/production/s1/scene-selection:confirm"
        body = json.loads(request.content)
        assert body == {"scene_numbers": [1, 3], "selection_version": 2}
        return httpx.Response(200, json={"confirmed": True})

    out = call(
        "confirm_scenes",
        handler,
        session_id="s1",
        scene_numbers=[1, 3],
        selection_version=2,
    )
    assert out["confirmed"] is True


def test_save_storyline_put() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/production/s1/storyline"
        body = json.loads(request.content)
        assert body == {
            "red_thread": "A journey begins",
            "chapters": [
                {"chapter": 1, "scenes": [1, 2], "title": "Act One"}
            ],
        }
        return httpx.Response(200, json={"saved": True})

    out = call(
        "save_storyline",
        handler,
        session_id="s1",
        red_thread="A journey begins",
        chapters=[{"chapter": 1, "scenes": [1, 2], "title": "Act One"}],
    )
    assert out["saved"] is True


def test_save_script_chapter_puts_chapter_number() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/production/s1/script/chapters/2"
        body = json.loads(request.content)
        assert body == {
            "lines": [
                {"chapter": 2, "scene_number": 1, "text": "Hello world"}
            ]
        }
        return httpx.Response(200, json={"saved": True})

    out = call(
        "save_script_chapter",
        handler,
        session_id="s1",
        chapter=2,
        lines=[{"chapter": 2, "scene_number": 1, "text": "Hello world"}],
    )
    assert out["saved"] is True


def test_approve_script_post() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/production/s1/script:approve"
        body = json.loads(request.content)
        assert body == {}
        return httpx.Response(200, json={"approved": True})

    out = call("approve_script", handler, session_id="s1")
    assert out["approved"] is True
