"""batch_shot_metrics must be metric-IDENTICAL to per-shot compute_shot_metrics — it only changes
the decode strategy (one O(N) pass instead of O(N²) per-shot select-from-0), never the sampled
frames, so keep/drop decisions are unchanged."""

from __future__ import annotations

from pathlib import Path

import pytest

from laura.analysis.quality import batch_shot_metrics, compute_shot_metrics
from laura.ingest.ffmpeg import run_ffmpeg


@pytest.fixture
def motion_video(tmp_path: Path) -> Path:
    """A 4s/120-frame clip with real motion (testsrc) so the metrics vary frame-to-frame."""
    out = tmp_path / "v.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
    ])
    return out


def test_batch_matches_per_shot(motion_video: Path) -> None:
    shots = [(0, 30), (30, 75), (75, 120)]
    batch = batch_shot_metrics(motion_video, shots)
    assert len(batch) == len(shots)
    for (src_in, src_out), bm in zip(shots, batch, strict=True):
        pm = compute_shot_metrics(motion_video, src_in, src_out)
        assert bm == pm, f"shot ({src_in},{src_out}): batch {bm} != per-shot {pm}"


def test_batch_single_short_shot_matches(motion_video: Path) -> None:
    # A shot shorter than SAMPLE_K frames (fewer samples) must still match per-shot.
    batch = batch_shot_metrics(motion_video, [(10, 13)])
    assert batch == [compute_shot_metrics(motion_video, 10, 13)]


def test_batch_empty_shots() -> None:
    assert batch_shot_metrics(Path("nonexistent.mp4"), []) == []
