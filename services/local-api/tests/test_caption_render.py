"""Integration test: burned-in ASS caption track via render_clips_mp4 (R1.2).

Skipped automatically when ffmpeg / ffprobe are not on PATH, or when the
ffmpeg build was compiled without libass (the ``ass`` filter won't appear in
``ffmpeg -hide_banner -filters``).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from laura.render.captions import build_ass
from laura.render.mp4 import render_clips_mp4

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _have_libass() -> bool:
    """Return True when ffmpeg was built with libass (the ``ass`` filter)."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"],
        capture_output=True,
        text=True,
    )
    # The filter list has lines like " ... ass  V->V  ..."
    # We check for the token " ass " (with surrounding spaces) to avoid
    # matching unrelated names such as "bassboost".
    return " ass " in result.stdout or " ass " in result.stderr


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
# skip markers — evaluated once at collection time
# ---------------------------------------------------------------------------

_skip_no_ffmpeg = pytest.mark.skipif(
    not _have_ffmpeg(),
    reason="ffmpeg/ffprobe not on PATH",
)

# Only evaluate _have_libass when ffmpeg is actually available.
def _skip_no_libass() -> pytest.MarkDecorator:
    if not _have_ffmpeg():
        # Already covered by _skip_no_ffmpeg; return a truthy skip.
        return pytest.mark.skip(reason="ffmpeg/ffprobe not on PATH")
    if not _have_libass():
        return pytest.mark.skip(reason="ffmpeg without libass")
    return pytest.mark.skipif(False, reason="")  # no-op — libass present


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


@_skip_no_ffmpeg
def test_caption_render_vertical(fixture_clip: Path, tmp_path: Path) -> None:
    """Captions + vertical=True must produce a 1080×1920 output and clean up temp files."""
    if not _have_ffmpeg():
        pytest.skip("ffmpeg/ffprobe not on PATH")
    if not _have_libass():
        pytest.skip("ffmpeg without libass")

    out = tmp_path / "reel_caption.mp4"
    ass = build_ass(
        [[("Hallo", 0, 15), ("Welt", 15, 30)]],
        rate_num=25,
        rate_den=1,
    )
    render_clips_mp4(
        [(fixture_clip, 0, 25)],
        out,
        rate_num=25,
        rate_den=1,
        vertical=True,
        caption_ass=ass,
    )
    assert out.exists(), "output file was not created"
    w, h = _probe_wh(out)
    assert w == 1080, f"expected width 1080, got {w}"
    assert h == 1920, f"expected height 1920, got {h}"

    # Temp ASS and txt files must be cleaned up.
    leftover_ass = list(tmp_path.glob("*.reel_*.ass"))
    leftover_txt = list(tmp_path.glob("*.reel_*.txt"))
    assert leftover_ass == [], f"reel ASS files leaked: {leftover_ass}"
    assert leftover_txt == [], f"reel txt files leaked: {leftover_txt}"


@_skip_no_ffmpeg
def test_caption_render_no_vertical(fixture_clip: Path, tmp_path: Path) -> None:
    """Captions WITHOUT vertical/hook (captions-only [vcat] branch) must still render."""
    if not _have_ffmpeg():
        pytest.skip("ffmpeg/ffprobe not on PATH")
    if not _have_libass():
        pytest.skip("ffmpeg without libass")

    out = tmp_path / "caption_only.mp4"
    ass = build_ass(
        [[("Hallo", 0, 15), ("Welt", 15, 30)]],
        rate_num=25,
        rate_den=1,
    )
    render_clips_mp4(
        [(fixture_clip, 0, 25)],
        out,
        rate_num=25,
        rate_den=1,
        vertical=False,
        caption_ass=ass,
    )
    assert out.exists(), "output file was not created (captions-only branch)"

    # Temp ASS files must be cleaned up.
    leftover_ass = list(tmp_path.glob("*.reel_*.ass"))
    leftover_txt = list(tmp_path.glob("*.reel_*.txt"))
    assert leftover_ass == [], f"reel ASS files leaked: {leftover_ass}"
    assert leftover_txt == [], f"reel txt files leaked: {leftover_txt}"
