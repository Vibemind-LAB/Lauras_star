"""Gate-S (scene selection): the pure structural guard + propose_scene_selection tool +
save_storyline/save_script_chapter refusal (Task GS2).

DB/board fixtures are copied verbatim from ``tests/test_production_tools_write.py`` (same
project + asset + succeeded analysis run + transcript + one-scene rough cut), with
``scene_gate=True`` added to the board's meta so the guard actually engages.
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
    SceneCandidate,
    SceneReview,
    SceneSelection,
    Storyline,
)
from laura.short_creator.production_tools import (
    build_production_tool_specs,
    scene_selection_block_reason,
)
from laura.short_creator.toolset import ToolSpec


def _sel(selected: list[int], confirmed: bool) -> SceneSelection:
    return SceneSelection(
        candidates=[
            SceneCandidate(
                scene_number=n, src_start_frame=0, src_end_frame_exclusive=100,
                thumb_frame=50, description="d", transcript_snippet="t",
                rationale="r", recommended=True,
            )
            for n in (2, 5, 7)
        ],
        selected_scene_numbers=selected if confirmed else [],
        confirmed_utc="2026-08-06T00:00:00Z" if confirmed else None,
    )


def test_gate_off_never_blocks() -> None:
    assert scene_selection_block_reason(None, [1, 2], gate_on=False) is None


def test_gate_on_without_selection_blocks() -> None:
    reason = scene_selection_block_reason(None, [2], gate_on=True)
    assert reason is not None and "propose_scene_selection" in reason


def test_gate_on_unconfirmed_blocks() -> None:
    reason = scene_selection_block_reason(_sel([], confirmed=False), [2], gate_on=True)
    assert reason is not None and "awaiting" in reason


def test_gate_on_outside_selection_names_offenders() -> None:
    reason = scene_selection_block_reason(_sel([2, 5], confirmed=True), [2, 9], gate_on=True)
    assert reason is not None and "9" in reason and "[2, 5]" in reason


def test_gate_on_subset_passes() -> None:
    assert scene_selection_block_reason(_sel([2, 5], confirmed=True), [5], gate_on=True) is None


# --- tool-level: save_storyline/save_script_chapter refuse under an active, unresolved gate --
# Fixture faktur copied verbatim from tests/test_production_tools_write.py's _seed_scene/_board/
# _review/_chapter, with scene_gate=True added to the board meta so the guard actually engages.

FPS = 30
SCENE_FRAMES = 150  # 150 frames @ 30fps = 5.0s


def _seed_scene(tmp_path: Path) -> tuple[Database, str]:
    """Project + asset + succeeded analysis run w/ transcript + a ONE-scene rough cut.

    Returns ``(db, asset_id)``. Mirrors ``test_production_tools_write.py``'s ``_seed_scene``.
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
    """Same as test_production_tools_write.py's ``_board``, but with the gate ON."""
    meta = BoardMeta(
        session_id="s1",
        asset_id=asset_id,
        created_utc="2026-08-06T00:00:00Z",
        task="overview short",
        target_seconds=20.0,
        scene_gate=True,
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


def _propose_and_confirm(
    board: Board, specs: dict[str, ToolSpec], scene_number: int = 1
) -> None:
    """propose_scene_selection, then the server-side confirm GS4's confirm service will do:
    load, stamp selected + confirmed_utc, save. No agent tool ever sets confirmed_utc."""
    specs["propose_scene_selection"].func(
        candidates=[
            {
                "scene_number": scene_number,
                "description": "d",
                "transcript_snippet": "t",
                "rationale": "r",
                "recommended": True,
            }
        ]
    )
    selection = board.load("scene_selection")
    assert isinstance(selection, SceneSelection)
    board.save(
        "scene_selection",
        selection.model_copy(
            update={
                "selected_scene_numbers": [scene_number],
                "confirmed_utc": "2026-08-06T00:00:00Z",
            }
        ),
    )


def test_save_storyline_refused_until_confirmed(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(board, 1)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    result = specs["save_storyline"].func(
        red_thread="m", chapters=[_chapter(scene_numbers=[1])]
    )

    assert result["ok"] is False
    assert "propose_scene_selection" in result["reason"]
    assert board.load("storyline") is None


def test_propose_then_confirm_then_storyline_stamps_parent(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(board, 1)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    proposed = specs["propose_scene_selection"].func(
        candidates=[
            {
                "scene_number": 1,
                "description": "d",
                "transcript_snippet": "t",
                "rationale": "r",
                "recommended": True,
            }
        ]
    )
    assert proposed["ok"] is True

    # confirm server-side, as GS4's service will: load, stamp selected + confirmed_utc, save
    selection = board.load("scene_selection")
    assert isinstance(selection, SceneSelection)
    board.save(
        "scene_selection",
        selection.model_copy(
            update={"selected_scene_numbers": [1], "confirmed_utc": "2026-08-06T00:00:00Z"}
        ),
    )

    result = specs["save_storyline"].func(
        red_thread="m", chapters=[_chapter(scene_numbers=[1])]
    )

    assert result["ok"] is True
    storyline = board.load("storyline")
    assert isinstance(storyline, Storyline)
    assert "scene_selection" in storyline.parents


def test_save_script_chapter_rejects_unselected_scene(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(board, 1)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}
    _propose_and_confirm(board, specs, scene_number=1)
    saved = specs["save_storyline"].func(
        red_thread="m", chapters=[_chapter(scene_numbers=[1])]
    )
    assert saved["ok"] is True, saved

    out = specs["save_script_chapter"].func(
        chapter=1, lines=[{"scene_number": 2, "text": "a line"}]
    )

    assert out["ok"] is False
    assert "outside" in out["reason"]
    assert board.load("script") is None
