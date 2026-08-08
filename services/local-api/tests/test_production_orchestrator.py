"""production_orchestrator: board lifecycle, resume-aware task contract, magentic-only ladder,
run entrypoint (Slice 3, Task 8).

DB/asset fixture mirrors ``tests/test_production_tools_review.py``'s ``_seed_scene`` (project +
asset + succeeded analysis run + transcript + a hand-built ONE-scene rough cut via
``created_from=asset_id``) so this file stays self-contained.

The escalation-ladder tests mirror ``tests/test_short_creator_orchestrator.py``'s scripted-
``ExecuteFn`` pattern (no autogen, no LLM) but keyed by stage only: v2 has no GraphFlow fallback,
so ``kind`` is always ``"magentic"`` here. The default executor (build + run the real production
team via ``asyncio.run``) is manual-to-verify, same as v1's ``orchestrator._default_execute``.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.short_creator import orchestrator, production_orchestrator, providers
from laura.short_creator.board import Board
from laura.short_creator.board_models import (
    BestWindow,
    BoardMeta,
    Chapter,
    RenderReport,
    SceneCandidate,
    SceneReview,
    SceneSelection,
    Script,
    ScriptLine,
    Storyline,
)

FPS = 30
SCENE_FRAMES = 150  # 150 frames @ 30fps = 5.0s

# script maps stage -> ("ok"|"hard_fail", weak) or an Exception to raise. Keyed by stage only
# (not (stage, kind), unlike v1) because v2's ladder only ever calls team="magentic".
_Script = dict[str, "tuple[str, bool] | Exception"]


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


def _review(scene_number: int = 1) -> SceneReview:
    return SceneReview(
        scene_number=scene_number,
        src_start_frame=0,
        src_end_frame_exclusive=SCENE_FRAMES,
        description="dashboard",
        whats_happening="scrolls",
        hook_score=7,
        best_window=BestWindow(offset_s=0.0, duration_s=3.0),
    )


def _make_execute(script: _Script) -> tuple[orchestrator.ExecuteFn, list[tuple[str, str]]]:
    calls: list[tuple[str, str]] = []

    def execute(
        db: Database,
        config: providers.AgentConfig,
        stage: str,
        kind: str,
        task: str,
    ) -> orchestrator.StageOutcome:
        calls.append((stage, kind))
        assert kind == "magentic"  # v2 has no graph fallback — always "magentic"
        spec = script[stage]
        if isinstance(spec, Exception):
            raise spec
        status, weak = spec
        return orchestrator.StageOutcome(
            status=cast(orchestrator.Status, status),
            weak=weak,
            summary="done",
            team=cast(orchestrator.TeamKind, kind),
            stage=cast(providers.Stage, stage),
        )

    return execute, calls


# --- board_root_for ------------------------------------------------------------------------


def test_board_root_under_workspace(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)

    root = production_orchestrator.board_root_for(db, asset_id, "sess1")

    assert root == tmp_path / "ws" / "proj" / "agent-runs" / "sess1" / "board"
    assert not root.exists()  # pure path construction — no filesystem side effect


# --- build_production_task ------------------------------------------------------------------


def test_task_text_contains_contract_and_resume(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    root = production_orchestrator.board_root_for(db, asset_id, "sess1")
    meta = BoardMeta(
        session_id="sess1",
        asset_id=asset_id,
        created_utc="2026-07-13T00:00:00+00:00",
        task="overview short",
        target_seconds=20.0,
    )
    board = Board.create(root, meta)

    fresh = production_orchestrator.build_production_task(
        db, board, asset_id=asset_id, task="overview short", target_seconds=20
    )
    assert "viral arc" in fresh.lower()
    assert "do not redo" in fresh.lower()
    assert "scene_reviews:1" in fresh  # fresh board -> scene 1 not reviewed yet
    # The contact-sheet checkpoint is part of the mandatory order AND documented as the known
    # steer-by-message pattern (stop at the Kontaktbogen / render later) — no session state.
    assert "save_contact_sheet" in fresh
    assert "Kontaktbogen" in fresh
    assert "dann stopp" in fresh
    assert "render jetzt" in fresh

    board.save_scene_review(_review(1))
    board.save(
        "storyline",
        Storyline(
            red_thread="thread",
            arc=[
                Chapter(
                    chapter=1, role="hook", message="m", scene_numbers=[1], target_seconds=3.0
                )
            ],
        ),
    )

    resumed = production_orchestrator.build_production_task(
        db, board, asset_id=asset_id, task="overview short", target_seconds=20
    )
    assert "storyline: DONE" in resumed
    assert "Resume point: script" in resumed


def test_task_text_carries_the_language_switch_rule(tmp_path: Path) -> None:
    """"Sprache folgt dem Input" (SP3): the team must know that a follow-up asking for another
    language is a set_board_language call FIRST, then every chapter rewritten in it — never a
    partial switch that leaves some chapters in the old language."""
    db, asset_id = _seed_scene(tmp_path)
    root = production_orchestrator.board_root_for(db, asset_id, "sess1")
    meta = BoardMeta(
        session_id="sess1",
        asset_id=asset_id,
        created_utc="2026-07-13T00:00:00+00:00",
        task="overview short",
        target_seconds=20.0,
    )
    board = Board.create(root, meta)

    task = production_orchestrator.build_production_task(
        db, board, asset_id=asset_id, task="overview short", target_seconds=20
    )

    assert "set_board_language" in task


def test_task_text_follow_up_block_only_with_message(tmp_path: Path) -> None:
    """The USER FOLLOW-UP REQUEST section (+ its revert/re-save/never-redo instructions) only
    appears when ``message`` is passed; the board-status block also grows an ``[archived: ...]``
    suffix per artifact, but only for artifacts that actually have an archived version, and only
    while a message is present (a fresh, non-follow-up call stays exactly as before)."""
    db, asset_id = _seed_scene(tmp_path)
    root = production_orchestrator.board_root_for(db, asset_id, "sess1")
    meta = BoardMeta(
        session_id="sess1",
        asset_id=asset_id,
        created_utc="2026-07-13T00:00:00+00:00",
        task="overview short",
        target_seconds=20.0,
    )
    board = Board.create(root, meta)
    chapter = Chapter(chapter=1, role="hook", message="m", scene_numbers=[1], target_seconds=3.0)
    board.save("storyline", Storyline(red_thread="thread v1", arc=[chapter]))
    board.save("storyline", Storyline(red_thread="thread v2", arc=[chapter]))  # -> v2, archives v1

    without_message = production_orchestrator.build_production_task(
        db, board, asset_id=asset_id, task="overview short", target_seconds=20
    )
    assert "USER FOLLOW-UP REQUEST" not in without_message
    assert "[archived:" not in without_message

    with_message = production_orchestrator.build_production_task(
        db,
        board,
        asset_id=asset_id,
        task="overview short",
        target_seconds=20,
        message="make the hook punchier",
    )
    assert "USER FOLLOW-UP REQUEST" in with_message
    assert "make the hook punchier" in with_message
    assert "revert_artifact" in with_message
    assert "archived_versions" in with_message
    assert "highest affected" in with_message.lower()
    assert "never redo" in with_message.lower()
    assert "intact upstream" in with_message.lower()
    assert "storyline: DONE (v2) [archived: v1]" in with_message


def test_task_text_carries_source_scene_transcripts(tmp_path: Path) -> None:
    """Live 2026-08-04: the task string carried only scout hits — the team never saw what is
    actually SAID per scene, and the scripts came out as invented marketing copy. The task now
    carries per-scene transcript excerpts as ground truth plus the grounding rule."""
    db, asset_id = _seed_scene(tmp_path)
    root = production_orchestrator.board_root_for(db, asset_id, "sess1")
    meta = BoardMeta(
        session_id="sess1",
        asset_id=asset_id,
        created_utc="2026-08-04T00:00:00+00:00",
        task="overview short",
        target_seconds=20.0,
    )
    board = Board.create(root, meta)

    task = production_orchestrator.build_production_task(
        db, board, asset_id=asset_id, task="overview short", target_seconds=20
    )

    assert "SOURCE MATERIAL" in task
    assert "hallo welt schauen wir uns das dashboard an" in task
    assert "scene 1" in task
    assert "get_scene_transcript" in task
    assert "invented" in task.lower()


def test_task_text_excerpts_are_capped_per_scene(tmp_path: Path) -> None:
    """A scene's excerpt is a taste, not the full text — the tool has the rest. An uncapped
    dump of a long talking-head scene would bloat every run's task string."""
    db, asset_id = _seed_scene(tmp_path)
    long_text = " ".join(f"wortnummer{i:03d}" for i in range(60))  # ~840 chars, unique tokens
    run = repos.create_analysis_run(db, asset_id=asset_id, pipeline_version="t2", config={})
    repos.start_analysis_run(db, run["id"])
    repos.insert_segment_with_words(
        db,
        asset_id=asset_id,
        run_id=run["id"],
        speaker_id=None,
        segment={
            "start_sample": 0,
            "end_sample": 96_000,
            "start_frame": 0,
            "end_frame": SCENE_FRAMES,
            "text": long_text,
            "confidence": 1.0,
        },
        words=[],
    )
    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})
    root = production_orchestrator.board_root_for(db, asset_id, "sess1")
    meta = BoardMeta(
        session_id="sess1",
        asset_id=asset_id,
        created_utc="2026-08-04T00:00:00+00:00",
        task="overview short",
        target_seconds=20.0,
    )
    board = Board.create(root, meta)

    task = production_orchestrator.build_production_task(
        db, board, asset_id=asset_id, task="overview short", target_seconds=20
    )

    assert long_text not in task, "the full text belongs to get_scene_transcript, not the task"
    assert long_text[:300] in task


