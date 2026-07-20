"""production_tools: storyline/script board writes with agent-facing validation (Slice 3, Task 4).

DB fixture is copied from ``tests/test_production_tools_review.py`` (project + asset + succeeded
analysis run + transcript + a hand-built one-scene rough cut via ``created_from=asset_id``) so
this file stays self-contained. The storyline/script tools themselves never touch ``db``/
``asset_id`` — only the board — but ``build_production_tool_specs`` still requires a valid
``(db, asset_id)`` pair, and a scene review (required by ``save_storyline``) is written straight
to the board with ``board.save_scene_review`` rather than going through the VLM-backed
``review_scene`` tool, since none of these tests need a fake VLM backend.
"""

from __future__ import annotations

from pathlib import Path

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.short_creator.board import Board
from laura.short_creator.board_models import (
    BestWindow,
    BoardMeta,
    Chapter,
    Cutlist,
    CutSegment,
    SceneReview,
    Storyline,
    VoiceArtifact,
)
from laura.short_creator.production_tools import build_production_tool_specs

FPS = 30
SCENE_FRAMES = 150  # 150 frames @ 30fps = 5.0s


def _seed_scene(tmp_path: Path) -> tuple[Database, str]:
    """Project + asset + succeeded analysis run w/ transcript + a ONE-scene rough cut.

    Returns ``(db, asset_id)``. Mirrors ``test_production_tools_review.py``'s ``_seed_scene``.
    """
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db: Database = SqliteDatabase(settings.db_path)
    db.migrate()
    project = repos.create_project(
        db,
        name="p",
        rate_num=FPS,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(tmp_path / "ws" / "proj"),
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a.mp4",
        source_path=str(tmp_path / "a.mp4"),
    )
    run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="t", config={})
    repos.start_analysis_run(db, run["id"])
    repos.insert_segment_with_words(
        db,
        asset_id=asset["id"],
        run_id=run["id"],
        speaker_id=None,
        segment={
            "start_sample": 0,
            "end_sample": 96_000,
            "start_frame": 0,
            "end_frame": SCENE_FRAMES,
            "text": "hallo welt schauen wir uns das dashboard an",
            "confidence": 1.0,
        },
        words=[],
    )
    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})
    timeline = repos.create_timeline(
        db,
        project_id=project["id"],
        name="Rough Cut",
        kind="rough_cut",
        created_from=asset["id"],
    )
    repos.add_timeline_clip(
        db,
        timeline_id=timeline["id"],
        asset_id=asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=SCENE_FRAMES,
        seq_in_frame=0,
        seq_out_frame_exclusive=SCENE_FRAMES,
        lane=0,
        role="base",
    )
    repos.replace_scenes(db, project["id"], timeline["id"], [(0, SCENE_FRAMES)])
    return db, str(asset["id"])


def _board(tmp_path: Path, asset_id: str) -> Board:
    meta = BoardMeta(
        session_id="s1",
        asset_id=asset_id,
        created_utc="2026-07-13T00:00:00Z",
        task="overview short",
        target_seconds=20.0,
    )
    return Board.create(tmp_path / "board", meta)


def _review(board: Board, scene_number: int, *, n_windows: int = 1) -> None:
    """Write a minimal valid SceneReview straight to the board (no VLM/frame-extract fakes
    needed — ``save_storyline``'s review-check only looks at ``board.scene_reviews()``)."""
    windows = [BestWindow(offset_s=float(i * 2), duration_s=1.0) for i in range(n_windows)]
    board.save_scene_review(
        SceneReview(
            scene_number=scene_number,
            src_start_frame=0,
            src_end_frame_exclusive=SCENE_FRAMES,
            description="d",
            whats_happening="h",
            hook_score=5,
            best_window=windows[0],
            windows=windows,
        )
    )


def _chapter(
    *, chapter: int = 1, role: str = "hook", scene_numbers: list[object] | None = None
) -> dict[str, object]:
    return {
        "chapter": chapter,
        "role": role,
        "message": "stop scrolling",
        "scene_numbers": scene_numbers if scene_numbers is not None else [1],
        "target_seconds": 3.0,
    }


def _seed_storyline(board: Board) -> None:
    """Straight-to-board storyline so save_script_chapter's order guard is satisfied in tests
    that are about OTHER rules (language, hygiene, validation)."""
    board.save(
        "storyline",
        Storyline(
            red_thread="r",
            arc=[
                Chapter(
                    chapter=1, role="hook", message="m", scene_numbers=[1], target_seconds=3.0
                )
            ],
        ),
    )


