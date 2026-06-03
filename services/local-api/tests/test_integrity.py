"""Integrity verification with REAL ffmpeg. Skipped if ffmpeg is unavailable."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from laura.ingest.ffmpeg import run_ffmpeg
from laura.ingest.integrity import is_media_file, verify_decode

pytestmark = pytest.mark.skipif(
    shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None,
    reason="ffmpeg not available on PATH",
)


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    out = tmp_path / "sample.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
    ])
    return out


def test_clean_file_passes(sample: Path) -> None:
    report = verify_decode(sample)
    assert report.ok is True
    assert report.container_ok is True
    assert report.decode_errors == 0


def test_truncated_file_is_flagged(sample: Path, tmp_path: Path) -> None:
    truncated = tmp_path / "broken.mp4"
    data = sample.read_bytes()
    truncated.write_bytes(data[: len(data) // 2])  # chop the file in half
    report = verify_decode(truncated)
    assert report.ok is False


def test_skip_decode_scan_only_checks_container(sample: Path) -> None:
    report = verify_decode(sample, full_scan=False)
    assert report.ok is True
    assert "skipped" in report.detail


def test_is_media_file_true_for_real_media(sample: Path) -> None:
    assert is_media_file(sample) is True


def test_is_media_file_false_for_text(tmp_path: Path) -> None:
    junk = tmp_path / "notes.txt"
    junk.write_text("just some text", encoding="utf-8")
    assert is_media_file(junk) is False
