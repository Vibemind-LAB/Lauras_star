"""aria2 engine wrapper. The download test is skipped if aria2c is not installed."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from laura.ingest.aria2 import aria2_available, aria2_download

from ._flaky_http import serve

CONTENT = b"laura-aria2-" * 8192  # ~96 KiB


def test_aria2_available_reflects_path() -> None:
    assert aria2_available() == (shutil.which("aria2c") is not None)


@pytest.mark.skipif(shutil.which("aria2c") is None, reason="aria2c not installed")
def test_aria2_downloads_http_file(tmp_path: Path) -> None:
    dest_dir = tmp_path / "dl"
    with serve(CONTENT) as url:
        files = aria2_download(url, dest_dir, filename="out.bin")
    assert len(files) == 1
    assert files[0].read_bytes() == CONTENT
    assert all(not f.name.endswith(".aria2") for f in files)
