"""render_clips_mp4 zoom_specs wiring — validation without invoking ffmpeg."""

from pathlib import Path

import pytest

from laura.render.mp4 import VideoTransition, render_clips_mp4
from laura.render.zoom import ZoomSpec


def _spec() -> ZoomSpec:
    return ZoomSpec(end_win=(1200, 0, 606, 1080), start_win=(657, 0, 606, 1080),
                    zoom_start_s=1.0, transition_s=0.6)


def test_zoom_specs_length_mismatch_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="zoom_specs"):
        render_clips_mp4(
            [(tmp_path / "a.mp4", 0, 60), (tmp_path / "a.mp4", 60, 120)],
            tmp_path / "out.mp4", rate_num=30, rate_den=1,
            vertical=True, zoom_specs=[_spec()],
        )


def test_zoom_requires_vertical(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="vertical"):
        render_clips_mp4(
            [(tmp_path / "a.mp4", 0, 60)], tmp_path / "out.mp4",
            rate_num=30, rate_den=1, vertical=False, zoom_specs=[_spec()],
        )


def test_zoom_excludes_video_transitions(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="transitions"):
        render_clips_mp4(
            [(tmp_path / "a.mp4", 0, 60), (tmp_path / "a.mp4", 60, 120)],
            tmp_path / "out.mp4", rate_num=30, rate_den=1, vertical=True,
            zoom_specs=[_spec(), None],
            video_transitions=[
                VideoTransition(kind="crossfade", boundary_frame=0, duration_frames=12)
            ],
        )
