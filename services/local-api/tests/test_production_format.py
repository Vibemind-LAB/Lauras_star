"""The production's delivery format decides the canvas — end to end.

Live finding: the whole v2 pipeline was nailed to 1080x1920. The renderer had taken an
``out_size`` all along; only the agent layer above it hard-coded the reel, so a screen
recording headed for YouTube would have been cropped to a vertical short — throwing away
the half of the frame that carries the content. The format field already existed on the
board and nothing read it.

These tests pin the seam: format -> canvas -> render call, QA prompt, and report.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from laura.short_creator.board_models import BoardMeta, canvas_for
from laura.short_creator.production_tools import _qa_prompt, _roi_rule, _shape_of


def _meta(fmt: str) -> BoardMeta:
    return BoardMeta(
        session_id="s1",
        asset_id="a1",
        created_utc="2026-07-16T12:00:00+00:00",
        task="demo",
        format=fmt,  # type: ignore[arg-type]  # the point of the test is the validation
        target_seconds=180.0,
    )


def test_the_reel_stays_the_default_so_existing_boards_keep_working() -> None:
    """A board written before formats meant anything must still load and render as before."""
    assert _meta("insta").format == "insta"
    assert canvas_for("insta") == (True, (1080, 1920))


def test_x_is_the_landscape_canvas() -> None:
    vertical, (w, h) = canvas_for("x")
    assert (w, h) == (1920, 1080)
    assert vertical is False, "a 16:9 delivery must not go through the vertical fit path"


def test_an_unknown_format_is_rejected_at_the_board_boundary() -> None:
    """Silently falling back to a reel would ship the wrong video with no error anywhere."""
    with pytest.raises(ValidationError):
        _meta("youtube")


def test_the_qa_prompt_names_the_shape_actually_rendered() -> None:
    """A VLM told to judge a "vertical canvas" invents vertical faults on a landscape frame."""
    wide = _qa_prompt(1920, 1080)
    assert "landscape" in wide
    assert "1920x1080" in wide
    assert "vertical" not in wide

    reel = _qa_prompt(1080, 1920)
    assert "vertical" in reel
    assert "1080x1920" in reel


@pytest.mark.parametrize(
    ("w", "h", "shape"),
    [(1080, 1920, "vertical"), (1920, 1080, "landscape"), (1080, 1080, "square")],
)
def test_shape_of_covers_every_preset(w: int, h: int, shape: str) -> None:
    assert _shape_of(w, h) == shape


# --- the roi rule follows canvas vs source, not the reel by habit --------------------------
# Live finding: on a 16:9 screen recording rendered to a 16:9 canvas, the scene_author cropped
# an org chart captioned "36 agents, 9 teams" down to 2% of its area — the scale WAS the point.
# The prompt had asked for "the ONE region a viewer must read", which is right only when the
# canvas is narrower than the footage and the content would otherwise sit tiny in a letterbox.


def test_a_reel_canvas_on_landscape_footage_still_wants_a_roi() -> None:
    """The v1 case: 16:9 source into a 9:16 reel — without a roi the content is a stamp."""
    rule = _roi_rule(src_w=1920, src_h=1080, out_w=1080, out_h=1920)
    assert "ONE region" in rule
    assert "must be null" not in rule


def test_a_matching_canvas_tells_the_reviewer_to_leave_the_frame_alone() -> None:
    """16:9 into 16:9: a roi cannot rescue anything, it can only cut content away."""
    rule = _roi_rule(src_w=1920, src_h=1080, out_w=1920, out_h=1080)
    assert "must be null" in rule
    assert "ONE region" not in rule


def test_a_wide_screen_recording_into_a_16_9_canvas_leaves_the_frame_alone() -> None:
    """The real footage: a 2.27:1 app window letterboxed into 16:9 — still no crop wanted."""
    rule = _roi_rule(src_w=1706, src_h=752, out_w=1920, out_h=1080)
    assert "must be null" in rule


def test_a_square_canvas_on_wide_footage_wants_a_roi() -> None:
    """linkedin 1:1 is much narrower than 16:9 footage — the crop earns its keep again."""
    rule = _roi_rule(src_w=1920, src_h=1080, out_w=1080, out_h=1080)
    assert "ONE region" in rule


def test_unknown_source_dimensions_keep_the_cropping_rule() -> None:
    """Metrics can be missing; the v1 behaviour must stay the fallback, not a silent change."""
    rule = _roi_rule(src_w=0, src_h=0, out_w=1080, out_h=1920)
    assert "ONE region" in rule
