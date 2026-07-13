"""Real-ffmpeg golden check for the zoom_hybrid render path."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from laura.render.mp4 import render_clips_mp4
from laura.render.zoom import zoom_spec_from_option

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)


def _make_source(path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "testsrc2=size=1920x1080:rate=30:duration=4",
         "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


def _probe(path: Path) -> dict[str, Any]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-show_entries", "stream=codec_type,width,height", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    result = json.loads(out)
    assert isinstance(result, dict)
    return result


def test_zoom_hybrid_e2e(tmp_path: Path) -> None:
    src = tmp_path / "src.mp4"
    _make_source(src)
    dest = tmp_path / "out.mp4"
    spec = zoom_spec_from_option(
        {"roi": {"x": 0.6, "y": 0.1, "w": 0.3, "h": 0.3}, "zoom_start_s": 0.8},
        src_w=1920, src_h=1080, out_w=1080, out_h=1920, segment_seconds=2.0,
    )
    assert spec is not None
    render_clips_mp4(
        [(src, 0, 60), (src, 60, 120)], dest,
        rate_num=30, rate_den=1, vertical=True,
        zoom_specs=[spec, None], out_size=(1080, 1920),
    )
    assert dest.is_file() and dest.stat().st_size > 0
    info = _probe(dest)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert abs(float(info["format"]["duration"]) - 4.0) < 0.25
