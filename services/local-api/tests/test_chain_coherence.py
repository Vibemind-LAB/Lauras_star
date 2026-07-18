"""A chain link must know what it was built from — and the chain must check.

Live finding (board 094f92a8, 2026-07-18): the board carried script v39 (82 words), voice v3
(29.4s) and render_report v2 whose voice_s was 108.8s — the signature of script *v14*, 25
versions earlier. The render's own ``voice_fits`` check said OK, and it was: for a pairing that
no longer existed. Read off the board it looked like a finished, verified film.

Nothing had lied. Every artifact was saved correctly, the chain position was decided by file
presence as designed, and the revision-cap guard restored the last real render so a revising
loop could not burn the chain. What was missing is that no artifact records what it was derived
from, so no one could notice the pieces had drifted apart.

``voice`` already had the right idea with ``script_hash``. These tests generalise it to the
render and — the part that matters — make the drift VISIBLE instead of merely recorded.
"""

from __future__ import annotations

from pathlib import Path

from laura.short_creator.board import Board
from laura.short_creator.board_models import (
    BoardMeta,
    RenderCheck,
    RenderReport,
    Script,
    ScriptLine,
)
from laura.short_creator.production_tools import script_hash


def _board(tmp_path: Path) -> Board:
    return Board.create(
        tmp_path / "board",
        BoardMeta(
            session_id="s1",
            asset_id="a1",
            created_utc="2026-07-18T12:00:00+00:00",
            task="demo",
            language="English",
            target_seconds=174.0,
        ),
    )


def _script(text: str) -> Script:
    return Script(language="English", lines=[ScriptLine(chapter=1, scene_number=1, text=text)])


def _render(*, script_hash_: str) -> RenderReport:
    return RenderReport(
        export_id="e1",
        video_s=109.4,
        voice_s=108.8,
        width=1920,
        height=1080,
        script_hash=script_hash_,
        checks=[RenderCheck(name="voice_fits", ok=True), RenderCheck(name="export_ready", ok=True)],
    )


# --- the render records what it rendered ---------------------------------------------------


def test_a_render_records_the_script_it_was_made_from() -> None:
    report = _render(script_hash_="abc123")
    assert report.script_hash == "abc123"


def test_an_old_board_without_provenance_still_loads(tmp_path: Path) -> None:
    """The back-compat guard: boards written before this field must not fail to open.

    A stage-direction check was once put in a model validator and broke reading every existing
    board. Provenance is additive for exactly that reason.
    """
    board = _board(tmp_path)
    board.save(
        "render_report",
        RenderReport(export_id="e1", video_s=109.4, width=1920, height=1080),
    )

    loaded = board.load("render_report")
    assert isinstance(loaded, RenderReport)
    assert loaded.script_hash == ""


# --- the board can answer whether the render still matches the script ----------------------


def test_a_render_made_from_the_current_script_is_not_stale(tmp_path: Path) -> None:
    board = _board(tmp_path)
    script = _script("the line that was rendered")
    board.save("script", script)
    board.save("render_report", _render(script_hash_=script_hash(script.lines)))

    assert board.status()["artifacts"]["render_report"]["stale"] is False


def test_the_live_drift_is_reported_as_stale(tmp_path: Path) -> None:
    """The incident, in the order it actually happened.

    A new script invalidates the render below it, so drift alone leaves nothing to check. The
    live board had a render anyway: on hitting the revision cap, render_production restores the
    newest archived report so the finished export is still reported (b511639). That restore is
    what put a v14-era render on a v39 board — and it is what has to be caught.
    """
    board = _board(tmp_path)
    rendered = _script("the old line that was rendered")
    board.save("script", rendered)
    board.save("render_report", _render(script_hash_=script_hash(rendered.lines)))

    board.save("script", _script("a completely different line written later"))
    assert board.load("render_report") is None, "the save should have invalidated it"

    board.revert("render_report", 1)  # what the revision-cap guard does

    assert board.status()["artifacts"]["render_report"]["stale"] is True


def test_unknown_provenance_is_not_claimed_to_be_either(tmp_path: Path) -> None:
    """An old board cannot be proven stale OR current — say so rather than guess.

    Reporting stale=False would repeat the original bug in a new place: asserting freshness
    that was never established.
    """
    board = _board(tmp_path)
    board.save("script", _script("some line"))
    board.save(
        "render_report",
        RenderReport(export_id="e1", video_s=109.4, width=1920, height=1080),
    )

    assert board.status()["artifacts"]["render_report"]["stale"] is None


def test_a_render_without_a_script_on_the_board_is_not_stale(tmp_path: Path) -> None:
    """Nothing to compare against is not the same as a mismatch."""
    board = _board(tmp_path)
    board.save("render_report", _render(script_hash_="abc123"))

    assert board.status()["artifacts"]["render_report"]["stale"] is None


def test_artifacts_that_carry_no_provenance_report_no_staleness(tmp_path: Path) -> None:
    """Only links that record what they came from can answer the question."""
    board = _board(tmp_path)
    board.save("script", _script("some line"))

    assert "stale" not in board.status()["artifacts"]["script"]
