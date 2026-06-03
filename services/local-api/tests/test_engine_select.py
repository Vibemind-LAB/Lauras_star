"""Protocol-based download-engine selection (pure function, no network)."""

from __future__ import annotations

import pytest

from laura.ingest.engine import select_engine


@pytest.mark.parametrize("url", [
    "http://example.com/a.mp4",
    "https://example.com/a.mp4",
    "https://drive.usercontent.google.com/download?id=x",
])
def test_http_uses_httpx(url: str) -> None:
    assert select_engine(url) == "httpx"


@pytest.mark.parametrize("url", [
    "magnet:?xt=urn:btih:abcdef",
    "https://example.com/film.torrent",
    "https://example.com/list.metalink",
    "https://example.com/list.meta4",
    "ftp://example.com/a.mp4",
    "ftps://example.com/a.mp4",
    "sftp://example.com/a.mp4",
])
def test_non_http_uses_aria2(url: str) -> None:
    assert select_engine(url) == "aria2"
