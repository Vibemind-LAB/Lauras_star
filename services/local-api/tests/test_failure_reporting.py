"""A failed run must look failed — to the job, to the board, and to whoever is watching.

Live incident (2026-07-18): a production run was started while the backend process had no
agent API key. It died within seconds. For 55 minutes nothing said so:

    GET /jobs/{id}        -> status "succeeded", result_json {"status": "hard_fail",
                             "summary": "Connection error.", "ok": false}
    GET /production/{sid} -> meta.status "active", resume_point "scene_reviews:1"

An operator polling the production endpoint could not tell that run from a healthy one still
working. The audit that followed found one pattern behind every symptom: the system advances on
the SHAPE of a return value, not on whether the work happened.

  * the job runner treated "the handler returned" as "the job succeeded"
  * meta.status was written once at board creation and never again — decoration that reads
    like a liveness signal
  * run_production knew the failure and never wrote it to the board: two stores, one told
  * review_scene wrote a board artifact and reported ok when the VLM never ran
  * render_production saved a render_report — advancing the chain to QA — when the export
    had timed out and was still rendering

These tests pin the repaired contract. They are deliberately about VISIBILITY, not about the
work succeeding: a degraded review and an unfinished render are legitimate states, but they
must never be indistinguishable from the real thing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from laura.short_creator.board import Board
from laura.short_creator.board_models import (
    BestWindow,
    BoardMeta,
    Chapter,
    ContactSheet,
    ContactSheetTile,
    Cutlist,
    CutSegment,
    RenderCheck,
    RenderReport,
    SceneReview,
    Script,
    ScriptLine,
    Storyline,
    VoiceArtifact,
)


def _meta(**kw: object) -> BoardMeta:
    base: dict[str, object] = {
        "session_id": "s1",
        "asset_id": "a1",
        "created_utc": "2026-07-18T12:00:00+00:00",
        "task": "demo",
        "target_seconds": 174.0,
    }
    base.update(kw)
    return BoardMeta(**base)  # type: ignore[arg-type]


def _board(tmp_path: Path) -> Board:
    return Board.create(tmp_path / "board", _meta())


def _review(scene_number: int, *, degraded: bool) -> SceneReview:
    window = BestWindow(offset_s=0.0, duration_s=5.0)
    return SceneReview(
        scene_number=scene_number,
        src_start_frame=0,
        src_end_frame_exclusive=1080,
        description="a screen",
        whats_happening="something happens",
        hook_score=5,
        best_window=window,
        windows=[window],
        degraded=degraded,
        model="test",
        created_utc="2026-07-18T12:00:00+00:00",
    )


# --- B: the board must be able to learn that the run died ---------------------------------


def test_a_new_board_starts_active() -> None:
    assert _meta().status == "active"


def test_the_status_is_a_closed_set_not_free_text() -> None:
    """It was a bare str, so nothing stopped a typo from reading as a healthy run."""
    with pytest.raises(ValidationError):
        _meta(status="activ")


def test_the_board_can_be_told_the_run_failed(tmp_path: Path) -> None:
    """The whole incident in one assertion: the failure has to reach the board's own store."""
    board = _board(tmp_path)
    board.set_status("failed")

    assert Board(board.root).meta().status == "failed"


def test_the_board_can_be_told_the_run_finished(tmp_path: Path) -> None:
    board = _board(tmp_path)
    board.set_status("complete")

    assert Board(board.root).meta().status == "complete"


def test_setting_the_status_leaves_the_rest_of_the_meta_alone(tmp_path: Path) -> None:
    """A status write must not quietly reset the format, language or target."""
    board = Board.create(tmp_path / "b2", _meta(format="x", language="English"))
    board.set_status("failed")

    after = Board(board.root).meta()
    assert (after.format, after.language, after.target_seconds) == ("x", "English", 174.0)


def test_the_status_is_visible_where_an_operator_looks(tmp_path: Path) -> None:
    board = _board(tmp_path)
    board.set_status("failed")

    assert board.status()["meta"]["status"] == "failed"


# --- C: degraded reviews must not look like real ones -------------------------------------
# review_scene writes a SceneReview with a neutral hook_score and one default window when the
# VLM never ran. The count climbs exactly as it would with real analysis, so a run with zero
# visual analysis is indistinguishable from a working one — and the storyline and cutlist are
# then built on default windows.


