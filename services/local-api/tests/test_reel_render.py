"""Integration test: reel filter wiring through render_clips_mp4.

Skipped automatically when ffmpeg / ffprobe are not on PATH.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from laura.render.mp4 import render_clips_mp4

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _probe_wh(path: Path) -> tuple[int, int]:
    """Return (width, height) of the first video stream."""
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v", "quiet",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "json",
            str(path),
        ],
        text=True,
    )
    data = json.loads(out)
    stream = data["streams"][0]
    return int(stream["width"]), int(stream["height"])


# ---------------------------------------------------------------------------
# fixture: 1-second 320×240 test clip
# ---------------------------------------------------------------------------

@pytest.fixture()
def fixture_clip(tmp_path: Path) -> Path:
    fix = tmp_path / "fix.mp4"
    subprocess.check_call(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "testsrc=d=1:s=320x240:r=25",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(fix),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return fix


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_reel_vertical_dimensions(fixture_clip: Path, tmp_path: Path) -> None:
    """render_clips_mp4 with vertical=True must produce a 1080×1920 output."""
    out = tmp_path / "reel.mp4"
    render_clips_mp4(
        [(fixture_clip, 0, 25)],
        out,
        rate_num=25,
        rate_den=1,
        vertical=True,
        hook_text="Hook: 50%",
        disclosure_text="KI",
    )
    assert out.exists(), "output file was not created"
    w, h = _probe_wh(out)
    assert w == 1080, f"expected width 1080, got {w}"
    assert h == 1920, f"expected height 1920, got {h}"


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_reel_hook_with_metacharacters_renders(fixture_clip: Path, tmp_path: Path) -> None:
    """Regression: hooks with apostrophes/commas/colons/% must not break the render.

    Inline drawtext ``text='…'`` could not escape an apostrophe on Windows, so a
    benign hook like ``Geht's los, jetzt!`` failed. The ``textfile=`` approach
    reads the text verbatim, so every metacharacter is safe.
    """
    out = tmp_path / "reel_meta.mp4"
    render_clips_mp4(
        [(fixture_clip, 0, 25)],
        out,
        rate_num=25,
        rate_den=1,
        vertical=True,
        hook_text="Geht's los, jetzt: 100%!",
        disclosure_text="KI · synthetisch — don't trust",
    )
    assert out.exists(), "output file was not created"
    assert _probe_wh(out) == (1080, 1920)
    # The temp text files must be cleaned up after rendering.
    leftovers = list(tmp_path.glob("*.reel_*.txt"))
    assert leftovers == [], f"reel text files leaked: {leftovers}"
