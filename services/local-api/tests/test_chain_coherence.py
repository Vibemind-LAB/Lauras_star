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
    Chapter,
    RenderCheck,
    RenderReport,
    Script,
    ScriptLine,
    Storyline,
)
from laura.short_creator.production_tools import script_hash, silent_chapters


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


def test_a_fresh_render_is_not_stale_when_the_storyline_reorders_scenes(tmp_path: Path) -> None:
    """Review finding: the check site hashed the STORED line order, the write sites hash the
    STORYLINE order. A chapter whose storyline plays scene 2 before scene 1 — a normal
    narrative choice — made a just-rendered report read stale=True. A provenance signal that
    cries wolf on fresh renders teaches everyone to ignore the real v14-on-v39 case.
    """
    board = _board(tmp_path)
    board.save(
        "storyline",
        Storyline(
            red_thread="a demo",
            arc=[
                Chapter(
                    chapter=1,
                    role="hook",
                    message="m",
                    # Scene 2 opens the chapter; the author wrote the lines in 1, 2 order.
                    scene_numbers=[2, 1],
                    target_seconds=10.0,
                )
            ],
        ),
    )
    script = Script(
        language="English",
        lines=[
            ScriptLine(chapter=1, scene_number=1, text="the line for scene one"),
            ScriptLine(chapter=1, scene_number=2, text="the line for scene two"),
        ],
    )
    board.save("script", script)
    # The write sites stamp the hash over the STORYLINE order — scene 2's line first.
    played_order = [script.lines[1], script.lines[0]]
    board.save("render_report", _render(script_hash_=script_hash(played_order)))

    assert board.status()["artifacts"]["render_report"]["stale"] is False


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


# --- the script must serve every chapter the storyline planned -----------------------------
# Live finding (run E): the storyline planned six chapters summing to exactly the 174s target.
# The script covered chapters 1 and 2 — 82 words — and chapters 3, 4, 5 and 6 had NO lines at
# all. The film came out at 109s. Nothing noticed: save_script_chapter validates a chapter it
# is given, and no one ever asked which chapters were never given.
#
# This is the same disease as the stale render, one link up: the script was a valid artifact
# that did not correspond to the storyline it was written against.


def _storyline(n_chapters: int) -> Storyline:
    return Storyline(
        red_thread="a demo",
        arc=[
            Chapter(
                chapter=i,
                role="hook" if i == 1 else "feature",
                message=f"beat {i}",
                scene_numbers=[i],
                target_seconds=29.0,
            )
            for i in range(1, n_chapters + 1)
        ],
    )


def _script_for_chapters(chapters: list[int]) -> Script:
    return Script(
        language="English",
        lines=[ScriptLine(chapter=c, scene_number=c, text=f"a line for {c}") for c in chapters],
    )


def test_the_chapters_the_author_never_wrote_are_named() -> None:
    """Run E exactly: six chapters planned, two written."""
    assert silent_chapters(_script_for_chapters([1, 2]), _storyline(6)) == [3, 4, 5, 6]


def test_a_complete_script_reports_no_silence() -> None:
    assert silent_chapters(_script_for_chapters([1, 2, 3]), _storyline(3)) == []


def test_a_chapter_with_any_line_counts_as_written() -> None:
    """The check is about coverage, not length — length is the budget's job."""
    assert silent_chapters(_script_for_chapters([1, 2, 3]), _storyline(3)) == []


def test_lines_for_a_chapter_the_storyline_does_not_have_are_not_silence() -> None:
    """A stray line is a different fault; do not report it as a missing chapter."""
    assert silent_chapters(_script_for_chapters([1, 2, 9]), _storyline(2)) == []


def test_without_a_storyline_nothing_can_be_called_missing() -> None:
    assert silent_chapters(_script_for_chapters([1]), None) == []


# --- provenance spreads to every derived link ----------------------------------------------
# The render learned to record its script in 6f702dc; the cutlist and the contact sheet were
# left without it — a stale cutlist on a revised board was exactly as invisible as the stale
# render used to be. Same contract everywhere: empty means pre-provenance (unknown), and the
# sheet INHERITS the cutlist's hash (it is a projection of the cutlist; recomputing could
# disagree with the very artifact it renders).


