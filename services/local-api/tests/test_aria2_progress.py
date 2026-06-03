"""Parser for aria2c periodic progress lines (no network, no aria2c needed)."""

from __future__ import annotations

from laura.ingest.aria2 import _parse_aria2_progress


def test_parses_standard_progress_line() -> None:
    line = "[#7d3c1a 1.2GiB/30.0GiB(4%) CN:16 DL:5.0MiB ETA:1h38m]"
    got = _parse_aria2_progress(line)
    assert got is not None
    downloaded, total, speed = got
    assert downloaded == int(1.2 * 1024**3)
    assert total == int(30.0 * 1024**3)
    assert speed == int(5.0 * 1024**2)


def test_returns_none_for_non_progress_line() -> None:
    assert _parse_aria2_progress("12/34 08:00:00 [NOTICE] Downloading 1 item(s)") is None
    assert _parse_aria2_progress("") is None
