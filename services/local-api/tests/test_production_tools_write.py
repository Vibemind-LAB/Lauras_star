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
from laura.short_creator.board_models import BestWindow, BoardMeta, SceneReview, VoiceArtifact
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


def _review(board: Board, scene_number: int) -> None:
    """Write a minimal valid SceneReview straight to the board (no VLM/frame-extract fakes
    needed — ``save_storyline``'s review-check only looks at ``board.scene_reviews()``)."""
    board.save_scene_review(
        SceneReview(
            scene_number=scene_number,
            src_start_frame=0,
            src_end_frame_exclusive=SCENE_FRAMES,
            description="d",
            whats_happening="h",
            hook_score=5,
            best_window=BestWindow(offset_s=0.0, duration_s=1.0),
        )
    )


def _chapter(
    *, chapter: int = 1, role: str = "hook", scene_numbers: list[int] | None = None
) -> dict[str, object]:
    return {
        "chapter": chapter,
        "role": role,
        "message": "stop scrolling",
        "scene_numbers": scene_numbers if scene_numbers is not None else [1],
        "target_seconds": 3.0,
    }


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


def test_save_script_chapter_merges_per_chapter(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out1 = specs["save_script_chapter"].func(
        chapter=1,
        lines=[
            {"scene_number": 1, "text": "line a"},
            {"scene_number": 1, "text": "line b"},
        ],
    )
    assert out1 == {"ok": True, "version": 1, "total_lines": 2}

    out2 = specs["save_script_chapter"].func(
        chapter=2, lines=[{"scene_number": 1, "text": "line c"}]
    )
    assert out2 == {"ok": True, "version": 2, "total_lines": 3}

    # Seed a downstream artifact directly to prove the next script write invalidates it.
    board.save("voice", VoiceArtifact(script_hash="x", mp3_path="v.mp3"))
    assert board.load("voice") is not None

    out3 = specs["save_script_chapter"].func(
        chapter=1, lines=[{"scene_number": 1, "text": "line d"}]
    )
    assert out3 == {"ok": True, "version": 3, "total_lines": 2}

    got = specs["get_script"].func()
    assert got["ok"] is True
    lines = got["script"]["lines"]
    assert [(line["chapter"], line["text"]) for line in lines] == [
        (1, "line d"),
        (2, "line c"),
    ]
    assert got["script"]["language"] == "de"
    assert board.load("voice") is None  # downstream invalidated by the re-save


def test_save_script_chapter_validation_error(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
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
    assert got_script["script"]["language"] == "de"
    assert got_script["script"]["lines"] == [{"chapter": 1, "scene_number": 1, "text": "hi"}]
