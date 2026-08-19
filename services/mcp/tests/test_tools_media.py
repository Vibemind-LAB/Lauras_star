from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from laura_mcp import tools_media
from laura_mcp.client import LauraError

from .conftest import make_client


def call(tool: str, handler: Any, /, **kwargs: Any) -> Any:
    client = make_client(handler)
    return tools_media.TOOLS[tool](client, **kwargs)


def test_list_projects_hits_projects_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET" and request.url.path == "/projects"
        return httpx.Response(200, json=[{"id": "p1", "name": "Demo"}])

    assert call("list_projects", handler) == [{"id": "p1", "name": "Demo"}]


def test_import_media_with_url_posts_source_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/projects/p1/assets/import"
        body = json.loads(request.content)
        assert body == {"source_url": "https://example.com/v.mp4", "format": "best"}
        return httpx.Response(202, json={"asset_id": "a1", "job_id": "j1"})

    out = call("import_media", handler, project_id="p1",
               source="https://example.com/v.mp4", format="best")
    assert out["asset_id"] == "a1"


def test_import_media_with_path_posts_source_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body == {"source_path": "C:/videos/talk.mp4"}
        return httpx.Response(202, json={"asset_id": "a2", "job_id": "j2"})

    assert call("import_media", handler, project_id="p1",
                source="C:/videos/talk.mp4")["asset_id"] == "a2"


def test_import_status_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/assets/a1/import-status"
        return httpx.Response(200, json={"phase": "downloading"})

    assert call("import_status", handler, asset_id="a1")["phase"] == "downloading"


def test_backend_down_message_reaches_caller() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(LauraError, match="Laura app is not running"):
        call("list_projects", handler)
