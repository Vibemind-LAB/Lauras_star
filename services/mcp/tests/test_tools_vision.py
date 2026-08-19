from __future__ import annotations

from typing import Any

import httpx

from laura_mcp import tools_vision

from .conftest import make_client


def call(tool: str, handler: Any, /, **kwargs: Any) -> Any:
    client = make_client(handler)
    return tools_vision.TOOLS[tool](client, **kwargs)


def test_get_frame_returns_png_bytes() -> None:
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/assets/a1/frame/10"
        return httpx.Response(200, content=png_bytes)

    result = call("get_frame", handler, asset_id="a1", frame=10)
    assert result == png_bytes


def test_get_contact_sheet_path() -> None:
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/production/s1/contact-sheet"
        return httpx.Response(200, content=png_bytes)

    result = call("get_contact_sheet", handler, session_id="s1")
    assert result == png_bytes
