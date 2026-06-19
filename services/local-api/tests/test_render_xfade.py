"""Plan A / Task 3 — real xfade/acrossfade crossfade with reserve overlap.

Pure-helper + mocked-render string assertions run without ffmpeg; one real render is gated
on ffmpeg being present and checks the assembled length is preserved (sync invariant)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

import laura.render.mp4 as mp4mod
from laura.ingest.ffmpeg import probe, run_ffmpeg
from laura.render.mp4 import (
    VideoTransition,
    _crossfade_durations,
    _xfade_base_graph,
    render_clips_mp4,
)

# --- pure helpers (no ffmpeg) ------------------------------------------------

def test_xfade_graph_builds_dissolve_with_reserve() -> None:
    clips = [(Path("a.mp4"), 0, 30), (Path("b.mp4"), 0, 30)]
    parts, v_label, a_label = _xfade_base_graph(
        clips, xdur=[6], audio_flags=[False, False], has_base_audio=False,
        rate_num=30, rate_den=1,
    )
    joined = ";".join(parts)
    assert "end_frame=36" in joined  # clip A extended by the 6-frame reserve
    assert "xfade=transition=fade:duration=0.2:offset=1" in joined  # 6/30=0.2s, offset=30/30=1s
    assert a_label is None and v_label.startswith("xv")


def test_xfade_graph_hard_boundary_uses_concat() -> None:
    clips = [(Path("a.mp4"), 0, 30), (Path("b.mp4"), 0, 30)]
    parts, _v, _a = _xfade_base_graph(
        clips, xdur=[0], audio_flags=[False, False], has_base_audio=False,
        rate_num=30, rate_den=1,
    )
    joined = ";".join(parts)
    assert "concat=n=2:v=1:a=0" in joined and "xfade" not in joined
    assert "end_frame=30" in joined  # no reserve added on a hard boundary


def test_xfade_graph_with_audio_uses_acrossfade() -> None:
    clips = [(Path("a.mp4"), 0, 30), (Path("b.mp4"), 0, 30)]
    parts, _v, a_label = _xfade_base_graph(
        clips, xdur=[6], audio_flags=[True, True], has_base_audio=True,
        rate_num=30, rate_den=1,
    )
    joined = ";".join(parts)
    assert "acrossfade=d=0.2" in joined and a_label is not None


def test_crossfade_durations_clamp_to_reserve() -> None:
    clips = [(Path("a.mp4"), 0, 30), (Path("b.mp4"), 0, 30)]
    tr = [VideoTransition(kind="crossfade", boundary_frame=30, duration_frames=6)]
    # plenty of reserve -> full 6
    assert _crossfade_durations(clips, tr, source_frames=lambda p: 60) == [6]
    # only 2 frames of reserve -> shortened to 2
    assert _crossfade_durations(clips, tr, source_frames=lambda p: 32) == [2]
    # no reserve -> degrades to hard cut
    assert _crossfade_durations(clips, tr, source_frames=lambda p: 30) == [0]


def test_crossfade_durations_only_marks_matching_boundary() -> None:
    clips = [(Path("a"), 0, 30), (Path("b"), 0, 30), (Path("c"), 0, 30)]
    tr = [VideoTransition(kind="crossfade", boundary_frame=60, duration_frames=8)]
    # boundary 60 is AFTER clip 1 (cumulative 30+30), so durations == [0, 8]
    assert _crossfade_durations(clips, tr, source_frames=lambda p: 200) == [0, 8]


# --- render wiring (mocked ffmpeg) ------------------------------------------

def _patch(monkeypatch, *, nb_frames: str = "1000") -> list[list[str]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(mp4mod, "run_ffmpeg", lambda args, **kw: calls.append(list(args)))
    monkeypatch.setattr(
        mp4mod,
        "probe",
        lambda p: {
            "streams": [{"codec_type": "video", "nb_frames": nb_frames}],
            "format": {"duration": "33"},
        },
    )
    return calls


def _fc(args: list[str]) -> str:
    return args[args.index("-filter_complex") + 1]


def test_render_hard_only_is_concat_no_xfade(monkeypatch, tmp_path: Path) -> None:
    calls = _patch(monkeypatch)
    render_clips_mp4(
        [(tmp_path / "a.mp4", 0, 30), (tmp_path / "b.mp4", 0, 30)],
        tmp_path / "o.mp4", rate_num=30, rate_den=1,
    )
    fc = _fc(calls[-1])
    assert "concat=n=2:v=1" in fc and "xfade" not in fc


def test_render_crossfade_builds_xfade(monkeypatch, tmp_path: Path) -> None:
    calls = _patch(monkeypatch)
    tr = [VideoTransition(kind="crossfade", boundary_frame=30, duration_frames=6)]
    render_clips_mp4(
        [(tmp_path / "a.mp4", 0, 30), (tmp_path / "b.mp4", 0, 30)],
        tmp_path / "o.mp4", rate_num=30, rate_den=1, video_transitions=tr,
    )
    fc = _fc(calls[-1])
    assert "xfade=transition=fade:duration=0.2:offset=1" in fc
    assert "end_frame=36" in fc


def test_render_crossfade_without_reserve_falls_back_to_hard(monkeypatch, tmp_path: Path) -> None:
    calls = _patch(monkeypatch, nb_frames="30")  # clip ends at 30, source has 30 -> no reserve
    tr = [VideoTransition(kind="crossfade", boundary_frame=30, duration_frames=6)]
    render_clips_mp4(
        [(tmp_path / "a.mp4", 0, 30), (tmp_path / "b.mp4", 0, 30)],
        tmp_path / "o.mp4", rate_num=30, rate_den=1, video_transitions=tr,
    )
    fc = _fc(calls[-1])
    assert "xfade" not in fc and "concat=n=2:v=1" in fc


# --- real ffmpeg smoke -------------------------------------------------------

@pytest.mark.skipif(
    shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None, reason="ffmpeg"
)
def test_real_crossfade_preserves_total_length(tmp_path: Path) -> None:
    def _src(name: str, secs: int) -> Path:
        p = tmp_path / name
        run_ffmpeg([
            "-f", "lavfi", "-i", f"testsrc=duration={secs}:size=320x240:rate=30",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p),
        ])
        return p

    a, b = _src("a.mp4", 2), _src("b.mp4", 2)  # 60 source frames each -> 30 reserve avail
    out = tmp_path / "xf.mp4"
    tr = [VideoTransition(kind="crossfade", boundary_frame=30, duration_frames=6)]
    render_clips_mp4([(a, 0, 30), (b, 0, 30)], out, rate_num=30, rate_den=1, video_transitions=tr)
    assert out.exists() and out.stat().st_size > 0
    streams = probe(out).get("streams", [])
    vstream = next(s for s in streams if s.get("codec_type") == "video")
    nb = int(vstream["nb_frames"])
    assert abs(nb - 60) <= 1  # 30 + 30, reserve overlap keeps the sum (sync invariant)
