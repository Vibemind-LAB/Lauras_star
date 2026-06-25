"""Tests for the pure reel video-filter chain builder.

Overlay text is referenced via drawtext ``textfile=`` (a basename), never inline
``text='…'`` — see ``reel.py`` for why. These tests assert the chain *structure*;
the real-ffmpeg edge-case coverage (apostrophes, colons, %, unicode) lives in
``test_reel_render.py`` and ``test_reel_e2e.py``.
"""
from __future__ import annotations

from laura.render.reel import reel_blur_fill_graph, reel_video_chain


def test_vertical_no_text_contains_crop_and_scale() -> None:
    chain = reel_video_chain(vertical=True, font="X")
    # Center-crop is clamped to the source so a source already narrower/taller than 9:16
    # cannot request a crop larger than the frame (regression: 464×832 broke crop=ih*9/16:ih).
    assert "crop='min(iw,ih*9/16)':'min(ih,iw*16/9)'" in chain
    assert "scale=1080:1920" in chain
    assert "drawtext=" not in chain


def test_hook_uses_textfile_basename_single_drawtext() -> None:
    chain = reel_video_chain(
        vertical=True,
        hook_textfile="out.reel_hook.txt",
        font="X",
    )
    assert "textfile=out.reel_hook.txt" in chain
    assert "text='" not in chain  # never inline text
    assert chain.count("drawtext=") == 1


def test_both_textfiles_produce_two_drawtexts() -> None:
    chain = reel_video_chain(
        vertical=False,
        hook_textfile="h.txt",
        disclosure_textfile="d.txt",
        font="X",
    )
    assert chain.count("drawtext=") == 2
    assert "textfile=h.txt" in chain
    assert "textfile=d.txt" in chain


def test_no_flags_returns_empty_string() -> None:
    chain = reel_video_chain(vertical=False, font="X")
    assert chain == ""


# ---------------------------------------------------------------------------
# reel_fit mode
# ---------------------------------------------------------------------------


def test_reel_fit_contains_pad_not_crop() -> None:
    """reel_fit=True produces a scale+pad letterbox chain without any crop filter."""
    chain = reel_video_chain(vertical=True, reel_fit=True, font="X")
    assert "pad" in chain
    assert "crop" not in chain
    assert "1080" in chain
    assert "1920" in chain


def test_reel_fit_false_is_unchanged_crop_chain() -> None:
    """reel_fit=False (default) is the (clamped) center-crop chain, no letterbox pad."""
    chain_default = reel_video_chain(vertical=True, font="X")
    chain_explicit_false = reel_video_chain(vertical=True, reel_fit=False, font="X")
    assert chain_default == chain_explicit_false
    assert "crop='min(iw,ih*9/16)':'min(ih,iw*16/9)'" in chain_default
    assert "scale=1080:1920" in chain_default
    assert "pad" not in chain_default


def test_reel_fit_ignored_when_not_vertical() -> None:
    """reel_fit=True has no effect when vertical=False — no reframe filters are added."""
    chain = reel_video_chain(vertical=False, reel_fit=True, font="X")
    assert "pad" not in chain
    assert "crop" not in chain
    assert "scale" not in chain


# ---------------------------------------------------------------------------
# reel_blur_fill_graph
# ---------------------------------------------------------------------------


def test_blur_fill_graph_contains_split_boxblur_overlay() -> None:
    """The blur-fill graph must contain split, boxblur, and overlay primitives."""
    graph = reel_blur_fill_graph("[vcat]", "[out]")
    assert "split=2" in graph
    assert "boxblur" in graph
    assert "overlay" in graph


def test_blur_fill_graph_uses_in_and_out_labels() -> None:
    """The graph must start from in_label and end at out_label."""
    graph = reel_blur_fill_graph("[myconcatin]", "[myout]")
    assert "[myconcatin]" in graph
    assert "[myout]" in graph


def test_blur_fill_graph_uses_scale_to_cover_for_background() -> None:
    """Background copy must use force_original_aspect_ratio=increase (scale-to-cover)."""
    graph = reel_blur_fill_graph("[vcat]", "[out]")
    assert "force_original_aspect_ratio=increase" in graph


def test_blur_fill_graph_uses_scale_to_fit_for_foreground() -> None:
    """Foreground copy must use force_original_aspect_ratio=decrease (scale-to-fit)."""
    graph = reel_blur_fill_graph("[vcat]", "[out]")
    assert "force_original_aspect_ratio=decrease" in graph


def test_blur_fill_graph_is_semicolon_separated() -> None:
    """The graph must use semicolons (filter_complex segments), not just commas."""
    graph = reel_blur_fill_graph("[vcat]", "[out]")
    assert ";" in graph


def test_blur_fill_graph_crops_background_to_1080x1920() -> None:
    """Background must be cropped exactly to canvas size after scale-to-cover."""
    graph = reel_blur_fill_graph("[vcat]", "[out]")
    assert "crop=1080:1920" in graph


def test_blur_fill_graph_centers_overlay() -> None:
    """Overlay must centre the foreground: (W-w)/2:(H-h)/2."""
    graph = reel_blur_fill_graph("[vcat]", "[out]")
    assert "(W-w)/2:(H-h)/2" in graph


def test_reel_video_chain_unchanged_when_blur_fill_off() -> None:
    """reel_video_chain output is byte-identical regardless of reel_blur_fill — it does
    not accept that parameter; the caller (mp4.py) handles blur-fill routing."""
    chain_default = reel_video_chain(vertical=True, font="X")
    chain_fit_false = reel_video_chain(vertical=True, reel_fit=False, font="X")
    assert chain_default == chain_fit_false
    # No blur-fill artefacts leak into the plain chain.
    assert "boxblur" not in chain_default
    assert "split" not in chain_default
    assert "overlay" not in chain_default
