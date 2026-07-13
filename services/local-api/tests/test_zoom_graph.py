"""Filtergraph builders for zoom_hybrid — exact string assertions."""

from laura.render.reel import reel_blur_fill_graph
from laura.render.zoom import (
    ZoomSpec,
    roi_to_window,
    start_window,
    zoom_concat_graph,
    zoom_hybrid_segment_parts,
)


def _spec() -> ZoomSpec:
    end = roi_to_window((0.6, 0.1, 0.25, 0.25), src_w=1920, src_h=1080, out_w=1080, out_h=1920)
    start = start_window(end, src_w=1920, src_h=1080, out_w=1080, out_h=1920)
    return ZoomSpec(end_win=end, start_win=start, zoom_start_s=1.0, transition_s=0.6)


def test_blur_fill_default_tag_is_byte_identical() -> None:
    graph = reel_blur_fill_graph("[vcat]", "[out]")
    assert "[_rbbg]" in graph and "[_rbfg]" in graph and "[_rbbl]" in graph and "[_rbfl]" in graph


def test_blur_fill_custom_tag() -> None:
    graph = reel_blur_fill_graph("[a]", "[b]", tag="_z3")
    assert "[_z3bg]" in graph and "[_rbbg]" not in graph


def test_segment_without_spec_is_blur_only() -> None:
    parts, label = zoom_hybrid_segment_parts(
        0, 0, start_frame=30, end_frame_exclusive=150, spec=None, out_w=1080, out_h=1920)
    assert label == "[zh0]"
    joined = ";".join(parts)
    assert "trim=start_frame=30:end_frame=150" in joined
    assert "[_z0bg]" in joined          # per-segment blur tag
    assert "xfade" not in joined
    assert joined.endswith("setsar=1[zh0]")


def test_segment_with_spec_builds_hybrid_graph() -> None:
    spec = _spec()
    parts, label = zoom_hybrid_segment_parts(
        2, 2, start_frame=0, end_frame_exclusive=120, spec=spec, out_w=1080, out_h=1920)
    assert label == "[zh2]"
    joined = ";".join(parts)
    assert "split=2[zfa2][zza2]" in joined
    assert "[_z2bg]" in joined
    # _fmt_seconds trims trailing zeros: 1.0 → "1", 0.6 → "0.6"
    assert "trim=start=1,setpts=PTS-STARTPTS" in joined  # zoom branch starts at zoom_start_s
    ex, ey, ew, eh = spec.end_win
    assert f"crop={ew}:{eh}:{ex}:{ey}" in joined  # static end-window crop (w/h are config-time)
    assert "scale=1080:1920:flags=lanczos" in joined
    assert "xfade=transition=fade:duration=0.6:offset=1," in joined


def test_concat_graph_single_and_multi() -> None:
    parts, v, a = zoom_concat_graph(
        [(0, 120)], [None], audio_flags=[False], has_base_audio=False,
        rate_num=30, rate_den=1, out_w=1080, out_h=1920)
    assert v == "[vcat]" and a is None
    assert "[zh0]null[vcat]" in parts

    parts2, v2, a2 = zoom_concat_graph(
        [(0, 120), (120, 240)], [None, _spec()],
        audio_flags=[True, True], has_base_audio=True,
        rate_num=30, rate_den=1, out_w=1080, out_h=1920)
    assert v2 == "[vcat]" and a2 == "[abase]"
    assert "[zh0][zh1]concat=n=2:v=1:a=0[vcat]" in parts2
    assert "[0:a]atrim=start=0:end=4,asetpts=PTS-STARTPTS[zba0]" in parts2
    assert "[zba0][zba1]concat=n=2:v=0:a=1[abase]" in parts2


def test_concat_graph_silent_input_gets_anullsrc() -> None:
    parts, _v, a = zoom_concat_graph(
        [(0, 120)], [None], audio_flags=[False], has_base_audio=True,
        rate_num=30, rate_den=1, out_w=1080, out_h=1920)
    assert a == "[abase]"
    assert "anullsrc=channel_layout=stereo:sample_rate=48000" in parts
    assert "[zba0]anull[abase]" in parts