def test_the_status_counts_reviews_that_were_never_actually_analysed(tmp_path: Path) -> None:
    board = _board(tmp_path)
    board.save_scene_review(_review(1, degraded=False))
    board.save_scene_review(_review(2, degraded=True))
    board.save_scene_review(_review(3, degraded=True))

    reviews = board.status()["scene_reviews"]
    assert reviews["count"] == 3
    assert reviews["degraded_count"] == 2
    assert reviews["degraded_scenes"] == [2, 3]


def test_a_fully_analysed_board_reports_no_degradation(tmp_path: Path) -> None:
    board = _board(tmp_path)
    board.save_scene_review(_review(1, degraded=False))

    reviews = board.status()["scene_reviews"]
    assert reviews["degraded_count"] == 0
    assert reviews["degraded_scenes"] == []


# --- C: a render that never finished must not advance the chain ---------------------------
# render_production saves the report unconditionally. When the export poll times out the export
# is still "rendering", export_ready is False — and the chain moved on to qa_report anyway,
# reporting an export_id for a video that may never have become watchable.


def _report(*, export_ready: bool) -> RenderReport:
    return RenderReport(
        export_id="e1",
        video_s=149.0,
        width=1920,
        height=1080,
        checks=[
            RenderCheck(name="voice_fits", ok=True),
            RenderCheck(
                name="export_ready",
                ok=export_ready,
                note="" if export_ready else "rendering",
            ),
        ],
    )


def _chain_up_to_render(board: Board, *, export_ready: bool) -> None:
    """Fill every link before render_report so resume_point is decided by the render alone."""
    board.save_scene_review(_review(1, degraded=False))
    board.save(
        "storyline",
        Storyline(
            red_thread="a demo",
            arc=[
                Chapter(
                    chapter=1,
                    role="hook",
                    message="m",
                    scene_numbers=[1],
                    target_seconds=10.0,
                )
            ],
        ),
    )
    board.save(
        "script",
        Script(
            language="English",
            lines=[ScriptLine(chapter=1, scene_number=1, text="a line")],
        ),
    )
    board.save("voice", VoiceArtifact(script_hash="h", mp3_path="v.mp3"))
    board.save(
        "cutlist",
        Cutlist(
            segments=[
                CutSegment(order=0, scene_number=1, start_frame=0, end_frame_exclusive=240)
            ]
        ),
    )
    board.save(
        "contact_sheet",
        ContactSheet(
            png_path="sheet.png",
            cols=1,
            rows=1,
            tiles=[ContactSheetTile(order=0, scene_number=1, frame=120, label="1")],
        ),
    )
    board.save("render_report", _report(export_ready=export_ready))


def test_the_chain_still_advances_over_an_unfinished_render_and_that_is_deliberate(
    tmp_path: Path,
) -> None:
    """Pins a REJECTED fix, with its reason, so it is not re-attempted naively.

    Making resume_point refuse a render_report whose export_ready check failed looks obviously
    right and is wrong three times over:

      * export_ready records whether the export was ready WHEN THE POLL GAVE UP. An export that
        finished a second later would be re-rendered forever on the strength of a stale False.
      * _MAX_RENDER_CYCLES refuses the second re-render such a rule demands — the run would sit
        at a resume point it is not allowed to act on.
      * status() decides DONE by presence, and the agent prompt prints both, so the team would
        be told "render_report is DONE, do not redo it" and "resume at render_report" at once.

    The honest repair belongs at the write site (do not record a render that did not happen) and
    needs a re-poll rather than a re-render. Until then the failure must at least be VISIBLE,
    which is what the next test covers.
    """
    board = _board(tmp_path)
    _chain_up_to_render(board, export_ready=False)

    assert board.resume_point([1]) == "qa_report"


def test_the_status_says_which_render_checks_failed(tmp_path: Path) -> None:
    board = _board(tmp_path)
    _chain_up_to_render(board, export_ready=False)

    entry = board.status()["artifacts"]["render_report"]
    assert entry["checks_ok"] is False
    assert entry["failed_checks"] == ["export_ready"]
