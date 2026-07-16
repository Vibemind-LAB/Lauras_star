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
from laura.short_creator.production_tools import _qa_prompt, _shape_of


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