def test_save_storyline_happy_and_versioned(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(board, 1)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    first = specs["save_storyline"].func(red_thread="stop scrolling", chapters=[_chapter()])
    assert first == {"ok": True, "version": 1}

    second = specs["save_storyline"].func(red_thread="stop scrolling v2", chapters=[_chapter()])
    assert second == {"ok": True, "version": 2}


def test_save_storyline_rejects_invalid_role(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(board, 1)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["save_storyline"].func(
        red_thread="rt", chapters=[_chapter(role="outro")]
    )

    assert out["ok"] is False
    assert "errors" in out
    assert any("role" in err for err in out["errors"])
    assert len(out["errors"]) <= 5


def test_save_storyline_rejects_unreviewed_scenes(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    # No review saved at all -> chapter referencing scene 7 must be rejected.
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["save_storyline"].func(
        red_thread="rt", chapters=[_chapter(scene_numbers=[7])]
    )

    assert out["ok"] is False
    assert "reason" in out
    assert "7" in out["reason"]
    assert board.load("storyline") is None


def test_save_storyline_accepts_window_refs(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(board, 1, n_windows=2)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["save_storyline"].func(
        red_thread="rt",
        chapters=[_chapter(scene_numbers=[1, {"scene": 1, "window": 1}])],
    )

    assert out == {"ok": True, "version": 1}
    got = specs["get_storyline"].func()
    assert got["storyline"]["arc"][0]["scene_numbers"] == [1, {"scene": 1, "window": 1}]


def test_save_storyline_rejects_out_of_range_window(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(board, 1)  # a single window (0)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["save_storyline"].func(
        red_thread="rt", chapters=[_chapter(scene_numbers=[{"scene": 1, "window": 3}])]
    )

    assert out["ok"] is False
    assert "window 3" in out["reason"] and "scene 1" in out["reason"]
    assert board.load("storyline") is None


def test_save_storyline_rejects_duplicate_scene_window(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(board, 1)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["save_storyline"].func(
        red_thread="rt", chapters=[_chapter(scene_numbers=[1, 1])]
    )

    assert out["ok"] is False
    assert any("scene 1 window 0" in err for err in out["errors"])
    assert any(err.startswith("arc") for err in out["errors"])
    assert board.load("storyline") is None


def test_save_script_chapter_merges_per_chapter(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(board, 1)
    _review(board, 2)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}
    # The storyline comes first now — a script written before it is doomed work (order guard).
    saved = specs["save_storyline"].func(
        red_thread="r",
        chapters=[
            _chapter(chapter=1),
            _chapter(chapter=2, role="payoff_cta", scene_numbers=[2]),
        ],
    )
    assert saved["ok"] is True, saved

    out1 = specs["save_script_chapter"].func(
        chapter=1,
        lines=[
            {"scene_number": 1, "text": "line a"},
            {"scene_number": 1, "text": "line b"},
        ],
    )
    assert out1 == {
        "ok": True,
        "version": 1,
        "total_lines": 2,
        "total_words": 4,
        "chapter_words_before": 0,
        "chapter_words_after": 4,
    }

    out2 = specs["save_script_chapter"].func(
        chapter=2, lines=[{"scene_number": 1, "text": "line c"}]
    )
    assert out2 == {
        "ok": True,
        "version": 2,
        "total_lines": 3,
        "total_words": 6,
        "chapter_words_before": 0,
        "chapter_words_after": 2,
    }

    # Seed a downstream artifact directly to prove the next script write invalidates it.
    board.save("voice", VoiceArtifact(script_hash="x", mp3_path="v.mp3"))
    assert board.load("voice") is not None

    out3 = specs["save_script_chapter"].func(
        chapter=1, lines=[{"scene_number": 1, "text": "line d"}]
    )
    # Chapter 1 shrinks from 4 words ("line a", "line b") to 2 — the reply says so, and
    # the warning names the replace semantics that once cost a run half its script.
    assert out3["ok"] is True and out3["version"] == 3 and out3["total_lines"] == 2
    assert out3["chapter_words_before"] == 4 and out3["chapter_words_after"] == 2
    assert "REPLACED" in out3["warning"]

    got = specs["get_script"].func()
    assert got["ok"] is True
    lines = got["script"]["lines"]
    assert [(line["chapter"], line["text"]) for line in lines] == [
        (1, "line d"),
        (2, "line c"),
    ]
    # The script's language follows the board's, not a hard-coded "de". This board is German
    # (the default), so the saved script says German.
    assert got["script"]["language"] == "German"
    assert board.load("voice") is None  # downstream invalidated by the re-save


def test_save_script_chapter_language_follows_the_board(tmp_path: Path) -> None:
    """Live finding: save_script_chapter stamped every script "de" regardless of the board.
    Both an English gpt-5-mini run and an English gpt-5.5 run produced English text tagged
    language="de" — the artifact lied about its own contents."""
    db, asset_id = _seed_scene(tmp_path)
    meta = BoardMeta(
        session_id="s2",
        asset_id=asset_id,
        created_utc="2026-07-17T00:00:00Z",
        task="demo",
        language="English",
        target_seconds=20.0,
    )
    board = Board.create(tmp_path / "en_board", meta)
    _seed_storyline(board)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    specs["save_script_chapter"].func(
        chapter=1, lines=[{"scene_number": 1, "text": "an english line"}]
    )

    assert specs["get_script"].func()["script"]["language"] == "English"


def test_save_script_chapter_rejects_spoken_stage_directions(tmp_path: Path) -> None:
    """Live finding: three autonomous runs wrote screenplay labels into the narration, and the
    voice spoke them — "Narration:" and "CAPTION:" eight times each per film. Rejected on the
    write path so the author rewrites the line; stripping the label would leave the screen
    description behind it standing."""
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _seed_storyline(board)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["save_script_chapter"].func(
        chapter=1,
        lines=[
            {
                "scene_number": 1,
                "text": "Narration: One input produced a full org chart. "
                "CAPTION: Cold open, org chart on screen.",
            }
        ],
    )

    assert out["ok"] is False
    assert "stage-direction" in out["reason"].lower()
    assert "narration" in out["reason"].lower()
    assert board.load("script") is None, "nothing malformed reaches the board"


def test_save_script_chapter_accepts_narration_with_an_ordinary_colon(tmp_path: Path) -> None:
    """The rule catches labels, not punctuation — spoken prose keeps its colons."""
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _seed_storyline(board)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["save_script_chapter"].func(
        chapter=1,
        lines=[
            {
                "scene_number": 1,
                "text": "It writes down why it wants each one: filesystem, memory, reasoning.",
            }
        ],
    )

    assert out["ok"] is True, out


def test_save_script_chapter_validation_error(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _seed_storyline(board)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["save_script_chapter"].func(
        chapter=1, lines=[{"scene_number": 1}]  # missing required "text"
    )

    assert out["ok"] is False
    assert "errors" in out
    assert any("text" in err for err in out["errors"])
    assert board.load("script") is None


def test_get_storyline_and_script_roundtrip(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(board, 1)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    assert specs["get_storyline"].func() == {"ok": False, "reason": "no storyline on the board"}
    assert specs["get_script"].func() == {"ok": False, "reason": "no script on the board"}

    specs["save_storyline"].func(red_thread="rt", chapters=[_chapter()])
    specs["save_script_chapter"].func(chapter=1, lines=[{"scene_number": 1, "text": "hi"}])

    got_storyline = specs["get_storyline"].func()
    assert got_storyline["ok"] is True
    assert got_storyline["storyline"]["red_thread"] == "rt"
    assert got_storyline["storyline"]["version"] == 1
    assert got_storyline["storyline"]["arc"][0]["scene_numbers"] == [1]

    got_script = specs["get_script"].func()
    assert got_script["ok"] is True
    assert got_script["script"]["version"] == 1
    assert got_script["script"]["language"] == "German"  # follows the board (default German)
    assert got_script["script"]["lines"] == [{"chapter": 1, "scene_number": 1, "text": "hi"}]


# --- the order and the wipe: how run F lost a finished script twice ------------------------
# Live finding (run 48d5660a): the author wrote a COMPLETE 433-word script (v1-v6, all six
# chapters) BEFORE saving the storyline. The storyline save then invalidated the whole thing —
# the chain is storyline -> script, and nothing required the storyline to exist first. The
# author rebuilt v7-v11, and at minute 37 a second storyline save wiped that too. Its diff:
# chapter structure IDENTICAL, only messages and target_seconds changed — nothing that made
# the script invalid. v12 was then merged against an empty board (one chapter), the voice was
# gone, and the run ended at the turn budget with no film.
#
# The prompt mandates the order. Prompt rules do not bind — the contract moves into code.


def test_save_script_chapter_requires_a_storyline_first(tmp_path: Path) -> None:
    """The 433-word accident: a script written before the storyline is a script that will be
    wiped by the storyline save. Refuse loudly instead of accepting doomed work."""
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["save_script_chapter"].func(
        chapter=1, lines=[{"scene_number": 1, "text": "a line"}]
    )

    assert out["ok"] is False
    assert "save_storyline" in out["reason"]
    assert board.load("script") is None, "doomed work must not be accepted"


def test_a_structure_preserving_storyline_save_keeps_script_and_voice(tmp_path: Path) -> None:
    """The minute-37 wipe: same chapters, same scenes — only messages and targets changed.

    That change does not make the script wrong, so the script (and the voice spoken from it)
    survive. The cutlist and below stay invalidated: targets DO change segment durations.
    """
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(board, 1)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}
    specs["save_storyline"].func(red_thread="v1", chapters=[_chapter()])
    specs["save_script_chapter"].func(chapter=1, lines=[{"scene_number": 1, "text": "a line"}])
    board.save("voice", VoiceArtifact(script_hash="h", mp3_path="v.mp3"))

    out = specs["save_storyline"].func(
        red_thread="reworded entirely",
        chapters=[{**_chapter(), "message": "a different beat", "target_seconds": 9.0}],
    )

    assert out["ok"] is True
    assert out["carried_over"] == ["script", "voice"]
    script = board.load("script")
    assert script is not None, "the 64 words must survive a cosmetic storyline change"
    assert board.load("voice") is not None
    assert board.load("cutlist") is None, "targets changed — the cut must be rebuilt"


def test_a_structural_storyline_change_still_invalidates_and_says_what_was_lost(
    tmp_path: Path,
) -> None:
    """A changed scene structure CAN invalidate the script — but silently is how 64 words
    vanished. The response names the archived version so the author knows what to rebuild."""
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(board, 1)
    _review(board, 2)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}
    specs["save_storyline"].func(red_thread="v1", chapters=[_chapter(scene_numbers=[1])])
    specs["save_script_chapter"].func(chapter=1, lines=[{"scene_number": 1, "text": "a line"}])

    out = specs["save_storyline"].func(
        red_thread="v2", chapters=[_chapter(scene_numbers=[2])]
    )

    assert out["ok"] is True
    assert "script v1" in out["note"]
    assert board.load("script") is None


def test_the_first_storyline_save_keeps_its_bare_response(tmp_path: Path) -> None:
    """No prior script, nothing carried, nothing lost — no noise in the reply."""
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(board, 1)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["save_storyline"].func(red_thread="v1", chapters=[_chapter()])

    assert out == {"ok": True, "version": 1}


def test_window_notation_and_plain_scene_numbers_compare_as_the_same_structure(
    tmp_path: Path,
) -> None:
    """{"scene": 1, "window": 0} IS plain 1 — notation must not read as a structural change."""
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(board, 1)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}
    specs["save_storyline"].func(red_thread="v1", chapters=[_chapter(scene_numbers=[1])])
    specs["save_script_chapter"].func(chapter=1, lines=[{"scene_number": 1, "text": "a line"}])

    out = specs["save_storyline"].func(
        red_thread="v2", chapters=[_chapter(scene_numbers=[{"scene": 1, "window": 0}])]
    )

    assert out["carried_over"] == ["script"]
    assert board.load("script") is not None


def test_an_identical_storyline_resave_stays_a_complete_noop(tmp_path: Path) -> None:
    """Board.save short-circuits identical content without invalidating (an agent once re-saved
    upstream artifacts three times per run and each save wiped the chain below). The carry-over
    must not undo that: nothing was invalidated, so nothing may be re-saved — or the "rescue"
    itself would wipe the cutlist the no-op protected."""
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(board, 1)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}
    specs["save_storyline"].func(red_thread="r", chapters=[_chapter()])
    specs["save_script_chapter"].func(chapter=1, lines=[{"scene_number": 1, "text": "a line"}])
    board.save("voice", VoiceArtifact(script_hash="h", mp3_path="v.mp3"))
    board.save(
        "cutlist",
        Cutlist(
            segments=[CutSegment(order=0, scene_number=1, start_frame=0, end_frame_exclusive=90)]
        ),
    )

    out = specs["save_storyline"].func(red_thread="r", chapters=[_chapter()])

    assert out == {"ok": True, "version": 1}, "identical content — same version, no ceremony"
    assert board.load("cutlist") is not None, "the no-op must stay a no-op"


# --- the save must say what it did to the words, because "replace" reads as "append" -------
# Live finding (run 85f0f884): told to EXPAND the script by ~200 words, the author saved only
# the NEW lines per chapter. save_script_chapter replaces a chapter's lines with what is
# passed, so each "expansion" actually shrank the script — 263 words fell to 123 across six
# saves, and nobody noticed because the response reports only version and line count. The
# film would have carried ~50s of voice against a 174s target.


def test_the_save_reports_the_word_delta(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _seed_storyline(board)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    first = specs["save_script_chapter"].func(
        chapter=1, lines=[{"scene_number": 1, "text": "one two three four five six"}]
    )
    assert first["total_words"] == 6

    second = specs["save_script_chapter"].func(
        chapter=1, lines=[{"scene_number": 1, "text": "one two"}]
    )

    assert second["total_words"] == 2
    assert second["chapter_words_before"] == 6
    assert second["chapter_words_after"] == 2


def test_a_shrinking_save_is_named_as_a_replacement(tmp_path: Path) -> None:
    """The trap in one sentence, in the reply the agent actually reads."""
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _seed_storyline(board)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}
    specs["save_script_chapter"].func(
        chapter=1, lines=[{"scene_number": 1, "text": "a long existing line with many words"}]
    )

    out = specs["save_script_chapter"].func(
        chapter=1, lines=[{"scene_number": 1, "text": "just the new line"}]
    )

    assert "REPLACED" in out["warning"]
    assert "append" in out["warning"].lower()


def test_a_growing_save_carries_no_warning(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _seed_storyline(board)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}
    specs["save_script_chapter"].func(chapter=1, lines=[{"scene_number": 1, "text": "short"}])

    out = specs["save_script_chapter"].func(
        chapter=1, lines=[{"scene_number": 1, "text": "short plus quite a few more words now"}]
    )

    assert "warning" not in out


# --- a chapter that overflows its scenes must hear it at write time ------------------------
# Live finding (run ee65e23a, the first full agent-built film): the budget offered chapter 3
# nineteen words for the 11.5s its scenes hold; the author wrote 62 — ~25s of voice. The
# TOTAL was perfect (367 words vs 378 budget), the distribution was not, and 14 seconds of
# narration ended with no picture to carry them: video 146.8s vs voice 173.1s, voice_fits
# FAIL. The per-chapter table existed in script_budget; the author read the total.
# The overflow now speaks at the moment of the save, where it can still be fixed cheaply.


def test_a_chapter_overflowing_its_scenes_is_warned_at_save_time(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(board, 1)  # scene 1 is 5.0s long (SCENE_FRAMES @ 30fps)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}
    specs["save_storyline"].func(red_thread="r", chapters=[_chapter()])

    # 5s of scene capacity; 40 German words are ~23s of voice.
    out = specs["save_script_chapter"].func(
        chapter=1, lines=[{"scene_number": 1, "text": " ".join(["wort"] * 40)}]
    )

    assert out["ok"] is True, "reporting, not blocking — shortening is the author's move"
    assert "capacity_warning" in out
    assert "5.0s" in out["capacity_warning"]


def test_a_chapter_inside_its_capacity_gets_no_capacity_warning(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(board, 1)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}
    specs["save_storyline"].func(red_thread="r", chapters=[_chapter()])

    out = specs["save_script_chapter"].func(
        chapter=1, lines=[{"scene_number": 1, "text": "vier kurze worte hier"}]
    )

    assert out["ok"] is True
    assert "capacity_warning" not in out


# --- QA judges a render, so QA needs a render — the order-guard pattern, last link ---------
# Live finding (run 1f0438b8): after QA had shipped, a revise re-saved an upstream artifact,
# which invalidated the render — and the QA agent then saved a FRESH qa_report onto a board
# with no render_report at all. The run ended with a ship verdict sitting on top of a missing
# film: exactly the incoherence the chain exists to prevent, one link further down.


def test_save_qa_report_requires_a_render_on_the_board(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["save_qa_report"].func(verdict="ship", findings=[])

    assert out["ok"] is False
    assert "render_production" in out["reason"]
    assert board.load("qa_report") is None


def test_save_qa_report_works_once_a_render_exists(tmp_path: Path) -> None:
    from laura.short_creator.board_models import RenderCheck, RenderReport

    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    board.save(
        "render_report",
        RenderReport(
            export_id="e1",
            video_s=100.0,
            width=1920,
            height=1080,
            checks=[RenderCheck(name="export_ready", ok=True)],
        ),
    )
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["save_qa_report"].func(verdict="ship", findings=[])

    assert out == {"ok": True, "version": 1}
