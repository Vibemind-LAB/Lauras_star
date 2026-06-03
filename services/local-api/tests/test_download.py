"""Unit tests for the resumable downloader against a local flaky HTTP server."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from laura.ingest.download import DownloadError, download_resumable

from ._flaky_http import serve

CONTENT = b"laura-resilient-ingest-" * 4096  # ~94 KiB, deterministic


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_simple_download_succeeds(tmp_path: Path) -> None:
    dest = tmp_path / "out.bin"
    with serve(CONTENT) as url:
        result = download_resumable(url, dest)
    assert dest.read_bytes() == CONTENT
    assert result.size_bytes == len(CONTENT)
    assert result.sha256 == _sha(CONTENT)
    assert not dest.with_name(dest.name + ".part").exists()


def test_cut_connection_then_resume(tmp_path: Path) -> None:
    dest = tmp_path / "out.bin"
    part = dest.with_name(dest.name + ".part")
    with serve(CONTENT, cut_after=10_000) as url:
        # first attempt: server drops the connection partway through
        with pytest.raises(DownloadError):
            download_resumable(url, dest, connections=1)
        assert part.exists() and 0 < part.stat().st_size < len(CONTENT)
        assert not dest.exists()  # not promoted until verified-complete

        # second attempt: resumes from .part and completes
        result = download_resumable(url, dest, connections=1)
    assert dest.read_bytes() == CONTENT
    assert result.sha256 == _sha(CONTENT)


def test_sha256_mismatch_raises(tmp_path: Path) -> None:
    dest = tmp_path / "out.bin"
    with serve(CONTENT) as url, pytest.raises(DownloadError, match="sha256"):
        download_resumable(url, dest, expected_sha256="00" * 32)


def test_server_ignoring_range_restarts_cleanly(tmp_path: Path) -> None:
    dest = tmp_path / "out.bin"
    part = dest.with_name(dest.name + ".part")
    part.write_bytes(b"STALE" * 1000)  # leftover partial from a prior attempt
    with serve(CONTENT, ignore_range=True) as url:
        result = download_resumable(url, dest)
    assert dest.read_bytes() == CONTENT
    assert result.sha256 == _sha(CONTENT)


def test_size_mismatch_raises(tmp_path: Path) -> None:
    dest = tmp_path / "out.bin"
    # server advertises more bytes than it sends -> our size check must catch it.
    # httpx may raise a RemoteProtocolError (incomplete read) before our own
    # size-mismatch check is reached; either way the exception is wrapped as
    # DownloadError so we assert on the type alone rather than the message text.
    with (
        serve(CONTENT, fake_content_length=len(CONTENT) + 100) as url,
        pytest.raises(DownloadError),
    ):
        download_resumable(url, dest)
