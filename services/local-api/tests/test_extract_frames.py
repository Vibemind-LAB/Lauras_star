"""Plan B / Task B5 — extract_frames (real-ffmpeg gated)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from laura.analysis.transition_review import extract_frames
from laura.ingest.ffmpeg import run_ffmpeg

pytestmark = pytest.mark.skipif(
    shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None, reason="ffmpeg"
)


def _testsrc(tmp_path: Path, secs: int) -> Path:
    p = tmp_path / "proxy.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", f"testsrc=duration={secs}:size=320x240:rate=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p),
    ])
    return p


def test_extract_frames_returns_one_jpeg_per_ref(tmp_path: Path) -> None:
    proxy = _testsrc(tmp_path, 2)
    refs = [("A", 10), ("A", 11), ("A", 12), ("A", 13)]
    blobs = extract_frames({"A": str(proxy)}, refs, rate_num=30, rate_den=1)
    assert len(blobs) == 4
    # JPEG magic bytes (FF D8) — confirms real frames came back
    assert all(b[:2] == b"\xff\xd8" for b in blobs)


def test_extract_frames_skips_missing_proxy(tmp_path: Path) -> None:
    proxy = _testsrc(tmp_path, 2)
    refs = [("A", 10), ("B", 5)]  # B has no proxy path -> skipped
    blobs = extract_frames({"A": str(proxy)}, refs, rate_num=30, rate_den=1)
    assert len(blobs) == 1