def test_cutlist_and_sheet_carry_provenance_and_status_reports_stale(tmp_path: Path) -> None:
    from laura.short_creator.board_models import ContactSheet, ContactSheetTile, Cutlist, CutSegment

    board = _board(tmp_path)
    rendered = _script("the rendered text")
    board.save("script", rendered)
    h = script_hash(rendered.lines)
    board.save(
        "cutlist",
        Cutlist(
            segments=[CutSegment(order=0, scene_number=1, start_frame=0, end_frame_exclusive=90)],
            script_hash=h,
        ),
    )
    board.save(
        "contact_sheet",
        ContactSheet(
            png_path="s.png",
            cols=1,
            rows=1,
            tiles=[ContactSheetTile(order=0, scene_number=1, frame=45, label="1")],
            script_hash=h,
        ),
    )

    arts = board.status()["artifacts"]
    assert arts["cutlist"]["stale"] is False
    assert arts["contact_sheet"]["stale"] is False


def test_old_boards_without_cutlist_provenance_still_load_as_unknown(tmp_path: Path) -> None:
    from laura.short_creator.board_models import Cutlist, CutSegment

    board = _board(tmp_path)
    board.save("script", _script("some line"))
    board.save(
        "cutlist",
        Cutlist(
            segments=[CutSegment(order=0, scene_number=1, start_frame=0, end_frame_exclusive=90)]
        ),
    )

    assert board.status()["artifacts"]["cutlist"]["stale"] is None


# --- staleness generalizes to parents: any drifted parent makes the artifact stale ---------
# script_hash-based staleness only saw the script. With parents, a reverted VOICE (script
# unchanged) correctly marks the render stale — the case the review proved script_hash-based
# checks could never see.


def test_parents_all_matching_reports_fresh(tmp_path: Path) -> None:
    from laura.short_creator.board_models import content_hash

    board = _board(tmp_path)
    script = _script("the rendered line")
    board.save("script", script)
    current_script = board.load("script")
    assert current_script is not None
    board.save(
        "render_report",
        RenderReport(
            export_id="e1",
            video_s=100.0,
            width=1920,
            height=1080,
            parents={"script": content_hash(current_script)},
        ),
    )

    assert board.status()["artifacts"]["render_report"]["stale"] is False


def test_a_single_drifted_parent_reports_stale(tmp_path: Path) -> None:
    from laura.short_creator.board_models import content_hash

    board = _board(tmp_path)
    board.save("script", _script("the rendered line"))
    old = board.load("script")
    assert old is not None
    old_hash = content_hash(old)
    render = RenderReport(
        export_id="e1",
        video_s=100.0,
        width=1920,
        height=1080,
        parents={"script": old_hash},
    )
    board.save("render_report", render)
    # The script moves on; put the render back the way the cap guard does.
    board.save("script", _script("a different line"))
    board.revert("render_report", 1)

    assert board.status()["artifacts"]["render_report"]["stale"] is True


def test_a_missing_parent_reports_unknown(tmp_path: Path) -> None:
    board = _board(tmp_path)
    board.save(
        "render_report",
        RenderReport(
            export_id="e1",
            video_s=100.0,
            width=1920,
            height=1080,
            parents={"cutlist": "somehash"},
        ),
    )

    assert board.status()["artifacts"]["render_report"]["stale"] is None


def test_an_unknown_parent_key_reports_unknown_not_crash(tmp_path: Path) -> None:
    """``status()`` must survive a ``parents`` key that names no real chain artifact (a
    hand-edited/corrupt board) -- ``Board.load`` raises ``KeyError`` for unknown names, and
    that must never escape through ``_parents_stale``."""
    board = _board(tmp_path)
    board.save(
        "render_report",
        RenderReport(
            export_id="e1",
            video_s=100.0,
            width=1920,
            height=1080,
            parents={"bogus": "x"},
        ),
    )

    assert board.status()["artifacts"]["render_report"]["stale"] is None


def test_empty_parents_falls_back_to_script_hash_logic(tmp_path: Path) -> None:
    """Old boards keep the behaviour they shipped with — no parents, script_hash decides."""
    board = _board(tmp_path)
    script = _script("the line that was rendered")
    board.save("script", script)
    board.save("render_report", _render(script_hash_=script_hash(script.lines)))

    assert board.status()["artifacts"]["render_report"]["stale"] is False
