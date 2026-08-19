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
        assert body == {}
        return httpx.Response(202, json={"export_id": "e1", "job_id": "j1"})

    out = call("render_timeline", handler, timeline_id="t1")
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
