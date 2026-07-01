"""Tests for multi-lane render compositing (P5 of free-multitrack spec §8 Test E).

Approach:
- Lane 0 (base): real clips + explicit opaque-black gap segments, concat chain.
- Lanes ≥ 1 (overlay): real clips only, each PTS-shifted to its absolute seq position via
  setpts=PTS-STARTPTS+<offset>/TB; absent frames (gaps) are handled by overlay=eof_action=pass.
  No color/gap segments appear in the graph for overlay lanes.

Filter-graph construction tests run without ffmpeg (pure helper tests).
Single-lane regression: existing render_clips_mp4 path unchanged.
Real 2-lane render: skipped when ffmpeg unavailable; runs otherwise and validates the output.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

import laura.render.mp4 as mp4mod
from laura.render.mp4 import (
    LaneSegment,
    build_lane_segments,
    build_multilane_filtergraph,
    render_multilane_mp4,
)

# ---------------------------------------------------------------------------
# Pure helper: build_lane_segments
# ---------------------------------------------------------------------------


def test_build_lane_segments_no_gap() -> None:
    """Single clip filling the entire sequence: no gap segments."""
    clips = [(Path("a.mp4"), 0, 30, 0, 30)]
    segs = build_lane_segments(clips, total_seq_frames=30)
    assert len(segs) == 1
    assert segs[0].path == Path("a.mp4")
    assert segs[0].src_in == 0
    assert segs[0].src_out == 30
    assert segs[0].seq_duration == 30


def test_build_lane_segments_leading_gap() -> None:
    """Clip starting at frame 10: gap [0,10) then clip [10,30)."""
    clips = [(Path("a.mp4"), 5, 25, 10, 30)]
    segs = build_lane_segments(clips, total_seq_frames=30)
    assert len(segs) == 2
    assert segs[0].path is None
    assert segs[0].seq_duration == 10
    assert segs[1].path == Path("a.mp4")
    assert segs[1].seq_duration == 20


def test_build_lane_segments_trailing_gap() -> None:
    """Clip ending at frame 20: clip [0,20) then gap [20,30)."""
    clips = [(Path("a.mp4"), 0, 20, 0, 20)]
    segs = build_lane_segments(clips, total_seq_frames=30)
    assert len(segs) == 2
    assert segs[0].path == Path("a.mp4")
    assert segs[0].seq_duration == 20
    assert segs[1].path is None
    assert segs[1].seq_duration == 10


def test_build_lane_segments_gap_between_clips() -> None:
    """Two clips with a gap between them."""
    clips = [
        (Path("a.mp4"), 0, 15, 0, 15),
        (Path("b.mp4"), 0, 15, 20, 35),
    ]
    segs = build_lane_segments(clips, total_seq_frames=35)
    assert len(segs) == 3
    assert segs[0].path == Path("a.mp4") and segs[0].seq_duration == 15
    assert segs[1].path is None and segs[1].seq_duration == 5
    assert segs[2].path == Path("b.mp4") and segs[2].seq_duration == 15


def test_build_lane_segments_all_gap() -> None:
    """Empty lane list: one big gap covering the whole sequence."""
    segs = build_lane_segments([], total_seq_frames=30)
    assert len(segs) == 1
    assert segs[0].path is None
    assert segs[0].seq_duration == 30


# ---------------------------------------------------------------------------
# Filter-graph construction tests (pure, no ffmpeg)
# ---------------------------------------------------------------------------


def _make_lane(
    clips: list[tuple[Path | None, int, int, int, int]],
) -> list[LaneSegment]:
    """Helper: build a LaneSegment list from (path_or_None, src_in, src_out, seq_in, seq_out)."""
    segs: list[LaneSegment] = []
    playhead = 0
    for path, src_in, src_out, seq_in, seq_out in clips:
        if seq_in > playhead:
            segs.append(LaneSegment(path=None, src_in=0, src_out=0, seq_duration=seq_in - playhead))
        segs.append(
            LaneSegment(path=path, src_in=src_in, src_out=src_out, seq_duration=seq_out - seq_in)
        )
        playhead = seq_out
    return segs


def _two_lane_segments() -> tuple[list[LaneSegment], list[LaneSegment]]:
    """Lane 0: one 30-frame clip [0,30). Lane 1: 10-frame gap then 20-frame clip [10,30)."""
    lane0 = [LaneSegment(path=Path("a.mp4"), src_in=0, src_out=30, seq_duration=30)]
    lane1 = [
        LaneSegment(path=None, src_in=0, src_out=0, seq_duration=10),
        LaneSegment(path=Path("b.mp4"), src_in=0, src_out=20, seq_duration=20),
    ]
    return lane0, lane1


def test_filtergraph_real_clip_inputs_ordered() -> None:
    """Real clips across lanes appear in order: lane 0 clips first, then lane 1+."""
    lane0, lane1 = _two_lane_segments()
    real_clips, fc, out_label = build_multilane_filtergraph(
        [lane0, lane1], rate_num=30, rate_den=1
    )
    # Lane 0 has 1 real clip (a.mp4), lane 1 has 1 real clip (b.mp4) — in that order.
    assert len(real_clips) == 2
    assert real_clips[0][0] == Path("a.mp4")
    assert real_clips[1][0] == Path("b.mp4")


def test_filtergraph_output_label() -> None:
    """Output video label is [vout]."""
    lane0, lane1 = _two_lane_segments()
    _, _, out_label = build_multilane_filtergraph(
        [lane0, lane1], rate_num=30, rate_den=1
    )
    assert out_label == "[vout]"


def test_filtergraph_has_overlay_filter() -> None:
    """Filter graph includes an overlay filter for compositing lane 1 over lane 0."""
    lane0, lane1 = _two_lane_segments()
    _, fc, _ = build_multilane_filtergraph([lane0, lane1], rate_num=30, rate_den=1)
    assert "overlay=eof_action=pass" in fc


def test_filtergraph_lane0_has_black_gap() -> None:
    """Gap on lane 0 uses an opaque black color source (no alpha).
    Gaps on overlay lanes are NOT represented as color segments — they are absent frames
    handled by overlay=eof_action=pass:repeatlast=0."""
    lane0 = [
        LaneSegment(path=None, src_in=0, src_out=0, seq_duration=10),  # opaque black gap
        LaneSegment(path=Path("a.mp4"), src_in=0, src_out=20, seq_duration=20),
    ]
    lane1 = [
        LaneSegment(path=None, src_in=0, src_out=0, seq_duration=15),  # overlay gap (absent)
        LaneSegment(path=Path("b.mp4"), src_in=0, src_out=15, seq_duration=15),
    ]
    _, fc, _ = build_multilane_filtergraph([lane0, lane1], rate_num=30, rate_den=1)
    parts = fc.split(";")
    # Lane-0 gap: opaque black color source (label l0s0)
    lane0_gap = next(p for p in parts if "l0s0" in p and "color" in p)
    assert "color=c=black:" in lane0_gap
    assert "@0.0" not in lane0_gap
    # Overlay gap: NO color source in the graph at all for lane-1 gaps
    assert not any("l1" in p and "color" in p for p in parts)


def test_filtergraph_overlay_clip_has_pts_offset() -> None:
    """Overlay clips use PTS offset (setpts=PTS-STARTPTS+<offset>/TB) for absolute positioning."""
    lane0, lane1 = _two_lane_segments()  # lane1 clip at seq [10,30): offset = 10/30s
    _, fc, _ = build_multilane_filtergraph([lane0, lane1], rate_num=30, rate_den=1)
    # Offset = 10 frames at 30fps = 10/30 s = 0.333333 s.
    # _seconds(10 * 1/30) = _seconds(0.333333...) ≈ "0.333333"
    assert "STARTPTS+0.333333/TB" in fc or "STARTPTS+0/TB" in fc  # 0 when at seq [0, ...)
    # More precisely for this fixture: offset = 10*1/30 = 0.333333
    assert "STARTPTS+0.333333/TB" in fc


def test_filtergraph_overlay_clip_has_yuva420p() -> None:
    """Real clip on lane ≥ 1 gets format=yuva420p conversion (alpha channel for transparency)."""
    lane0 = [LaneSegment(path=Path("a.mp4"), src_in=0, src_out=30, seq_duration=30)]
    lane1 = [LaneSegment(path=Path("b.mp4"), src_in=0, src_out=30, seq_duration=30)]
    _, fc, _ = build_multilane_filtergraph([lane0, lane1], rate_num=30, rate_den=1)
    # Overlay clip filter (for input [1:v]) must include format=yuva420p
    assert "yuva420p" in fc
    assert "[1:v]" in fc


def test_filtergraph_lane0_clip_no_yuva() -> None:
    """Lane-0 real clips must NOT have yuva420p (opaque base, no alpha needed)."""
    lane0 = [LaneSegment(path=Path("a.mp4"), src_in=0, src_out=30, seq_duration=30)]
    lane1 = [LaneSegment(path=Path("b.mp4"), src_in=0, src_out=30, seq_duration=30)]
    _, fc, _ = build_multilane_filtergraph([lane0, lane1], rate_num=30, rate_den=1)
    parts = fc.split(";")
    # Lane-0 clip: [0:v] trim, must NOT have yuva420p
    lane0_clip = next(p for p in parts if "[0:v]" in p and "trim=start_frame" in p)
    assert "yuva420p" not in lane0_clip


def test_filtergraph_three_lanes_two_overlays() -> None:
    """Three lanes (lane 0 + 2 overlay lanes) produce two chained overlay filters."""
    lane0 = [LaneSegment(path=Path("a.mp4"), src_in=0, src_out=30, seq_duration=30)]
    lane1 = [LaneSegment(path=Path("b.mp4"), src_in=0, src_out=30, seq_duration=30)]
    lane2 = [LaneSegment(path=Path("c.mp4"), src_in=0, src_out=30, seq_duration=30)]
    _, fc, out_label = build_multilane_filtergraph(
        [lane0, lane1, lane2], rate_num=30, rate_den=1
    )
    assert fc.count("overlay=eof_action=pass") == 2
    assert out_label == "[vout]"


def test_filtergraph_frame_exact_trim() -> None:
    """Clip trim uses exact integer start_frame/end_frame, never float seconds for trim."""
    lane0 = [LaneSegment(path=Path("a.mp4"), src_in=15, src_out=45, seq_duration=30)]
    lane1 = [
        LaneSegment(path=Path("b.mp4"), src_in=5, src_out=20, seq_duration=15),
        LaneSegment(path=None, src_in=0, src_out=0, seq_duration=15),
    ]
    _, fc, _ = build_multilane_filtergraph([lane0, lane1], rate_num=30, rate_den=1)
    assert "trim=start_frame=15:end_frame=45" in fc
    assert "trim=start_frame=5:end_frame=20" in fc
    # trim filter must not use :end= (float-second form)
    assert ":end=" not in fc


def test_filtergraph_base_input_offset() -> None:
    """base_input_offset shifts all real-clip input indices in the filter."""
    lane0 = [LaneSegment(path=Path("a.mp4"), src_in=0, src_out=30, seq_duration=30)]
    lane1 = [LaneSegment(path=Path("b.mp4"), src_in=0, src_out=30, seq_duration=30)]
    _, fc_zero, _ = build_multilane_filtergraph(
        [lane0, lane1], rate_num=30, rate_den=1, base_input_offset=0
    )
    _, fc_two, _ = build_multilane_filtergraph(
        [lane0, lane1], rate_num=30, rate_den=1, base_input_offset=2
    )
    assert "[0:v]" in fc_zero and "[1:v]" in fc_zero
    assert "[2:v]" in fc_two and "[3:v]" in fc_two


def test_filtergraph_overlay_repeatlast_zero() -> None:
    """Overlay filter uses repeatlast=0 so base passes through when overlay has no frames."""
    lane0, lane1 = _two_lane_segments()
    _, fc, _ = build_multilane_filtergraph([lane0, lane1], rate_num=30, rate_den=1)
    assert "repeatlast=0" in fc


def test_filtergraph_output_is_yuv420p() -> None:
    """Final overlay output is converted to yuv420p (no alpha — encoder-safe)."""
    lane0, lane1 = _two_lane_segments()
    _, fc, _ = build_multilane_filtergraph([lane0, lane1], rate_num=30, rate_den=1)
    # The overlay output should have format=yuv420p (not yuva)
    assert "format=yuv420p" in fc


# ---------------------------------------------------------------------------
# Single-lane regression: render_clips_mp4 path unaffected
# ---------------------------------------------------------------------------


def test_single_lane_render_unchanged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """render_clips_mp4 with 2 lane-0 clips: filter graph is byte-identical to pre-P5 behavior."""
    captured: dict[str, list[str]] = {}

    def fake_run_ffmpeg(args: list[str], **kw: object) -> None:
        captured["args"] = args

    monkeypatch.setattr(mp4mod, "run_ffmpeg", fake_run_ffmpeg)

    dest = tmp_path / "out.mp4"
    mp4mod.render_clips_mp4(
        [(Path("a.mp4"), 0, 30), (Path("b.mp4"), 0, 30)],
        dest,
        rate_num=30,
        rate_den=1,
    )

    fc = captured["args"][captured["args"].index("-filter_complex") + 1]
    # Must use concat (not overlay), byte-identical to pre-P5
    assert "concat=n=2:v=1:a=0" in fc
    assert "overlay" not in fc
    assert "yuva420p" not in fc


# ---------------------------------------------------------------------------
# render_multilane_mp4 wiring test (mocked ffmpeg)
# ---------------------------------------------------------------------------


def test_render_multilane_builds_overlay_graph(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """render_multilane_mp4 wires -filter_complex with overlay stack and correct inputs."""
    captured: dict[str, list[str]] = {}

    def fake_run_ffmpeg(args: list[str], **kw: object) -> None:
        captured["args"] = args

    monkeypatch.setattr(mp4mod, "run_ffmpeg", fake_run_ffmpeg)

    dest = tmp_path / "out.mp4"
    # Lane 0: one 30-frame clip at seq [0,30)
    # Lane 1: one 20-frame clip at seq [10,30) — 10-frame leading gap (absent frames)
    lane0 = [(Path("a.mp4"), 0, 30, 0, 30)]
    lane1 = [(Path("b.mp4"), 0, 20, 10, 30)]
    mp4mod.render_multilane_mp4(
        [lane0, lane1],
        dest,
        rate_num=30,
        rate_den=1,
    )

    args = captured["args"]
    fc = args[args.index("-filter_complex") + 1]

    # Contains overlay with eof_action=pass
    assert "overlay=eof_action=pass" in fc
    # Overlay clip has yuva420p
    assert "yuva420p" in fc
    # Lane-1 clip has PTS offset for seq_in=10 frames at 30fps
    assert "STARTPTS+0.333333/TB" in fc
    # Frame-exact trims
    assert "trim=start_frame=0:end_frame=30" in fc
    assert "trim=start_frame=0:end_frame=20" in fc
    # Output is mapped correctly
    assert "-map" in args
    assert "[out]" in args


# ---------------------------------------------------------------------------
# Real 2-lane render (Test E from spec §8) — skipped when ffmpeg unavailable
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None,
    reason="ffmpeg not available — real 2-lane render skipped (Test E)",
)
def test_real_two_lane_render_produces_valid_mp4(tmp_path: Path) -> None:
    """End-to-end 2-lane composite render via ffmpeg. Validates output mp4 with ffprobe.

    Lane 0: 30-frame lavfi test source (opaque base).
    Lane 1: 20-frame lavfi source overlaid at seq [10,30) — 10-frame leading absent frames.
    Expected output: valid mp4 with ~30 video frames (full sequence length).
    """
    from laura.ingest.ffmpeg import probe, run_ffmpeg

    # Build lane-0 source: 1 second, 30 fps, 320x240
    src_a = tmp_path / "a.mp4"
    run_ffmpeg([
        "-f", "lavfi",
        "-i", "testsrc=duration=1:size=320x240:rate=30",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        str(src_a),
    ])

    # Build lane-1 source: solid blue, same size/rate
    src_b = tmp_path / "b.mp4"
    run_ffmpeg([
        "-f", "lavfi",
        "-i", "color=c=blue:size=320x240:rate=30:duration=1",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        str(src_b),
    ])

    out = tmp_path / "composite.mp4"

    # Lane 0: clip covers src [0,30), seq [0,30)
    # Lane 1: clip covers src [0,20), seq [10,30)  — 10-frame leading gap (absent frames)
    lane0 = [(src_a, 0, 30, 0, 30)]
    lane1 = [(src_b, 0, 20, 10, 30)]

    render_multilane_mp4(
        [lane0, lane1],
        out,
        rate_num=30,
        rate_den=1,
    )

    assert out.exists(), "output mp4 was not created"
    assert out.stat().st_size > 0, "output mp4 is empty"

    # Validate with ffprobe
    data = probe(out)
    streams = data.get("streams", [])
    video_streams = [s for s in streams if isinstance(s, dict) and s.get("codec_type") == "video"]
    assert video_streams, "output mp4 has no video stream"

    vstream = video_streams[0]
    nb_raw = vstream.get("nb_frames")
    if nb_raw not in (None, "N/A"):
        nb = int(str(nb_raw))
        # Should be ~30 frames (full sequence length = max seq_out = 30)
        assert abs(nb - 30) <= 2, f"expected ~30 frames, got {nb}"
