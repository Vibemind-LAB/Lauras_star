import os
import shutil
from pathlib import Path

import pytest

from laura.ingest.ffmpeg import run_ffmpeg
from laura.render.mp4 import render_clips_mp4

pytestmark = pytest.mark.skipif(
    shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None,
    reason="ffmpeg",
)


def _clip(tmp_path: Path, name: str, secs: int) -> Path:
    p = tmp_path / name
    run_ffmpeg([
        "-f", "lavfi",
        "-i", f"testsrc=duration={secs}:size=320x240:rate=30",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        str(p),
    ])
    return p


def test_render_concats_clips(tmp_path: Path) -> None:
    a = _clip(tmp_path, "a.mp4", 1)
    b = _clip(tmp_path, "b.mp4", 1)
    out = tmp_path / "seq.mp4"
    # (src, in_frame, out_excl)
    render_clips_mp4([(a, 0, 30), (b, 0, 30)], out, rate_num=30, rate_den=1)
    assert out.exists() and out.stat().st_size > 0


def test_render_single_clip_subrange(tmp_path: Path) -> None:
    a = _clip(tmp_path, "a.mp4", 2)
    out = tmp_path / "one.mp4"
    render_clips_mp4([(a, 15, 45)], out, rate_num=30, rate_den=1)  # 0.5s..1.5s
    assert out.exists() and out.stat().st_size > 0
