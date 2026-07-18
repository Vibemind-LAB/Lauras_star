"""The production's delivery format decides the canvas — end to end.

Live finding: the whole v2 pipeline was nailed to 1080x1920. The renderer had taken an
``out_size`` all along; only the agent layer above it hard-coded the reel, so a screen
recording headed for YouTube would have been cropped to a vertical short — throwing away
the half of the frame that carries the content. The format field already existed on the
board and nothing read it.

These tests pin the seam: format -> canvas -> render call, QA prompt, and report.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from laura.config import Settings
from laura.db.database import Database, create_database
from laura.short_creator.board import Board
from laura.short_creator.board_models import BoardMeta, Script, ScriptLine, canvas_for
from laura.short_creator.production_agents import production_agent_specs
from laura.short_creator.production_tools import (
    _qa_prompt,
    _roi_rule,
    _shape_of,
    build_production_tool_specs,
)


def _bare_db(tmp_path: Path) -> Database:
    return create_database(Settings(workspace_root=tmp_path))


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


# --- the script language belongs to the production, not to the source code ----------------
# Live finding: the roster hard-coded "in the video's language (German) — never switch
# languages mid-script", and the orchestrator's task template repeated it. A goal that said
# ENGLISH twice still produced a German script: a task string cannot argue a system prompt out
# of a hard-coded language, and a hackathon jury reads English.


def test_german_stays_the_default_so_nothing_regresses() -> None:
    assert _meta("insta").language == "German"
    spec = next(s for s in production_agent_specs() if s.name == "scene_author")
    assert "German" in spec.system_message


def test_the_roster_writes_in_the_board_s_language() -> None:
    spec = next(s for s in production_agent_specs("English") if s.name == "scene_author")
    assert "English" in spec.system_message
    assert "German" not in spec.system_message


def test_the_language_rule_still_forbids_switching_mid_script() -> None:
    """The useful half of the old instruction must survive the parameterisation."""
    spec = next(s for s in production_agent_specs("English") if s.name == "scene_author")
    assert "never switch languages mid-script" in spec.system_message


# --- the script's own shortfall must be visible where the author checks its work ---------
# Live finding: the scene_author wrote 140 words against a 300-word budget and called
# get_script to verify. Nothing compared the two, so nothing said the film would come out
# at half its target. A chapter's video length IS its share of the voice, so a short script
# is a short film — this is the number that decides the whole shape.


def test_get_script_reports_the_gap_between_what_was_written_and_the_budget(
    tmp_path: Path,
) -> None:
    board = Board.create(
        tmp_path / "b",
        BoardMeta(
            session_id="s1",
            asset_id="a1",
            created_utc="2026-07-17T00:00:00+00:00",
            task="demo",
            format="x",
            language="English",
            target_seconds=174.0,
        ),
    )
    board.save(
        "script",
        Script(
            language="English",
            lines=[ScriptLine(chapter=1, scene_number=1, text=" ".join(["word"] * 140))],
        ),
    )

    specs = {
        s.name: s
        for s in build_production_tool_specs(_bare_db(tmp_path), board, asset_id="a1")
    }
    out = specs["get_script"].func()

    assert out["words"] == 140
    # 174s of English at 0.41 s/word, minus the 10% headroom the rate's variance needs.
    assert out["budget_words"] == 381
    assert out["estimated_voice_s"] == pytest.approx(57.4, abs=0.5)
    assert out["shortfall_pct"] == pytest.approx(63.3, abs=1.0)
