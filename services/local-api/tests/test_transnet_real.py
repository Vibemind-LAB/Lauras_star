"""Real TransNetV2 shot detection (optional ``[scene-ml]`` extra).

Synthesizes a clip with a hard cut and runs the learned detector on it, asserting it finds
a boundary near the cut and returns end-exclusive ``ShotResult`` ranges. SKIPPED whenever
``transnetv2-pytorch`` or ffmpeg is absent (e.g. CI / the default dev env), so it never
blocks the default suite — same guard style as ``test_asr_real.py``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

transnet = pytest.importorskip("transnetv2_pytorch")

from laura.analysis.shots import detect_shots  # noqa: E402
from laura.analysis.transnet import detect_shots_transnet  # noqa: E402
from laura.ingest.ffmpeg import run_ffmpeg  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None,
    reason="needs ffmpeg to synthesize the test clip",
)


@pytest.fixture
def cut_clip(tmp_path: Path) -> Path:
    """A 2s clip with a hard cut at frame 30 (1s testsrc -> 1s solid blue)."""
    out = tmp_path / "cut.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=30",
        "-f", "lavfi", "-i", "color=c=blue:duration=1:size=320x240:rate=30",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
    ])
    return out


def test_real_transnet_detects_cut(cut_clip: Path) -> None:
    shots = detect_shots_transnet(cut_clip)
    assert shots, "expected at least one shot"
    assert all(s.method == "transnetv2" for s in shots)
    assert all(s.src_out_frame_exclusive > s.src_in_frame for s in shots)
    boundaries = {s.src_in_frame for s in shots} | {s.src_out_frame_exclusive for s in shots}
    assert any(abs(b - 30) <= 3 for b in boundaries), boundaries


def test_real_transnet_via_detect_shots(cut_clip: Path) -> None:
    shots = detect_shots(cut_clip, detector="transnet")
    assert shots and all(s.method == "transnetv2" for s in shots)