def test_task_carries_scene_facts_for_selected_scenes(tmp_path: Path) -> None:
    """VS5 (voice-per-scene, Plan 2): once Gate S's pick is confirmed, the task carries a SCENE
    FACTS block — SHOWS (candidate description) + SAYS (candidate transcript_snippet) — for
    every SELECTED scene only, so scene_author writes each line FOR its scene instead of
    free-floating marketing copy. Candidate scene 1 is proposed but NOT selected — its facts
    must not leak onto the task."""
    db, asset_id = _seed_scene(tmp_path)
    root = production_orchestrator.board_root_for(db, asset_id, "sess1")
    meta = BoardMeta(
        session_id="sess1",
        asset_id=asset_id,
        created_utc="2026-08-06T00:00:00+00:00",
        task="overview short",
        target_seconds=20.0,
        scene_gate=True,
    )
    board = Board.create(root, meta)
    board.save(
        "scene_selection",
        SceneSelection(
            candidates=[
                SceneCandidate(
                    scene_number=1,
                    src_start_frame=0,
                    src_end_frame_exclusive=SCENE_FRAMES,
                    thumb_frame=SCENE_FRAMES // 2,
                    description="Startbildschirm",
                    transcript_snippet="hallo welt",
                    rationale="opener, not chosen",
                ),
                SceneCandidate(
                    scene_number=2,
                    src_start_frame=0,
                    src_end_frame_exclusive=SCENE_FRAMES,
                    thumb_frame=SCENE_FRAMES // 2,
                    description="n8n Flow im Bild",
                    transcript_snippet="wir bauen den flow",
                    rationale="core feature",
                    recommended=True,
                ),
            ],
            selected_scene_numbers=[2],
            confirmed_utc="2026-08-06T00:05:00+00:00",
        ),
    )

    task = production_orchestrator.build_production_task(
        db, board, asset_id=asset_id, task="overview short", target_seconds=20
    )

    assert "SCENE FACTS" in task
    assert "n8n Flow im Bild" in task and "wir bauen den flow" in task
    assert "Startbildschirm" not in task  # scene 1 proposed but not selected


def test_task_carries_scene_facts_from_reviews_without_confirmed_selection(
    tmp_path: Path,
) -> None:
    """Companion: no confirmed SceneSelection on the board (gate off entirely, or gate on but
    nothing proposed/confirmed yet) falls back to the board's scene_reviews for the SCENE FACTS
    block — SHOWS only, since a plain review carries no transcript_snippet. Old, gate-off
    sessions benefit from the same grounding the confirmed-selection branch gives new ones."""
    db, asset_id = _seed_scene(tmp_path)
    root = production_orchestrator.board_root_for(db, asset_id, "sess1")
    meta = BoardMeta(
        session_id="sess1",
        asset_id=asset_id,
        created_utc="2026-08-06T00:00:00+00:00",
        task="overview short",
        target_seconds=20.0,
    )
    board = Board.create(root, meta)
    board.save_scene_review(_review(1))

    task = production_orchestrator.build_production_task(
        db, board, asset_id=asset_id, task="overview short", target_seconds=20
    )

    assert "SCENE FACTS" in task
    assert "scene 1: SHOWS dashboard" in task


# --- run_production -----------------------------------------------------------------------


def test_run_production_creates_board_and_reports(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    config = providers.resolve_from_env({})

    # Asset missing is checked before anything else touches the board.
    missing = production_orchestrator.run_production(
        db, config, asset_id="does-not-exist", session_id="sess1", task="t"
    )
    assert missing["ok"] is False
    assert missing["error"] == "asset not found"
    assert missing["session_id"] == "sess1"
    assert missing["restored"] == []  # RED: preflight-failure returns ALWAYS carry restored

    execute, calls = _make_execute({"A": ("ok", False)})
    result = production_orchestrator.run_production(
        db,
        config,
        asset_id=asset_id,
        session_id="sess1",
        task="overview short",
        target_seconds=20,
        execute=execute,
    )

    assert result["ok"] is True
    assert result["stage"] == "A"
    assert result["team"] == "magentic"
    assert result["escalated"] is False
    assert result["session_id"] == "sess1"
    assert result["export_id"] is None
    assert result["resume_point"]  # non-empty
    assert result["board"]["meta"]["session_id"] == "sess1"
    assert calls == [("A", "magentic")]
    root = production_orchestrator.board_root_for(db, asset_id, "sess1")
    assert (root / "meta.json").is_file()


def test_run_production_message_on_fresh_board_is_error(tmp_path: Path) -> None:
    """A follow-up ``message`` assumes a prior production already exists on the board. A
    session that has never been run has no board yet, so this is reported as an error - and,
    unlike a plain fresh run (no message), no board directory gets created for it."""
    db, asset_id = _seed_scene(tmp_path)
    config = providers.resolve_from_env({})
    execute, calls = _make_execute({})  # empty script — must never be reached

    result = production_orchestrator.run_production(
        db,
        config,
        asset_id=asset_id,
        session_id="sess1",
        task="overview short",
        message="go back to the previous storyline",
        execute=execute,
    )

    assert result["ok"] is False
    assert result["error"] == "unknown session (no board)"
    assert result["asset_id"] == asset_id
    assert result["session_id"] == "sess1"
    assert result["restored"] == []  # RED: preflight-failure returns ALWAYS carry restored
    assert calls == []  # never reached team execution
    root = production_orchestrator.board_root_for(db, asset_id, "sess1")
    assert not root.exists()


def test_run_production_message_run_reaches_team_with_follow_up_text(tmp_path: Path) -> None:
    """A follow-up ``message`` against an EXISTING board runs normally, and the user's text
    ends up verbatim in the task text handed to the team (captured via a fake execute)."""
    db, asset_id = _seed_scene(tmp_path)
    root = production_orchestrator.board_root_for(db, asset_id, "sess1")
    meta = BoardMeta(
        session_id="sess1",
        asset_id=asset_id,
        created_utc="2026-01-01T00:00:00+00:00",
        task="original task",
        target_seconds=20.0,
    )
    Board.create(root, meta).save_scene_review(_review(1))
    config = providers.resolve_from_env({})
    captured_tasks: list[str] = []

    def execute(
        db: Database,
        config: providers.AgentConfig,
        stage: str,
        kind: str,
        task: str,
    ) -> orchestrator.StageOutcome:
        captured_tasks.append(task)
        return orchestrator.StageOutcome(
            status="ok",
            weak=False,
            summary="done",
            team=cast(orchestrator.TeamKind, kind),
            stage=cast(providers.Stage, stage),
        )

    result = production_orchestrator.run_production(
        db,
        config,
        asset_id=asset_id,
        session_id="sess1",
        task="overview short",
        message="swap the hook for the dashboard scene",
        execute=execute,
    )

    assert result["ok"] is True
    assert len(captured_tasks) == 1
    assert "swap the hook for the dashboard scene" in captured_tasks[0]
    assert "USER FOLLOW-UP REQUEST" in captured_tasks[0]


def test_run_production_orphaned_asset_never_raises(tmp_path: Path) -> None:
    """An asset row whose project has been deleted out from under it (board_root_for's own
    ValueError, per its docstring) must be reported the same way a missing asset is, not
    raised. FK-enforced cascade deletes normally remove the asset along with its project (see
    repos.delete_project), so this orphan state is reproduced directly on the fixture, the way
    it could arise from any out-of-band data repair that skips the ORM/repo layer."""
    db, asset_id = _seed_scene(tmp_path)
    with db.connection() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "UPDATE media_assets SET project_id = ? WHERE id = ?", ("does-not-exist", asset_id)
        )
    config = providers.resolve_from_env({})
    execute, calls = _make_execute({"A": ("ok", False)})

    result = production_orchestrator.run_production(
        db, config, asset_id=asset_id, session_id="sess1", task="t", execute=execute
    )

    assert result["ok"] is False
    assert result["error"] == "project not found"
    assert result["asset_id"] == asset_id
    assert result["session_id"] == "sess1"
    assert result["restored"] == []  # RED: preflight-failure returns ALWAYS carry restored
    assert calls == []  # never reached team execution


