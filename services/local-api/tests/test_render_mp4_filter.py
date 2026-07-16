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


def test_short_filtergraph_rides_the_command_line(monkeypatch: Any, tmp_path: Path) -> None:
    """A small cut keeps the graph inline — no helper file, byte-identical command."""
    captured: dict[str, list[str]] = {}

    def fake_run_ffmpeg(args: list[str]) -> None:
        captured["args"] = args

    monkeypatch.setattr(mp4, "run_ffmpeg", fake_run_ffmpeg)

    mp4.render_clips_mp4(
        [(Path("a.mp4"), 0, 30), (Path("b.mp4"), 0, 30)],
        tmp_path / "out.mp4",
        rate_num=30,
        rate_den=1,
    )

    args = captured["args"]
    assert "-filter_complex" in args
    assert "-filter_complex_script" not in args


def test_long_filtergraph_is_handed_over_as_a_file(monkeypatch: Any, tmp_path: Path) -> None:
    """A long cut's graph must NOT ride the command line.

    Windows caps a command line near 32k chars; past that CreateProcess fails with
    WinError 206, which ``run_ffmpeg`` surfaces as a misleading "ffmpeg not found".
    Live finding: a 55-segment multi-window cut (3-minute long-form) rendered fine as
    13 segments and died at 55. ffmpeg reads the graph from ``-filter_complex_script``.
    """
    captured: dict[str, Any] = {}

    def fake_run_ffmpeg(args: list[str]) -> None:
        captured["args"] = args
        if "-filter_complex_script" in args:
            path = Path(args[args.index("-filter_complex_script") + 1])
            # read INSIDE the call — the file is cleaned up when the render returns
            captured["graph"] = path.read_text(encoding="utf-8")

    monkeypatch.setattr(mp4, "run_ffmpeg", fake_run_ffmpeg)

    clips = [(Path(f"clip{i}.mp4"), 0, 30) for i in range(120)]
    mp4.render_clips_mp4(clips, tmp_path / "out.mp4", rate_num=30, rate_den=1)

    args = captured["args"]
    assert "-filter_complex_script" in args, "long graph must go to a file"
    assert "-filter_complex" not in args, "long graph must not also ride the command line"
    # The graph itself is unchanged — only how it reaches ffmpeg differs.
    assert "trim=start_frame=0:end_frame=30" in captured["graph"]
    assert len(captured["graph"]) > mp4._MAX_INLINE_FILTER_CHARS
    # The helper file must not survive the render.
    assert not list(tmp_path.glob("*.filtergraph.txt"))
