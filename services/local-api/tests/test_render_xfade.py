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

def _patch(monkeypatch: pytest.MonkeyPatch, *, nb_frames: str = "1000") -> list[list[str]]:
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


def test_render_hard_only_is_concat_no_xfade(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _patch(monkeypatch)
    render_clips_mp4(
        [(tmp_path / "a.mp4", 0, 30), (tmp_path / "b.mp4", 0, 30)],
        tmp_path / "o.mp4", rate_num=30, rate_den=1,
    )
    fc = _fc(calls[-1])
    assert "concat=n=2:v=1" in fc and "xfade" not in fc


def test_render_crossfade_builds_xfade(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = _patch(monkeypatch)
    tr = [VideoTransition(kind="crossfade", boundary_frame=30, duration_frames=6)]
    render_clips_mp4(
        [(tmp_path / "a.mp4", 0, 30), (tmp_path / "b.mp4", 0, 30)],
        tmp_path / "o.mp4", rate_num=30, rate_den=1, video_transitions=tr,
    )
    fc = _fc(calls[-1])
    assert "xfade=transition=fade:duration=0.2:offset=1" in fc
    assert "end_frame=36" in fc


def test_render_crossfade_without_reserve_falls_back_to_hard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _patch(monkeypatch, nb_frames="30")  # clip ends at 30, source has 30 -> no reserve
    tr = [VideoTransition(kind="crossfade", boundary_frame=30, duration_frames=6)]
    render_clips_mp4(
        [(tmp_path / "a.mp4", 0, 30), (tmp_path / "b.mp4", 0, 30)],
        tmp_path / "o.mp4", rate_num=30, rate_den=1, video_transitions=tr,
    )
    fc = _fc(calls[-1])
    assert "xfade" not in fc and "concat=n=2:v=1" in fc


def test_render_degraded_crossfade_emits_no_dip_to_black(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A degraded crossfade (no post-cut reserve -> ``_crossfade_durations`` clamps it to 0 ->
    ``has_crossfade`` False -> concat path) must fall through as a silent hard cut. Before the
    fix, the concat path passed the UNFILTERED ``video_transitions`` list into
    ``_video_transition_chain``, which only skips ``kind=="hard"`` -- the degraded boundary
    still carries ``kind="crossfade"`` with its original (unclamped) duration, so it still
    emitted a dip-to-black ``fade=t=out``/``fade=t=in`` pair. (A mid-timeline pair like this one
    blacks the ENTIRE assembled stream, not just the boundary's neighbourhood -- see the
    real-ffmpeg pixel test below for the measured proof.)"""
    calls = _patch(monkeypatch, nb_frames="30")  # clip ends at 30, source has 30 -> no reserve
    tr = [VideoTransition(kind="crossfade", boundary_frame=30, duration_frames=8)]
    render_clips_mp4(
        [(tmp_path / "a.mp4", 0, 30), (tmp_path / "b.mp4", 0, 30)],
        tmp_path / "o.mp4", rate_num=30, rate_den=1, video_transitions=tr,
    )
    fc = _fc(calls[-1])
    assert "concat=n=2:v=1" in fc and "xfade" not in fc
    assert "fade=t=" not in fc, f"degraded crossfade must not leave a dip-to-black filter: {fc}"


def test_render_xfade_path_also_applies_trailing_fade(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """I-1 reader gap 2: a narrated reel with a real crossfade boundary (so the xfade fold
    path is taken) AND a trailing "fade" on the last clip must still render the fade-out --
    the xfade fold only handles kind=="crossfade" boundaries; the trailing fade is a
    separate, coexisting transition that must not be silently dropped."""
    calls = _patch(monkeypatch)
    tr = [
        VideoTransition(kind="crossfade", boundary_frame=30, duration_frames=6),
        VideoTransition(kind="fade", boundary_frame=60, duration_frames=12),
    ]
    render_clips_mp4(
        [(tmp_path / "a.mp4", 0, 30), (tmp_path / "b.mp4", 0, 30)],
        tmp_path / "o.mp4", rate_num=30, rate_den=1, video_transitions=tr,
    )
    fc = _fc(calls[-1])
    # the crossfade boundary still folds into xfade (unaffected by the fade addition)
    assert "xfade=transition=fade:duration=0.2:offset=1" in fc
    # AND the trailing fade-out reaches the graph: boundary=60f/30fps=2s, d=12f/30fps=0.4s
    assert "fade=t=out:st=1.6:d=0.4" in fc
    # the fade-in half must NOT be emitted for a trailing fade (boundary_frame(60) >=
    # total_frames(60)): ffmpeg's fade=t=in forces EVERY frame before its st to black, not just
    # frames within its own window -- with no video past the boundary for a ramp-up to apply to,
    # emitting it would black out the entire preceding stream (live-found regression).
    assert "fade=t=in" not in fc


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


def _white_src(tmp_path: Path, name: str, *, seconds: float, size: str = "64x64",
                rate: int = 10) -> Path:
    p = tmp_path / name
    run_ffmpeg([
        "-f", "lavfi", "-i", f"color=c=white:s={size}:r={rate}:d={seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p),
    ])
    return p


def _yavg_series(path: Path, stats_path: Path) -> list[float]:
    """Per-frame average luma (signalstats ``YAVG``) of ``path``'s video stream, in order.

    Writes the metadata to a bare relative filename via ``run_ffmpeg``'s own ``cwd=`` support
    (mp4.py's drawtext/ass helper files use the same trick) -- an absolute Windows path inside a
    filter option string trips over the drive-letter colon, since filter options are themselves
    colon-delimited.
    """
    run_ffmpeg(
        ["-i", str(path), "-vf", f"signalstats,metadata=print:file={stats_path.name}",
         "-f", "null", "-"],
        cwd=stats_path.parent,
    )
    text = stats_path.read_text(encoding="utf-8", errors="replace")
    return [
        float(line.rsplit("=", 1)[1])
        for line in text.splitlines()
        if "lavfi.signalstats.YAVG=" in line
    ]


@pytest.mark.skipif(
    shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None, reason="ffmpeg"
)
def test_real_trailing_fade_concat_path_is_not_all_black(tmp_path: Path) -> None:
    """Live-found regression: a real narrated-reel render on the xfade path came back entirely
    black. Root cause: ffmpeg's ``fade=t=in:st=X`` forces EVERY frame before ``X`` to black, not
    just frames inside its own ``[X, X+d]`` window -- so the (pre-fix) unconditional fade-in half
    emitted for a TRAILING fade (whose ``st`` sits at the stream's own end) blacked the entire
    video. The string-only filtergraph assertions in this file would have (and did) pass on that
    broken build, since they only check which filter strings appear, not what they render to.
    This is the pixel-level proof, on the plain-concat path (hard cut between the two clips, no
    crossfade): build two solid-WHITE clips, render with only a trailing "fade", and check via
    real ffmpeg + signalstats that the output is bright early, still bright right up to the fade
    window, and near-black only at the very end -- never entirely black."""
    a = _white_src(tmp_path, "a.mp4", seconds=3)
    b = _white_src(tmp_path, "b.mp4", seconds=3)
    dest = tmp_path / "concat_trailing_fade.mp4"
    render_clips_mp4(
        [(a, 0, 30), (b, 0, 30)],
        dest,
        rate_num=10, rate_den=1,
        video_transitions=[VideoTransition(kind="fade", boundary_frame=60, duration_frames=30)],
    )
    yavg = _yavg_series(dest, tmp_path / "concat_stats.txt")
    assert len(yavg) == 60
    assert yavg[5] > 100, f"early frame must be bright, got {yavg[5]}"
    assert yavg[25] > 100, f"frame before the fade window must still be bright, got {yavg[25]}"
    assert yavg[-1] < 25, f"final frame must be near-black, got {yavg[-1]}"


@pytest.mark.skipif(
    shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None, reason="ffmpeg"
)
def test_real_trailing_fade_xfade_path_is_not_all_black(tmp_path: Path) -> None:
    """Same regression, on the xfade fold path this time: a real crossfade boundary (so
    ``has_crossfade`` routes the render through ``_xfade_base_graph``, exactly what a narrated
    reel with the default ``crossfade_frames`` takes) coexisting with a trailing fade must not
    black out the whole video either. Both source clips are solid white, so the crossfade blend
    region itself reads as white too -- the only expected darkening is the trailing fade's own
    ramp at the very end."""
    a = _white_src(tmp_path, "a.mp4", seconds=4)  # extra length -> reserve for the crossfade
    b = _white_src(tmp_path, "b.mp4", seconds=4)
    dest = tmp_path / "xfade_trailing_fade.mp4"
    render_clips_mp4(
        [(a, 0, 30), (b, 0, 30)],
        dest,
        rate_num=10, rate_den=1,
        video_transitions=[
            VideoTransition(kind="crossfade", boundary_frame=30, duration_frames=10),
            VideoTransition(kind="fade", boundary_frame=60, duration_frames=30),
        ],
    )
    yavg = _yavg_series(dest, tmp_path / "xfade_stats.txt")
    assert len(yavg) == 60
    assert yavg[5] > 100, f"early frame must be bright, got {yavg[5]}"
    assert yavg[25] > 100, f"frame before the fade window must still be bright, got {yavg[25]}"
    assert yavg[-1] < 25, f"final frame must be near-black, got {yavg[-1]}"


@pytest.mark.skipif(
    shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None, reason="ffmpeg"
)
def test_real_degraded_crossfade_concat_path_is_not_all_black(tmp_path: Path) -> None:
    """Live-reproduced regression (2026-08-22): a narrated reel whose clips end exactly at the
    source's last frame rendered ENTIRELY black (measured YAVG ~17 at every timestamp), while
    the same timeline with crossfade_frames=0 rendered correctly bright.

    Root cause: ``_crossfade_durations`` correctly clamps a crossfade to the post-cut reserve
    available in the outgoing clip's source; with zero reserve the boundary degrades to a hard
    cut (duration 0), so ``has_crossfade`` is False and ``render_clips_mp4`` takes the plain
    concat path. That path used to feed the UNFILTERED ``video_transitions`` list into
    ``_video_transition_chain``, which only skips ``kind=="hard"`` -- the degraded boundary
    still carries ``kind="crossfade"`` with its ORIGINAL (unclamped) duration, so it still
    emitted a dip-to-black ``fade=t=out``/``fade=t=in`` pair for a boundary that was supposed to
    be a silent hard cut.

    Per real ffmpeg's own ``fade`` semantics (confirmed empirically while diagnosing this bug):
    ``fade=t=in:st=X`` forces every frame before X to black, and ``fade=t=out`` STAYS black
    after its own window ends -- so this one mid-timeline dip-to-black pair does not just darken
    the boundary's neighbourhood, it blacks the ENTIRE assembled stream (both sides), which is
    exactly the reported all-black symptom.

    Three solid-white clips, each fully consumed by its cut (src_out == the source's own frame
    count -- no reserve for a crossfade). A crossfade is requested only at the first boundary
    (A/B); the second boundary (B/C) is a plain hard cut. After the fix, the degraded crossfade
    falls through as a silent hard cut and early/middle/late frames all stay bright."""
    a = _white_src(tmp_path, "a.mp4", seconds=3)  # exactly 30 frames -- fully used, no reserve
    b = _white_src(tmp_path, "b.mp4", seconds=3)
    c = _white_src(tmp_path, "c.mp4", seconds=3)
    dest = tmp_path / "degraded_crossfade.mp4"
    render_clips_mp4(
        [(a, 0, 30), (b, 0, 30), (c, 0, 30)],
        dest,
        rate_num=10, rate_den=1,
        video_transitions=[
            VideoTransition(kind="crossfade", boundary_frame=30, duration_frames=8),
        ],
    )
    yavg = _yavg_series(dest, tmp_path / "degraded_stats.txt")
    assert len(yavg) == 90
    assert yavg[5] > 100, f"early frame (clip A) must be bright, got {yavg[5]}"
    assert yavg[45] > 100, f"middle frame (clip B) must be bright, got {yavg[45]}"
    assert yavg[85] > 100, f"late frame (clip C) must be bright, got {yavg[85]}"


@pytest.mark.skipif(
    shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None, reason="ffmpeg"
)
def test_real_mid_timeline_fade_dips_only_its_own_window(tmp_path: Path) -> None:
    """Reported regression: a user-selectable mid-timeline dip-to-black (``kind="fade"`` at a
    boundary strictly BEFORE the end of the stream, e.g. ``transition_style="fade"`` on a cut
    between two clips) blacked the ENTIRE video, not just the window around the boundary.

    Root cause (two independent empirical checks): ffmpeg's ``fade=t=out:st=A:d=D`` does not
    merely ramp down during ``[A, A+D]`` -- it STAYS black for the whole remainder AFTER that
    window. Symmetrically ``fade=t=in:st=B:d=D`` force-blacks EVERY frame BEFORE ``B``, not just
    ramping up inside ``[B, B+D]``. ``_video_transition_chain`` chains exactly this pair
    (fade-out ending at the boundary, fade-in starting at the boundary) on one stream for every
    non-hard, non-trailing transition -- so before the fix the fade-out's "stays black after"
    covered everything from the window to the stream's end, and the fade-in's "blacks everything
    before" covered everything from the stream's start to the window, together blacking the
    whole video instead of just the intended dip.

    Two solid-white clips (3s/30f each @ 10fps, 60f total), hard-cut boundary at frame 30 (the
    stream's midpoint, NOT its end -- boundary_frame(30) < total_frames(60), so this is a true
    mid-timeline dip, unlike the trailing-fade regression tests above) with a 1s (10-frame)
    ``kind="fade"`` transition. Fade-out window [2.0s,3.0s], fade-in window [3.0s,4.0s]. Checked
    via real ffmpeg + signalstats: bright well before the dip, dark at the boundary, bright
    again well after -- never black outside the dip's own window."""
    a = _white_src(tmp_path, "a.mp4", seconds=3)
    b = _white_src(tmp_path, "b.mp4", seconds=3)
    dest = tmp_path / "mid_timeline_fade.mp4"
    render_clips_mp4(
        [(a, 0, 30), (b, 0, 30)],
        dest,
        rate_num=10, rate_den=1,
        video_transitions=[VideoTransition(kind="fade", boundary_frame=30, duration_frames=10)],
    )
    yavg = _yavg_series(dest, tmp_path / "mid_fade_stats.txt")
    assert len(yavg) == 60
    assert yavg[5] > 100, f"frame well before the dip must be bright, got {yavg[5]}"
    assert yavg[30] < 40, f"frame at the boundary must be dark, got {yavg[30]}"
    assert yavg[55] > 100, f"frame well after the dip must be bright again, got {yavg[55]}"
