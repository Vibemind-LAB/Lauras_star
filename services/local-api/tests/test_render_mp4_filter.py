"""Unit test: the render filter graph trims by exact integer frames, never float seconds.

Frame-accuracy invariant: trims must be end-exclusive integer frame indices. This test
monkeypatches the ffmpeg call, so it runs without the ffmpeg binary and asserts on the
generated ``-filter_complex`` string directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from laura.render import mp4


def test_render_filter_uses_exact_end_exclusive_frames(
    monkeypatch: Any, tmp_path: Path
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run_ffmpeg(args: list[str]) -> None:
        captured["args"] = args

    monkeypatch.setattr(mp4, "run_ffmpeg", fake_run_ffmpeg)

    dest = tmp_path / "out.mp4"
    mp4.render_clips_mp4(
        [(Path("a.mp4"), 15, 45), (Path("b.mp4"), 0, 30)],
        dest,
        rate_num=30000,
        rate_den=1001,  # 29.97 — exactly the rate where float seconds drift
    )

    args = captured["args"]
    fc = args[args.index("-filter_complex") + 1]

    # Frame-exact, end-exclusive: trim keeps input frames [start_frame, end_frame).
    assert "trim=start_frame=15:end_frame=45" in fc
    assert "trim=start_frame=0:end_frame=30" in fc
    # The old float-seconds form (trim=start=..:end=..) must be gone entirely.
    assert "start=" not in fc
    assert ":end=" not in fc


def test_render_filter_applies_dip_to_black_transition(
    monkeypatch: Any, tmp_path: Path
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run_ffmpeg(args: list[str]) -> None:
        captured["args"] = args

    monkeypatch.setattr(mp4, "run_ffmpeg", fake_run_ffmpeg)

    dest = tmp_path / "out.mp4"
    mp4.render_clips_mp4(
        [(Path("a.mp4"), 0, 30), (Path("b.mp4"), 0, 30)],
        dest,
        rate_num=30,
        rate_den=1,
        video_transitions=[
            mp4.VideoTransition(kind="dip_black", boundary_frame=30, duration_frames=12)
        ],
    )

    args = captured["args"]
    fc = args[args.index("-filter_complex") + 1]

    assert "fade=t=out:st=0.6:d=0.4" in fc
    assert "fade=t=in:st=1:d=0.4" in fc
