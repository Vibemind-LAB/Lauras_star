"""A contact-sheet tile must show what the render shows, not the raw source frame.

Live finding: the sheet extracted plain proxy frames, so every tile looked the same
whether the segment was zoomed or not. Exactly the faults the user hit in the finished
video — jumpy framing, text cut off at a crop edge, content sitting tiny inside the
letterbox — are invisible on a sheet of un-framed stills. A gate that cannot show the
defect it gates is decoration.

The canvas is the production's, not a constant: a landscape delivery sheeted 9:16 would
gate framing the viewer never sees.
"""

from __future__ import annotations

from laura.short_creator.board_models import canvas_for
from laura.short_creator.production_tools import _tile_filter

_REEL = (1080, 1920)
_WIDE = (1920, 1080)


def test_a_segment_without_roi_is_shown_letterboxed_in_the_output_aspect() -> None:
    """No zoom means the 16:9 source sits inside a 9:16 frame — the tile must say so."""
    vf = _tile_filter(
        roi=None, src_w=1920, src_h=1080, out_w=_REEL[0], out_h=_REEL[1],
        fontfile=None, label="0 S3",
    )

    assert "pad=" in vf, "must letterbox to the output aspect, not crop"
    assert "crop=" not in vf


def test_a_segment_with_roi_is_shown_cropped_exactly_as_the_render_crops_it() -> None:
    vf = _tile_filter(
        roi=(0.45, 0.15, 0.15, 0.10), src_w=1920, src_h=1080, out_w=_REEL[0], out_h=_REEL[1],
        fontfile=None, label="1 S4",
    )

    assert "crop=" in vf, "a zoomed segment must show its crop window"
    assert "pad=" not in vf, "the crop already carries the output aspect"


def test_the_crop_window_keeps_the_render_aspect() -> None:
    """roi_to_window is the renderer's own function — the tile must not invent framing."""
    vf = _tile_filter(
        roi=(0.45, 0.15, 0.15, 0.10), src_w=1920, src_h=1080, out_w=_REEL[0], out_h=_REEL[1],
        fontfile=None, label="x",
    )
    crop = vf.split("crop=")[1].split(",")[0]
    w, h, _x, _y = (int(v) for v in crop.split(":"))
    assert abs(w / h - _REEL[0] / _REEL[1]) < 0.01


def test_a_landscape_production_crops_to_landscape_not_to_a_reel() -> None:
    """The whole point of format "x": a 16:9 demo must not be gated through a 9:16 window."""
    vf = _tile_filter(
        roi=(0.45, 0.15, 0.15, 0.10), src_w=1920, src_h=1080, out_w=_WIDE[0], out_h=_WIDE[1],
        fontfile=None, label="0 S1",
    )
    crop = vf.split("crop=")[1].split(",")[0]
    w, h, _x, _y = (int(v) for v in crop.split(":"))
    assert abs(w / h - _WIDE[0] / _WIDE[1]) < 0.01
    assert w > h, "a landscape canvas must produce a landscape crop window"


def test_a_landscape_tile_is_wider_than_it_is_tall() -> None:
    """The tile geometry follows the canvas, so the sheet reads like the finished video."""
    wide = _tile_filter(
        roi=None, src_w=1920, src_h=1080, out_w=_WIDE[0], out_h=_WIDE[1], fontfile=None, label="a"
    )
    reel = _tile_filter(
        roi=None, src_w=1920, src_h=1080, out_w=_REEL[0], out_h=_REEL[1], fontfile=None, label="a"
    )
    wide_w, wide_h = (int(v) for v in wide.split("pad=")[1].split(":")[:2])
    reel_w, reel_h = (int(v) for v in reel.split("pad=")[1].split(":")[:2])

    assert wide_w > wide_h
    assert reel_h > reel_w


def test_label_is_drawn_when_a_font_is_available() -> None:
    vf = _tile_filter(
        roi=None, src_w=1920, src_h=1080, out_w=_REEL[0], out_h=_REEL[1],
        fontfile="C:/f.ttf", label="7 S10",
    )
    assert "drawtext" in vf


def test_no_font_means_no_drawtext_but_still_framed() -> None:
    vf = _tile_filter(
        roi=None, src_w=1920, src_h=1080, out_w=_REEL[0], out_h=_REEL[1],
        fontfile=None, label="7 S10",
    )
    assert "drawtext" not in vf
    assert "pad=" in vf


def test_the_sheet_canvas_comes_from_the_board_format() -> None:
    """canvas_for is the one place a format becomes pixels — tiles and render share it."""
    assert canvas_for("insta") == (True, _REEL)
    assert canvas_for("x") == (False, _WIDE)
    assert canvas_for("linkedin") == (True, (1080, 1080))
