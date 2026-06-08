"""Real-ffmpeg integration tests for the music-mix path of render_clips_mp4."""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from laura.ingest.ffmpeg import run_ffmpeg
from laura.render.mp4 import render_clips_mp4

pytestmark = pytest.mark.skipif(
    shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None, reason="ffmpeg"
)


def _video(tmp_path, secs):
    p = tmp_path / "v.mp4"
    run_ffmpeg([
        "-f", "lavfi",
        "-i", f"testsrc=duration={secs}:size=320x240:rate=30",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        str(p),
    ])
    return p


def _audio(tmp_path, name, secs):
    p = tmp_path / name
    run_ffmpeg([
        "-f", "lavfi",
        "-i", f"sine=frequency=440:duration={secs}",
        "-c:a", "aac",
        str(p),
    ])
    return p


def _has_audio(path) -> bool:
    ffprobe = os.environ.get("LAURA_FFPROBE", "ffprobe")
    out = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    )
    return "audio" in out.stdout


def test_render_with_music_has_audio_stream(tmp_path):
    v = _video(tmp_path, 2)
    m = _audio(tmp_path, "m.m4a", 5)
    out = tmp_path / "out.mp4"
    # music_tracks: (path, seq_in_frame, seq_out_frame_exclusive, gain_percent)
    render_clips_mp4([(v, 0, 30)], out, rate_num=30, rate_den=1, music_tracks=[(m, 0, 30, 100)])
    assert out.exists() and _has_audio(out)


def test_render_without_music_has_no_audio(tmp_path):
    v = _video(tmp_path, 1)
    out = tmp_path / "out2.mp4"
    render_clips_mp4([(v, 0, 30)], out, rate_num=30, rate_den=1)
    assert out.exists() and not _has_audio(out)


def test_render_music_at_nonzero_offset_has_audio(tmp_path):
    """A music track with a non-zero seq_in_frame (offset scene) still produces audio."""
    v = _video(tmp_path, 3)
    m = _audio(tmp_path, "m2.m4a", 5)
    out = tmp_path / "out3.mp4"
    # Two "scenes": first 30 frames have no music, frames 30-60 have music.
    render_clips_mp4(
        [(v, 0, 60)], out, rate_num=30, rate_den=1,
        music_tracks=[(m, 30, 60, 80)],
    )
    assert out.exists() and _has_audio(out)