def test_run_production_reopens_existing_board(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    root = production_orchestrator.board_root_for(db, asset_id, "sess1")
    meta = BoardMeta(
        session_id="sess1",
        asset_id=asset_id,
        created_utc="2026-01-01T00:00:00+00:00",
        task="original task",
        target_seconds=20.0,
    )
    Board.create(root, meta).save_scene_review(_review(1))

    config = providers.resolve_from_env({})
    execute, _calls = _make_execute({"A": ("ok", False)})
    result = production_orchestrator.run_production(
        db,
        config,
        asset_id=asset_id,
        session_id="sess1",
        task="a different task text",
        target_seconds=99,
        execute=execute,
    )

    assert result["board"]["meta"]["created_utc"] == "2026-01-01T00:00:00+00:00"
    assert result["board"]["meta"]["task"] == "original task"
    assert result["board"]["scene_reviews"]["count"] == 1
    reopened = Board.open(root)
    assert reopened.scene_reviews()[0].version == 1  # not overwritten


def test_run_production_escalates_on_hard_fail(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    config = providers.resolve_from_env({})
    execute, calls = _make_execute({"A": ("hard_fail", False), "B": ("ok", False)})

    result = production_orchestrator.run_production(
        db, config, asset_id=asset_id, session_id="sess1", task="t", execute=execute
    )

    assert result["ok"] is True
    assert result["stage"] == "B"
    assert result["escalated"] is True
    assert calls == [("A", "magentic"), ("B", "magentic")]


def test_run_production_export_id_from_render_report(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    root = production_orchestrator.board_root_for(db, asset_id, "sess1")
    meta = BoardMeta(
        session_id="sess1",
        asset_id=asset_id,
        created_utc="2026-01-01T00:00:00+00:00",
        task="t",
        target_seconds=20.0,
    )
    board = Board.create(root, meta)
    board.save(
        "render_report",
        RenderReport(export_id="exp123", video_s=12.0, width=1080, height=1920, checks=[]),
    )

    config = providers.resolve_from_env({})
    execute, _calls = _make_execute({"A": ("ok", False)})
    result = production_orchestrator.run_production(
        db, config, asset_id=asset_id, session_id="sess1", task="t", execute=execute
    )

    assert result["export_id"] == "exp123"


def test_run_production_never_raises(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    config = providers.resolve_from_env({})
    execute, calls = _make_execute({"A": RuntimeError("boom"), "B": RuntimeError("boom again")})

    result = production_orchestrator.run_production(
        db, config, asset_id=asset_id, session_id="sess1", task="t", execute=execute
    )

    assert result["ok"] is False
    assert result["status"] == "hard_fail"
    assert result["escalated"] is True
    assert calls == [("A", "magentic"), ("B", "magentic")]


# --- ok is the loop's status; complete is the production's ---------------------------------
# Live finding: a run returned ok=true, weak=true, export_id=null, resume_point="script".
# Every field was accurate and the whole was misleading — ok says the agent loop did not
# hard-fail, nothing about whether a video exists. A caller reads ok=true as "there is a
# video". There was none. resume_point already knew ("done" only when the chain is complete);
# the result just never asked.


def _fill_chain(board: Board) -> None:
    """Save one valid artifact for every step of the chain, so resume_point == 'done'."""
    from laura.short_creator.board_models import (
        ContactSheet,
        ContactSheetTile,
        Cutlist,
        CutSegment,
        QaReport,
        RenderCheck,
        Script,
        ScriptLine,
        VoiceArtifact,
    )

    board.save("storyline", Storyline(red_thread="t", arc=[Chapter(
        chapter=1, role="hook", message="m", scene_numbers=[1], target_seconds=3.0)]))
    board.save("script", Script(
        language="German", lines=[ScriptLine(chapter=1, scene_number=1, text="Hallo")]))
    board.save("voice", VoiceArtifact(script_hash="h", mp3_path="/tmp/v.mp3", voice_s=3.0))
    board.save("cutlist", Cutlist(segments=[CutSegment(
        order=0, scene_number=1, start_frame=0, end_frame_exclusive=90)]))
    board.save("contact_sheet", ContactSheet(png_path="/tmp/s.png", cols=1, rows=1, tiles=[
        ContactSheetTile(order=0, scene_number=1, frame=45, label="0 S1")]))
    board.save("render_report", RenderReport(
        export_id="exp1", video_s=3.0, voice_s=3.0, width=1920, height=1080,
        checks=[RenderCheck(name="export_ready", ok=True)]))
    board.save("qa_report", QaReport(verdict="ship"))


def test_a_run_that_stopped_early_reports_ok_but_not_complete(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    config = providers.resolve_from_env({})
    execute, _calls = _make_execute({"A": ("ok", True)})  # loop survives, writes no board

    result = production_orchestrator.run_production(
        db, config, asset_id=asset_id, session_id="sess_early", task="demo", execute=execute)

    assert result["ok"] is True, "the loop really did survive — that meaning is unchanged"
    assert result["complete"] is False, "but nothing was produced, and the result must say so"
    assert result["export_id"] is None


def test_a_finished_board_reports_complete(tmp_path: Path) -> None:
    """resume_point already knew; the result just never asked."""
    db, asset_id = _seed_scene(tmp_path)
    root = production_orchestrator.board_root_for(db, asset_id, "sess_done")
    meta = BoardMeta(
        session_id="sess_done",
        asset_id=asset_id,
        created_utc="2026-01-01T00:00:00+00:00",
        task="demo",
        target_seconds=20.0,
    )
    board = Board.create(root, meta)
    board.save_scene_review(_review(1))
    _fill_chain(board)
    config = providers.resolve_from_env({})
    execute, _calls = _make_execute({"A": ("ok", False)})

    result = production_orchestrator.run_production(
        db, config, asset_id=asset_id, session_id="sess_done", task="demo", execute=execute)

    assert result["complete"] is True
    assert result["resume_point"] == "done"


def test_the_charter_names_the_way_out_of_scarce_footage(tmp_path: Path) -> None:
    """Pins the escape hatch the run-M deadlock was missing.

    The charter forbade shortening the voice; capacity said the scenes could not stretch
    further. Cornered between the two, the orchestrator invented a third option — acquiring
    longer scene files from an "asset owner" — and spent twenty minutes instructing agents to
    contact a person who does not exist, ending the run with no render. The contract now
    closes that door (the footage is fixed, nobody to ask) and opens the honest one: a
    shorter script, pre-authorized, no permission loop.
    """
    db, asset_id = _seed_scene(tmp_path)
    root = production_orchestrator.board_root_for(db, asset_id, "sess-charter")
    meta = BoardMeta(
        session_id="sess-charter",
        asset_id=asset_id,
        created_utc="2026-07-19T00:00:00+00:00",
        task="demo",
        target_seconds=174.0,
    )
    board = Board.create(root, meta)

    task = production_orchestrator.build_production_task(
        db, board, asset_id=asset_id, task="demo", target_seconds=174
    )

    assert "THE FOOTAGE IS FIXED" in task
    assert "never plan around acquiring material" in task
    assert "SHORTER SCRIPT" in task
    assert "capacity_warning" in task


def test_the_task_names_every_agents_tools(tmp_path: Path) -> None:
    """Pins the tool-ownership roster, measured across three runs of delegation confusion.

    The magentic orchestrator repeatedly routed writes to agents that do not hold the tool:
    run J's coding_agent was told to save the storyline ("no exposed save endpoint for me"),
    run N ended with the orchestrator and story_architect asking EACH OTHER to call
    save_script_chapter — thirteen save_storyline attempts, sixteen refusals, no script, and
    a hallucinated "finished film" in the closing statement. Agents cannot discover each
    other's toolsets; the contract now prints the roster, derived from the same AgentSpec
    definitions the team is built from, so it cannot drift.
    """
    db, asset_id = _seed_scene(tmp_path)
    root = production_orchestrator.board_root_for(db, asset_id, "sess-roster")
    meta = BoardMeta(
        session_id="sess-roster",
        asset_id=asset_id,
        created_utc="2026-07-19T00:00:00+00:00",
        task="demo",
        target_seconds=174.0,
    )
    board = Board.create(root, meta)

    task = production_orchestrator.build_production_task(
        db, board, asset_id=asset_id, task="demo", target_seconds=174
    )

    assert "TOOL OWNERSHIP" in task
    # The two misroutings that actually happened, pinned by name.
    assert re.search(r"story_architect:[^\n]*save_storyline", task)
    assert re.search(r"scene_author:[^\n]*save_script_chapter", task)
    assert re.search(r"coding_agent:[^\n]*render_production", task)
    assert "ONLY the named agent can call its tools" in task


# --- the automatic render restore was tried, review-killed, and stays out ------------------
# It brought back the newest archived render when its script_hash matched the current script.
# Review refuted it with a live repro on this branch: a render is a projection of the CUTLIST
# and the VOICE as much as of the script text, and script_hash covers neither. Reverting the
# cutlist (a documented follow-up flow) while the script stayed identical resurrected a film
# cut from the ABANDONED cutlist — reported stale=False, checks_ok=True. And in the scenario
# that motivated the restore (script revise back to the rendered text), the revise had also
# wiped voice+cutlist+sheet, so the mandated rebuild re-invalidated the restored render before
# anything used it, while the entry task text claimed it was DONE. The honest repair is a
# provenance CHAIN (render -> cutlist -> voice) plus a full-suffix restore — its own design.


def _script_artifact(text: str) -> Script:
    return Script(language="English", lines=[ScriptLine(chapter=1, scene_number=1, text=text)])


def _render_for(script: Script) -> RenderReport:
    from laura.short_creator.board_models import RenderCheck, script_hash

    return RenderReport(
        export_id="e-orphaned",
        video_s=135.0,
        width=1920,
        height=1080,
        script_hash=script_hash(script.lines),
        checks=[RenderCheck(name="export_ready", ok=True)],
    )


def test_an_orphaned_render_stays_orphaned_and_that_is_deliberate(tmp_path: Path) -> None:
    """Pins the rejection: no resume-time resurrection keyed on script text alone."""
    db, asset_id = _seed_scene(tmp_path)
    config = providers.resolve_from_env({})
    root = production_orchestrator.board_root_for(db, asset_id, "sess-norestore")
    board = Board.create(
        root,
        BoardMeta(
            session_id="sess-norestore",
            asset_id=asset_id,
            created_utc="2026-07-19T00:00:00+00:00",
            task="demo",
            language="English",
            target_seconds=174.0,
        ),
    )
    final = _script_artifact("the line that was rendered")
    board.save("script", final)
    board.save("render_report", _render_for(final))
    board.save("script", _script_artifact("a different draft"))
    board.save("script", final.model_copy(deep=True))
    assert board.load("render_report") is None, "the revise really orphaned the render"

    execute, _calls = _make_execute({"A": ("ok", False)})
    result = production_orchestrator.run_production(
        db,
        config,
        asset_id=asset_id,
        session_id="sess-norestore",
        task="demo",
        target_seconds=174,
        execute=execute,
    )

    assert result["export_id"] is None
    assert board.load("render_report") is None, "no resurrection on script text alone"


def test_run_production_restores_the_matching_suffix_and_reports_it(tmp_path: Path) -> None:
    """Entry restore: the resume contract reads DONE, the result names what came back."""
    from laura.short_creator.board_models import VoiceArtifact, content_hash

    db, asset_id = _seed_scene(tmp_path)
    config = providers.resolve_from_env({})
    root = production_orchestrator.board_root_for(db, asset_id, "sess-suffix")
    board = Board.create(
        root,
        BoardMeta(
            session_id="sess-suffix",
            asset_id=asset_id,
            created_utc="2026-07-20T00:00:00+00:00",
            task="demo",
            language="English",
            target_seconds=174.0,
        ),
    )
    board.save(
        "storyline",
        Storyline(
            red_thread="r",
            arc=[
                Chapter(
                    chapter=1, role="hook", message="m", scene_numbers=[1], target_seconds=10.0
                )
            ],
        ),
    )
    final = _script_artifact("the rendered line")
    board.save("script", final)
    script_now = board.load("script")
    assert script_now is not None
    board.save(
        "voice",
        VoiceArtifact(
            script_hash="k",
            mp3_path="voiceovers/a.mp3",
            parents={"script": content_hash(script_now)},
        ),
    )
    board.save("script", _script_artifact("a different draft"))
    board.save("script", final.model_copy(deep=True))
    assert board.load("voice") is None

    events: list[dict[str, object]] = []
    captured_tasks: list[str] = []

    def execute(
        db: Database,
        config: providers.AgentConfig,
        stage: str,
        kind: str,
        task: str,
    ) -> orchestrator.StageOutcome:
        captured_tasks.append(task)
        return orchestrator.StageOutcome(
            status="ok",
            weak=False,
            summary="done",
            team=cast(orchestrator.TeamKind, kind),
            stage=cast(providers.Stage, stage),
        )

    result = production_orchestrator.run_production(
        db,
        config,
        asset_id=asset_id,
        session_id="sess-suffix",
        task="demo",
        target_seconds=174,
        execute=execute,
        event_sink=events.append,
    )

    assert result["restored"] == ["voice"]
    board_after = Board.open(root)
    assert board_after.load("voice") is not None
    assert {"type": "restored", "artifacts": ["voice"]} in events
    # Pins the restore-before-task-text ordering (the predecessor feature's task-text lie was
    # exactly this: claiming DONE for something that then got wiped again). This is a PARTIAL
    # restore (cutlist stays missing, so the team still runs) — the exact marker
    # ``build_production_task``'s ``_artifact_line`` emits for a present artifact.
    assert len(captured_tasks) == 1
    assert "  - voice: DONE (v1)" in captured_tasks[0]


def test_run_production_reports_empty_restored_when_nothing_came_back(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    config = providers.resolve_from_env({})
    execute, _calls = _make_execute({"A": ("ok", False)})
    events: list[dict[str, object]] = []

    result = production_orchestrator.run_production(
        db,
        config,
        asset_id=asset_id,
        session_id="sess-plain",
        task="demo",
        execute=execute,
        event_sink=events.append,
    )

    assert result["restored"] == []
    # RED: no restored event should be emitted when nothing was restored
    assert not any(event.get("type") == "restored" for event in events)


# --- config_warning: the local-7B trap gets a voice in the run log -------------------------


def test_run_production_logs_a_config_warning_line_for_local_ollama(tmp_path: Path) -> None:
    """One {"type": "config_warning"} line in the run log when the text agents are local —
    the run log is where the 55-minute-invisibility class of incidents gets diagnosed."""
    db, asset_id = _seed_scene(tmp_path)
    config = providers.resolve_from_env({})  # zero env -> ollama default
    execute, _calls = _make_execute({"A": ("ok", False)})
    events: list[dict[str, object]] = []

    result = production_orchestrator.run_production(
        db,
        config,
        asset_id=asset_id,
        session_id="s-warn",
        task="demo",
        execute=execute,
        event_sink=events.append,
    )

    assert result["ok"] is True
    warn_lines = [e for e in events if e.get("type") == "config_warning"]
    assert len(warn_lines) == 1
    assert "ollama" in warn_lines[0]["warnings"][0]  # type: ignore[index]


def test_run_production_logs_no_config_warning_for_hosted_provider(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    config = providers.resolve_from_env(
        {"LAURA_AGENT_PROVIDER": "openai-compat", "LAURA_AGENT_API_KEY": "k"}
    )
    execute, _calls = _make_execute({"A": ("ok", False)})
    events: list[dict[str, object]] = []

    result = production_orchestrator.run_production(
        db,
        config,
        asset_id=asset_id,
        session_id="s-hosted",
        task="demo",
        execute=execute,
        event_sink=events.append,
    )

    assert result["ok"] is True
    assert not any(event.get("type") == "config_warning" for event in events)


# --- Finding 1: a fully-restored board must not spend an agent-team run --------------------
# Spec §Entscheidungen (User) 2: "ein komplett kohärentes Board erreicht complete: True ohne
# Agent-Turn". The entry restore above already brings back the WHOLE suffix through qa_report
# (test_restore_suffix.py's motivating case, replayed here through run_production itself) — a
# board in that state needs nothing further from the team.


def _seed_full_chain(board: Board, text: str) -> None:
    """storyline -> script -> voice -> cutlist -> sheet -> render -> qa, each parents-stamped.

    Mirrors ``test_restore_suffix.py``'s ``_seed_full_chain`` (this file needs its own fixture
    to exercise the full walk through the ``run_production`` entry point, not just the board
    method directly).
    """
    from laura.short_creator.board_models import (
        ContactSheet,
        ContactSheetTile,
        Cutlist,
        CutSegment,
        QaReport,
        VoiceArtifact,
        content_hash,
    )

    board.save(
        "storyline",
        Storyline(
            red_thread="r",
            arc=[
                Chapter(chapter=1, role="hook", message="m", scene_numbers=[1], target_seconds=10.0)
            ],
        ),
    )
    board.save("script", _script_artifact(text))
    script = board.load("script")
    assert script is not None
    voice = VoiceArtifact(
        script_hash="cache-key",
        mp3_path=f"voiceovers/{text[:8]}.mp3",
        parents={"script": content_hash(script)},
    )
    board.save("voice", voice)
    cur_voice = board.load("voice")
    assert cur_voice is not None
    board.save(
        "cutlist",
        Cutlist(
            segments=[CutSegment(order=0, scene_number=1, start_frame=0, end_frame_exclusive=90)]
        ).model_copy(
            update={"parents": {"script": content_hash(script), "voice": content_hash(cur_voice)}}
        ),
    )
    cur_cut = board.load("cutlist")
    assert cur_cut is not None
    board.save(
        "contact_sheet",
        ContactSheet(
            png_path="s.png",
            cols=1,
            rows=1,
            tiles=[ContactSheetTile(order=0, scene_number=1, frame=45, label="0 S1")],
        ).model_copy(update={"parents": {"cutlist": content_hash(cur_cut)}}),
    )
    board.save(
        "render_report",
        RenderReport(
            export_id=f"e-{text[:8]}",
            video_s=100.0,
            width=1920,
            height=1080,
            parents={"voice": content_hash(cur_voice), "cutlist": content_hash(cur_cut)},
        ),
    )
    cur_render = board.load("render_report")
    assert cur_render is not None
    board.save(
        "qa_report",
        QaReport(verdict="ship", findings=[], parents={"render_report": content_hash(cur_render)}),
    )


def _revise_and_revert_back(board: Board, text: str) -> None:
    """Wipe voice..qa_report by revising the script, then bring the SAME text back — the
    archived suffix's parent hashes still match (content_hash ignores ``version``)."""
    board.save("script", _script_artifact("a different draft"))
    board.save("script", _script_artifact(text))


def test_full_restore_to_done_skips_team_execution(tmp_path: Path) -> None:
    """A board whose full suffix (through qa_report) restores must report complete WITHOUT
    ever invoking ``execute`` — the user-approved decision this feature exists to satisfy."""
    db, asset_id = _seed_scene(tmp_path)
    config = providers.resolve_from_env({})
    root = production_orchestrator.board_root_for(db, asset_id, "sess-full-restore")
    board = Board.create(
        root,
        BoardMeta(
            session_id="sess-full-restore",
            asset_id=asset_id,
            created_utc="2026-07-20T00:00:00+00:00",
            task="demo",
            language="English",
            target_seconds=174.0,
        ),
    )
    board.save_scene_review(_review(1))
    _seed_full_chain(board, "the rendered line")
    _revise_and_revert_back(board, "the rendered line")
    assert board.load("voice") is None and board.load("qa_report") is None

    calls: list[tuple[str, str]] = []

    def execute(
        db: Database,
        config: providers.AgentConfig,
        stage: str,
        kind: str,
        task: str,
    ) -> orchestrator.StageOutcome:
        calls.append((stage, kind))
        return orchestrator.StageOutcome(
            status="ok",
            weak=False,
            summary="done",
            team=cast(orchestrator.TeamKind, kind),
            stage=cast(providers.Stage, stage),
        )

    result = production_orchestrator.run_production(
        db,
        config,
        asset_id=asset_id,
        session_id="sess-full-restore",
        task="demo",
        target_seconds=174,
        execute=execute,
    )

    assert calls == [], "a fully coherent board must not spend a team turn"
    assert result["restored"] == [
        "voice",
        "cutlist",
        "contact_sheet",
        "render_report",
        "qa_report",
    ]
    assert result["ok"] is True
    assert result["complete"] is True
    assert result["resume_point"] == "done"
    assert result["session_id"] == "sess-full-restore"
    assert result["board"]["meta"]["session_id"] == "sess-full-restore"


def test_full_restore_with_a_message_still_runs_the_team(tmp_path: Path) -> None:
    """The short-circuit is for a plain resume only — a follow-up ``message`` IS a request for
    a team turn, so it must still run even against a fully-restored, coherent board."""
    db, asset_id = _seed_scene(tmp_path)
    config = providers.resolve_from_env({})
    root = production_orchestrator.board_root_for(db, asset_id, "sess-full-restore-msg")
    board = Board.create(
        root,
        BoardMeta(
            session_id="sess-full-restore-msg",
            asset_id=asset_id,
            created_utc="2026-07-20T00:00:00+00:00",
            task="demo",
            language="English",
            target_seconds=174.0,
        ),
    )
    board.save_scene_review(_review(1))
    _seed_full_chain(board, "the rendered line")
    _revise_and_revert_back(board, "the rendered line")
    assert board.load("voice") is None

    calls: list[tuple[str, str]] = []

    def execute(
        db: Database,
        config: providers.AgentConfig,
        stage: str,
        kind: str,
        task: str,
    ) -> orchestrator.StageOutcome:
        calls.append((stage, kind))
        return orchestrator.StageOutcome(
            status="ok",
            weak=False,
            summary="done",
            team=cast(orchestrator.TeamKind, kind),
            stage=cast(providers.Stage, stage),
        )

    result = production_orchestrator.run_production(
        db,
        config,
        asset_id=asset_id,
        session_id="sess-full-restore-msg",
        task="demo",
        target_seconds=174,
        message="make the hook punchier",
        execute=execute,
    )

    assert calls == [("A", "magentic")], "a follow-up message must still run the team"
    assert result["restored"] == [
        "voice",
        "cutlist",
        "contact_sheet",
        "render_report",
        "qa_report",
    ]


# --- _parse_outcome -----------------------------------------------------------------------


class _SummaryMsg:
    def __init__(self, content: str) -> None:
        self.content = content


def _make_board(tmp_path: Path) -> Board:
    """Create a minimal board for testing _parse_outcome."""
    root = tmp_path / "board"
    meta = BoardMeta(
        session_id="s1",
        asset_id="a1",
        created_utc="2026-07-21T00:00:00+00:00",
        task="t",
        language="English",
        target_seconds=60.0,
    )
    return Board.create(root, meta)


def test_parse_outcome_summary_is_the_last_answer_not_the_task(tmp_path: Path) -> None:
    """Live finding 2026-07-20: every run's result.summary was the TASK text — _parse_outcome
    concatenated ALL messages and truncated to 2000 chars, so messages[0] (the task) always
    won. The summary must be the team's final answer."""
    from laura.short_creator.production_orchestrator import _parse_outcome

    board = _make_board(tmp_path)
    task_echo = "1) GOAL: build the film... " * 100  # long, like the real task text
    result = SimpleNamespace(
        messages=[
            _SummaryMsg(task_echo),
            _SummaryMsg("intermediate tool chatter"),
            _SummaryMsg("The film is built: 6 chapters, QA verdict ship."),
        ]
    )

    outcome = _parse_outcome(board, result, stage="A")

    assert outcome.summary == "The film is built: 6 chapters, QA verdict ship."


def test_parse_outcome_skips_trailing_empty_messages(tmp_path: Path) -> None:
    from laura.short_creator.production_orchestrator import _parse_outcome

    board = _make_board(tmp_path)
    result = SimpleNamespace(
        messages=[_SummaryMsg("the real answer"), _SummaryMsg("   "), _SummaryMsg("")]
    )

    outcome = _parse_outcome(board, result, stage="A")

    assert outcome.summary == "the real answer"


def test_parse_outcome_empty_result_gives_empty_summary(tmp_path: Path) -> None:
    from laura.short_creator.production_orchestrator import _parse_outcome

    board = _make_board(tmp_path)

    outcome = _parse_outcome(board, SimpleNamespace(messages=[]), stage="A")

    assert outcome.summary == ""


def test_parse_outcome_base_group_chat_max_turns_hard_fails(tmp_path: Path) -> None:
    from laura.short_creator.production_orchestrator import _parse_outcome

    board = _make_board(tmp_path)
    result = SimpleNamespace(
        messages=[_SummaryMsg("still waiting")],
        stop_reason="Maximum number of turns 30 reached.",
    )

    outcome = _parse_outcome(board, result, stage="A", tool_calls=3)

    assert outcome.status == "hard_fail"
    assert "Maximum number of turns 30 reached" in outcome.summary


@pytest.mark.parametrize("stop_reason", ["Max rounds reached.", "mAx RoUnDs ReAcHeD."])
def test_parse_outcome_magentic_max_rounds_hard_fails(
    tmp_path: Path, stop_reason: str
) -> None:
    from laura.short_creator.production_orchestrator import _parse_outcome

    board = _make_board(tmp_path)
    result = SimpleNamespace(
        messages=[_SummaryMsg("still waiting")],
        stop_reason=stop_reason,
    )

    outcome = _parse_outcome(board, result, stage="A", tool_calls=3)

    assert outcome.status == "hard_fail"
    assert outcome.summary == stop_reason


def test_parse_outcome_functional_termination_stays_ok(tmp_path: Path) -> None:
    from laura.short_creator.production_orchestrator import _parse_outcome

    board = _make_board(tmp_path)
    result = SimpleNamespace(
        messages=[_SummaryMsg("proposal saved")],
        stop_reason="Functional termination condition met",
    )

    outcome = _parse_outcome(board, result, stage="A", tool_calls=1)

    assert outcome.status == "ok"
    assert outcome.summary == "proposal saved"


def test_parse_outcome_unrelated_max_round_reason_stays_ok(tmp_path: Path) -> None:
    from laura.short_creator.production_orchestrator import _parse_outcome

    board = _make_board(tmp_path)
    result = SimpleNamespace(
        messages=[_SummaryMsg("proposal saved")],
        stop_reason="Max round-trip latency reached.",
    )

    outcome = _parse_outcome(board, result, stage="A", tool_calls=1)

    assert outcome.status == "ok"
    assert outcome.summary == "proposal saved"


# --- follow-up guards (live finding 2026-08-04) --------------------------------------------------
# Live session 6021d069: the user asked for a reframe; run 170643Z's MagenticOne orchestrator
# declared success with ZERO tool calls, and the render-cycle cap silently ate the re-render.
# Two guards: (1) a follow-up run that finishes without a single tool call is a hard_fail (so
# the ladder escalates to Stage B instead of reporting a success that changed nothing); (2) an
# explicit user follow-up raises the render cap by one via deps.max_render_cycles.


def test_parse_outcome_zero_tool_calls_on_follow_up_hard_fails(tmp_path: Path) -> None:
    from laura.short_creator.production_orchestrator import _parse_outcome

    board = _make_board(tmp_path)
    result = SimpleNamespace(messages=[_SummaryMsg("All done, everything looks great.")])

    outcome = _parse_outcome(
        board, result, stage="A", tool_calls=0, require_tool_call=True
    )

    assert outcome.status == "hard_fail"
    assert "without a single tool call" in outcome.summary
    assert "All done" in outcome.summary  # the team's own claim stays inspectable


def test_parse_outcome_with_tool_calls_on_follow_up_stays_ok(tmp_path: Path) -> None:
    from laura.short_creator.production_orchestrator import _parse_outcome

    board = _make_board(tmp_path)
    result = SimpleNamespace(messages=[_SummaryMsg("rebuilt and re-rendered")])

    outcome = _parse_outcome(
        board, result, stage="A", tool_calls=3, require_tool_call=True
    )

    assert outcome.status == "ok"
    assert outcome.summary == "rebuilt and re-rendered"


def test_parse_outcome_zero_tool_calls_without_requirement_stays_ok(tmp_path: Path) -> None:
    """A plain (non-message) run may legitimately end without tool calls — e.g. the full-board
    resume where the team only confirms; the guard is scoped to follow-up runs only."""
    from laura.short_creator.production_orchestrator import _parse_outcome

    board = _make_board(tmp_path)
    result = SimpleNamespace(messages=[_SummaryMsg("board already complete")])

    outcome = _parse_outcome(board, result, stage="A", tool_calls=0)

    assert outcome.status == "ok"


def test_deps_for_run_raises_render_cap_only_for_message_runs(tmp_path: Path) -> None:
    from laura.short_creator.production_orchestrator import _deps_for_run
    from laura.short_creator.production_tools import ProductionDeps, follow_up_render_cap

    board = _make_board(tmp_path)
    base = ProductionDeps()

    assert _deps_for_run(base, board, None) is base, "a plain resume must stay untouched"
    assert _deps_for_run(None, board, None) is None

    raised = _deps_for_run(base, board, "zeig das volle Bild, kein enger Zoom")
    assert raised is not None
    assert raised is not base, "the caller's deps object must not be mutated"
    assert raised.max_render_cycles == follow_up_render_cap(board)

    from_none = _deps_for_run(None, board, "render jetzt")
    assert from_none is not None
    assert from_none.max_render_cycles == follow_up_render_cap(board)


# --- deterministic_eligible + run_production's deterministic-tail branch -------------------
# Spec 2026-08-05 (modular production): once Gate B is approved and the session is resuming
# past creative work, production_pipeline.run_tail_with_qa replaces the agent team entirely —
# voice/cutlist/contact_sheet/render/qa become plain tool calls, never rewritten by a resumed
# team turn. deterministic_eligible is the pure predicate that decides when that applies.

_EXPECTED_ONE_SCENE = [1]


def _gated_board(tmp_path: Path, name: str, *, script_gate: bool) -> Board:
    """A fresh board (scene reviewed, nothing else) with the given gate setting.
    resume_point == 'storyline' — no creative work started yet."""
    root = tmp_path / name
    meta = BoardMeta(
        session_id=name,
        asset_id="a1",
        created_utc="2026-08-05T00:00:00+00:00",
        task="t",
        language="German",
        target_seconds=60.0,
        script_gate=script_gate,
    )
    board = Board.create(root, meta)
    board.save_scene_review(_review(1))
    return board


def _gated_board_with_storyline(tmp_path: Path, name: str, *, script_gate: bool) -> Board:
    """Storyline saved, script not yet — resume_point == 'script', still pre-chain."""
    board = _gated_board(tmp_path, name, script_gate=script_gate)
    board.save(
        "storyline",
        Storyline(
            red_thread="t",
            arc=[
                Chapter(
                    chapter=1, role="hook", message="m", scene_numbers=[1], target_seconds=3.0
                )
            ],
        ),
    )
    return board


def _gated_board_with_script(tmp_path: Path, name: str, *, script_gate: bool) -> Board:
    """Storyline + script saved, gate not yet approved — resume_point == 'voice'."""
    board = _gated_board_with_storyline(tmp_path, name, script_gate=script_gate)
    board.save("script", _script_artifact("hallo welt schauen wir uns das dashboard an"))
    return board


def _approve_current(board: Board) -> None:
    from laura.short_creator.board_models import content_hash

    script = board.load("script")
    assert script is not None
    board.set_script_approved("2026-08-05T12:00:00Z", content_hash(script))


class TestDeterministicEligible:
    def test_true_only_for_gated_current_post_script_no_message(self, tmp_path: Path) -> None:
        from laura.short_creator.production_orchestrator import deterministic_eligible

        board = _gated_board_with_script(tmp_path, "elig-true", script_gate=True)
        _approve_current(board)
        assert board.resume_point(_EXPECTED_ONE_SCENE) == "voice"
        assert deterministic_eligible(board, None, _EXPECTED_ONE_SCENE) is True

    def test_false_without_gate(self, tmp_path: Path) -> None:
        from laura.short_creator.production_orchestrator import deterministic_eligible

        board = _gated_board_with_script(tmp_path, "elig-nogate", script_gate=False)
        _approve_current(board)
        assert deterministic_eligible(board, None, _EXPECTED_ONE_SCENE) is False

    def test_false_when_approval_stale(self, tmp_path: Path) -> None:
        from laura.short_creator.production_orchestrator import deterministic_eligible

        board = _gated_board_with_script(tmp_path, "elig-stale", script_gate=True)
        board.set_script_approved("2026-08-05T12:00:00Z", "not-the-current-hash")
        assert deterministic_eligible(board, None, _EXPECTED_ONE_SCENE) is False

    def test_false_when_never_approved(self, tmp_path: Path) -> None:
        from laura.short_creator.production_orchestrator import deterministic_eligible

        board = _gated_board_with_script(tmp_path, "elig-noapproval", script_gate=True)
        assert deterministic_eligible(board, None, _EXPECTED_ONE_SCENE) is False

    def test_false_with_follow_up_message(self, tmp_path: Path) -> None:
        from laura.short_creator.production_orchestrator import deterministic_eligible

        board = _gated_board_with_script(tmp_path, "elig-followup", script_gate=True)
        _approve_current(board)
        assert deterministic_eligible(
            board, "mach den Hook punchiger", _EXPECTED_ONE_SCENE
        ) is False

    def test_false_before_script_exists(self, tmp_path: Path) -> None:
        """Pre-chain resume point 'storyline' (nothing saved yet) — never approved, so this
        already fails the gate check, but it must still read False, not crash."""
        from laura.short_creator.production_orchestrator import deterministic_eligible

        board = _gated_board(tmp_path, "elig-nostoryline", script_gate=True)
        assert board.resume_point(_EXPECTED_ONE_SCENE) == "storyline"
        assert deterministic_eligible(board, None, _EXPECTED_ONE_SCENE) is False

    def test_false_when_resume_point_is_script(self, tmp_path: Path) -> None:
        """Pre-chain resume point 'script' (storyline saved, script not yet) — the second
        half of the pre-chain matrix the storyline case above doesn't cover. Ledger note:
        deterministic_eligible's resume_point membership check is the ONLY guard stopping
        run_deterministic_tail from being handed a pre-chain board and reporting a hollow
        ok+empty tail — this pins that the guard actually excludes 'script' too, not just
        'storyline'."""
        from laura.short_creator.production_orchestrator import deterministic_eligible

        board = _gated_board_with_storyline(tmp_path, "elig-noscript", script_gate=True)
        assert board.resume_point(_EXPECTED_ONE_SCENE) == "script"
        assert deterministic_eligible(board, None, _EXPECTED_ONE_SCENE) is False


def _seeded_gated_run(
    tmp_path: Path, session_id: str
) -> tuple[Database, str, providers.AgentConfig]:
    """db + asset + a gated, approved board (resume_point == 'voice') under that session,
    ready for run_production to reopen."""
    db, asset_id = _seed_scene(tmp_path)
    root = production_orchestrator.board_root_for(db, asset_id, session_id)
    meta = BoardMeta(
        session_id=session_id,
        asset_id=asset_id,
        created_utc="2026-08-05T00:00:00+00:00",
        task="t",
        language="German",
        target_seconds=60.0,
        script_gate=True,
    )
    board = Board.create(root, meta)
    board.save_scene_review(_review(1))
    board.save(
        "storyline",
        Storyline(
            red_thread="t",
            arc=[
                Chapter(
                    chapter=1, role="hook", message="m", scene_numbers=[1], target_seconds=3.0
                )
            ],
        ),
    )
    board.save("script", _script_artifact("hallo welt schauen wir uns das dashboard an"))
    _approve_current(board)
    config = providers.resolve_from_env({})
    return db, asset_id, config


def test_run_production_uses_tail_not_team_when_eligible(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An eligible resume must NOT run the agent team: run_tail_with_qa is called exactly
    once, the fake team execute is never called."""
    db, asset_id, config = _seeded_gated_run(tmp_path, "sess-tail")
    called = {"tail": 0, "team": 0}

    def fake_tail(
        db: Database, board: Board, config: providers.AgentConfig, **kwargs: object
    ) -> tuple[object, object]:
        called["tail"] += 1
        from laura.short_creator.board_models import QaReport
        from laura.short_creator.production_pipeline import TailOutcome

        # I1: a real "ok" QA stage always ends with a saved qa_report — the deterministic
        # branch now treats an "ok" outcome with none on the board as stranded (see the
        # dedicated stranding test below), so the fake here must do what a real QA turn does.
        board.save("qa_report", QaReport(verdict="ship"))
        return (
            TailOutcome(True, None, None, "deterministic tail: all"),
            orchestrator.StageOutcome(
                status="ok", weak=False, summary="ship", team="magentic", stage="A"
            ),
        )

    monkeypatch.setattr(production_orchestrator, "run_tail_with_qa", fake_tail)

    def team_execute(*a: object, **k: object) -> orchestrator.StageOutcome:
        called["team"] += 1
        raise AssertionError("team must not run on the deterministic path")

    result = production_orchestrator.run_production(
        db, config, asset_id=asset_id, session_id="sess-tail", task="t", execute=team_execute
    )

    assert called == {"tail": 1, "team": 0}
    assert result["ok"] is True
    assert result["summary"].startswith("deterministic tail")


def test_run_production_tail_failure_sets_failed_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db, asset_id, config = _seeded_gated_run(tmp_path, "sess-tail-fail")

    def fake_tail(
        db: Database, board: Board, config: providers.AgentConfig, **kwargs: object
    ) -> tuple[object, None]:
        from laura.short_creator.production_pipeline import TailOutcome

        return (
            TailOutcome(False, "render_production", "boom", "deterministic tail: voice"),
            None,
        )

    monkeypatch.setattr(production_orchestrator, "run_tail_with_qa", fake_tail)

    def never_team(*a: object, **k: object) -> orchestrator.StageOutcome:
        raise AssertionError("team must not run on the deterministic path")

    result = production_orchestrator.run_production(
        db, config, asset_id=asset_id, session_id="sess-tail-fail", task="t",
        execute=never_team,
    )

    assert result["ok"] is False
    assert "render_production" in result["summary"]
    root = production_orchestrator.board_root_for(db, asset_id, "sess-tail-fail")
    assert Board.open(root).meta().status == "failed"


def test_run_production_qa_hard_fail_sets_failed_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A fully successful chain whose QA stage hard-fails (LLM outage, etc. — _safe_execute
    turns any raise into a hard_fail StageOutcome) must not read as a success. Live-class bug:
    ok=True with the board left neither failed nor complete is the exact dead-run shape
    Board.set_status's docstring warns about (a run reporting "active" long after it died)."""
    db, asset_id, config = _seeded_gated_run(tmp_path, "sess-qa-fail")

    def fake_tail(
        db: Database, board: Board, config: providers.AgentConfig, **kwargs: object
    ) -> tuple[object, orchestrator.StageOutcome]:
        from laura.short_creator.production_pipeline import TailOutcome

        return (
            TailOutcome(True, None, None, "deterministic tail: voice, cutlist, "
                        "contact_sheet, render_report"),
            orchestrator.StageOutcome(
                status="hard_fail", weak=False, summary="LLM outage", team="magentic", stage="A"
            ),
        )

    monkeypatch.setattr(production_orchestrator, "run_tail_with_qa", fake_tail)

    def never_team(*a: object, **k: object) -> orchestrator.StageOutcome:
        raise AssertionError("team must not run on the deterministic path")

    result = production_orchestrator.run_production(
        db, config, asset_id=asset_id, session_id="sess-qa-fail", task="t",
        execute=never_team,
    )

    assert result["ok"] is False
    assert "qa" in result["summary"].lower()
    root = production_orchestrator.board_root_for(db, asset_id, "sess-qa-fail")
    assert Board.open(root).meta().status == "failed"


def test_run_production_qa_ok_without_saved_report_strands_and_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """I1 (2026-08-05 final review): a QA StageOutcome reporting ``ok`` that never actually
    wrote a ``qa_report`` to the board — a zero-tool-call QA turn that
    ``_make_qa_execute``'s ``require_tool_call`` guard should already have caught, or any
    future QA execute that bypasses that guard — must not let the board finish with no
    verdict on record. Checked here as defense in depth, one write later than the guard
    itself."""
    db, asset_id, config = _seeded_gated_run(tmp_path, "sess-qa-stranded")

    def fake_tail(
        db: Database, board: Board, config: providers.AgentConfig, **kwargs: object
    ) -> tuple[object, orchestrator.StageOutcome]:
        from laura.short_creator.production_pipeline import TailOutcome

        return (
            TailOutcome(True, None, None, "deterministic tail: voice, cutlist, "
                        "contact_sheet, render_report"),
            orchestrator.StageOutcome(
                status="ok", weak=False, summary="board already complete",
                team="magentic", stage="A",
            ),
        )

    monkeypatch.setattr(production_orchestrator, "run_tail_with_qa", fake_tail)

    def never_team(*a: object, **k: object) -> orchestrator.StageOutcome:
        raise AssertionError("team must not run on the deterministic path")

    result = production_orchestrator.run_production(
        db, config, asset_id=asset_id, session_id="sess-qa-stranded", task="t",
        execute=never_team,
    )

    assert result["ok"] is False
    assert "qa_report" in result["summary"]
    root = production_orchestrator.board_root_for(db, asset_id, "sess-qa-stranded")
    assert Board.open(root).meta().status == "failed"


# --- C1 (2026-08-05 final review): the deterministic branch must raise the render cap ------
# deterministic_eligible() requires `message is None`, so the OLD `_deps_for_run(deps, board,
# message)` call in this branch never raised the cap — an approve->rewrite cycle that already
# exhausted _MAX_RENDER_CYCLES silently promoted a stale render. An explicit approval now
# grants exactly one render above what has already been spent, the same policy a message run
# gets (follow_up_render_cap).


def test_run_production_deterministic_branch_raises_render_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from laura.short_creator.board_models import QaReport
    from laura.short_creator.production_tools import _MAX_RENDER_CYCLES, follow_up_render_cap

    db, asset_id, config = _seeded_gated_run(tmp_path, "sess-cap-raise")
    root = production_orchestrator.board_root_for(db, asset_id, "sess-cap-raise")
    board = Board.open(root)
    # Spend the plain cap directly on the board (mirrors test_production_tools_render.py's
    # test_follow_up_render_cap_values — distinct export ids so each save really counts as
    # a new version instead of a same-content no-op).
    for i in range(_MAX_RENDER_CYCLES):
        board.save(
            "render_report",
            RenderReport(export_id=f"e{i}", video_s=4.0, width=1080, height=1920, checks=[]),
        )
    expected_cap = follow_up_render_cap(board)
    assert expected_cap > _MAX_RENDER_CYCLES, "the fixture must actually have spent the cap"

    captured: dict[str, Any] = {}

    def fake_tail(
        db: Database, board: Board, config: providers.AgentConfig, *,
        deps: Any, **kwargs: object,
    ) -> tuple[object, object]:
        captured["max_render_cycles"] = deps.max_render_cycles
        from laura.short_creator.production_pipeline import TailOutcome

        board.save("qa_report", QaReport(verdict="ship"))
        return (
            TailOutcome(True, None, None, "deterministic tail: all"),
            orchestrator.StageOutcome(
                status="ok", weak=False, summary="ship", team="magentic", stage="A"
            ),
        )

    monkeypatch.setattr(production_orchestrator, "run_tail_with_qa", fake_tail)

    def never_team(*a: object, **k: object) -> orchestrator.StageOutcome:
        raise AssertionError("team must not run on the deterministic path")

    production_orchestrator.run_production(
        db, config, asset_id=asset_id, session_id="sess-cap-raise", task="t",
        execute=never_team,
    )

    assert captured["max_render_cycles"] == expected_cap
