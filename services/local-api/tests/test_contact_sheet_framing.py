"""A contact-sheet tile must show what the render shows, not the raw source frame.

Live finding: the sheet extracted plain proxy frames, so every tile looked the same
whether the segment was zoomed or not. Exactly the faults the user hit in the finished
video — jumpy framing, text cut off at a crop edge, content sitting tiny inside the 9:16
letterbox — are invisible on a sheet of un-framed 16:9 stills. A gate that cannot show
the defect it gates is decoration.
"""

from __future__ import annotations

from laura.short_creator.production_tools import _RENDER_HEIGHT, _RENDER_WIDTH, _tile_filter


def test_a_segment_without_roi_is_shown_letterboxed_in_the_output_aspect() -> None:
    """No zoom means the 16:9 source sits inside a 9:16 frame — the tile must say so."""
    vf = _tile_filter(roi=None, src_w=1920, src_h=1080, fontfile=None, label="0 S3")

    assert "pad=" in vf, "must letterbox to the output aspect, not crop"
    assert "crop=" not in vf


def test_a_segment_with_roi_is_shown_cropped_exactly_as_the_render_crops_it() -> None:
    vf = _tile_filter(
        roi=(0.45, 0.15, 0.15, 0.10), src_w=1920, src_h=1080, fontfile=None, label="1 S4"
    )

    assert "crop=" in vf, "a zoomed segment must show its crop window"
    assert "pad=" not in vf, "the crop already carries the output aspect"


def test_the_crop_window_keeps_the_render_aspect() -> None:
    """roi_to_window is the renderer's own function — the tile must not invent framing."""
    vf = _tile_filter(
        roi=(0.45, 0.15, 0.15, 0.10), src_w=1920, src_h=1080, fontfile=None, label="x"
    )
    crop = vf.split("crop=")[1].split(",")[0]
    w, h, _x, _y = (int(v) for v in crop.split(":"))
    assert abs(w / h - _RENDER_WIDTH / _RENDER_HEIGHT) < 0.01


def test_label_is_drawn_when_a_font_is_available() -> None:
    vf = _tile_filter(roi=None, src_w=1920, src_h=1080, fontfile="C:/f.ttf", label="7 S10")
    assert "drawtext" in vf


def test_no_font_means_no_drawtext_but_still_framed() -> None:
    vf = _tile_filter(roi=None, src_w=1920, src_h=1080, fontfile=None, label="7 S10")
    assert "drawtext" not in vf
    assert "pad=" in vf
