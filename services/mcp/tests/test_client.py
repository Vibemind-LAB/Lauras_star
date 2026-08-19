from __future__ import annotations

import httpx
import pytest

from laura_mcp.client import BACKEND_DOWN, LauraError

from .conftest import make_client


def test_request_sends_token_and_parses_json() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers.get("X-Laura-Token", "")
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)
    assert client.request("GET", "/projects") == {"ok": True}
    assert seen["token"] == "test-token"
    assert seen["url"] == "http://127.0.0.1:8765/projects"


def test_http_error_surfaces_detail_sentence_not_raw_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "session not found"})

    client = make_client(handler)
    with pytest.raises(LauraError) as exc:
        client.request("GET", "/production/nope")
    assert "session not found" in str(exc.value)
    assert "{" not in str(exc.value)  # never the raw JSON body


def test_connect_error_becomes_backend_down_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = make_client(handler)
    with pytest.raises(LauraError) as exc:
        client.request("GET", "/projects")
    assert str(exc.value) == BACKEND_DOWN
    assert exc.value.message == BACKEND_DOWN


def test_get_bytes_returns_raw_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x89PNG...")

    client = make_client(handler)
    assert client.get_bytes("/assets/a/frame/10").startswith(b"\x89PNG")
