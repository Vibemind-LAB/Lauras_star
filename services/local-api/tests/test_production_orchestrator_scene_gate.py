"""Gate S (spec 2026-08-06): run_production pauses instead of spending a team turn while a
scene-selection proposal sits on the board unconfirmed.

Faktur mirrors ``test_production_orchestrator.py``'s ``_seed_scene``/``_review`` fixtures and its
``test_full_restore_to_done_skips_team_execution`` shape (same ``BoardMeta``/injected-``execute``
construction) — this is the same class of short-circuit, just gated on ``scene_gate``/
``SceneSelection`` instead of a fully restored chain. Duplicated locally (not imported from that
module) to stay self-contained, same rationale that module's own docstring gives for not
importing ``test_production_tools_review.py``'s version.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.short_creator import orchestrator, production_orchestrator, providers
from laura.short_creator.board import Board
from laura.short_creator.board_models import (
    BestWindow,
    BoardMeta,
    SceneCandidate,
    SceneReview,
    SceneSelection,
)

FPS = 30
SCENE_FRAMES = 150  # 150 frames @ 30fps = 5.0s


def _seed_scene(tmp_path: Path) -> tuple[Database, str]:
    """Project + asset + succeeded analysis run w/ transcript + a ONE-scene rough cut."""
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


def _proposal(
    scene_number: int = 1,
    *,
    confirmed: bool = False,
    rationale: str = "starker hook",
) -> SceneSelection:
    return SceneSelection(
        candidates=[
            SceneCandidate(
                scene_number=scene_number,
                src_start_frame=0,
                src_end_frame_exclusive=SCENE_FRAMES,
                thumb_frame=SCENE_FRAMES // 2,
                description="dashboard",
                transcript_snippet="hallo welt schauen wir uns das dashboard an",
                rationale=rationale,
                recommended=True,
            )
        ],
        selected_scene_numbers=[scene_number] if confirmed else [],
        confirmed_utc="2026-08-06T00:05:00+00:00" if confirmed else None,
    )


def _make_board(db: Database, asset_id: str, session_id: str, *, scene_gate: bool) -> Board:
    root = production_orchestrator.board_root_for(db, asset_id, session_id)
    return Board.create(
        root,
        BoardMeta(
            session_id=session_id,
            asset_id=asset_id,
            created_utc="2026-08-06T00:00:00+00:00",
            task="demo",
            language="English",
            target_seconds=60.0,
            scene_gate=scene_gate,
        ),
    )


def test_run_awaiting_selection_never_spawns_team(tmp_path: Path) -> None:
    """Proposal on the board, unconfirmed, no message -> no execute call, awaiting summary."""
    db, asset_id = _seed_scene(tmp_path)
    config = providers.resolve_from_env({})
    board = _make_board(db, asset_id, "sess-scene-gate", scene_gate=True)
    board.save_scene_review(_review(1))
    board.save("scene_selection", _proposal())
    assert board.resume_point([1]) == "scene_selection"

    calls: list[str] = []

    def fake_execute(
        db: Database,
        config: providers.AgentConfig,
        stage: str,
        kind: str,
        task: str,
    ) -> orchestrator.StageOutcome:
        calls.append("x")
        raise AssertionError("team must not run while the scene gate is open")

    result = production_orchestrator.run_production(
        db,
        config,
        asset_id=asset_id,
        session_id="sess-scene-gate",
        task="demo",
        target_seconds=60,
        execute=fake_execute,
    )

    assert calls == []
    assert result["status"] == "awaiting_user_input"
    assert result["resume_point"] == "scene_selection"
    assert "scene selection" in result["summary"]
    assert result["ok"] is True
    assert result["complete"] is False
    # the run is a healthy park, not a failure — the board must stay "active"
    assert board.meta().status == "active"


def test_run_confirmed_selection_runs_the_team(tmp_path: Path) -> None:
    """The companion of the awaiting-selection test above: once the SAME proposal is CONFIRMED
    (``selected_scene_numbers`` set, ``confirmed_utc`` set), the Gate-S early-exit's four-way
    guard must NOT trigger — ``board.resume_point`` has advanced past ``"scene_selection"`` (to
    ``"storyline"``), so ``execute`` IS called and the run proceeds normally. A regression that
    dropped the guard's ``resume_point`` re-check (e.g. checking only ``meta.scene_gate`` and
    artifact presence) would strand every confirmed gate-on board at the pause forever — this is
    the test that would catch it, since the earlier test only exercises the unconfirmed case."""
    db, asset_id = _seed_scene(tmp_path)
    config = providers.resolve_from_env({})
    board = _make_board(db, asset_id, "sess-scene-gate-confirmed", scene_gate=True)
    board.save_scene_review(_review(1))
    board.save("scene_selection", _proposal(confirmed=True))
    assert board.resume_point([1]) == "storyline"

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
        session_id="sess-scene-gate-confirmed",
        task="demo",
        target_seconds=60,
        execute=execute,
    )

    assert calls == [("A", "magentic")], "a confirmed selection must let the team run"
    assert result["resume_point"] != "scene_selection"


def test_pending_selection_revision_runs_once_then_parks_again(tmp_path: Path) -> None:
    """A follow-up can revise a pending proposal once, then the run parks at Gate S again."""
    db, asset_id = _seed_scene(tmp_path)
    config = providers.resolve_from_env({})
    board = _make_board(db, asset_id, "sess-scene-gate-msg", scene_gate=True)
    board.save_scene_review(_review(1))
    board.save("scene_selection", _proposal())

    calls: list[tuple[str, str]] = []

    def execute(
        db: Database,
        config: providers.AgentConfig,
        stage: str,
        kind: str,
        task: str,
    ) -> orchestrator.StageOutcome:
        calls.append((stage, kind))
        board.save("scene_selection", _proposal(rationale="revised hook"))
        return orchestrator.StageOutcome(
            status="ok",
            weak=False,
            summary="proposal revised",
            team=cast(orchestrator.TeamKind, kind),
            stage=cast(providers.Stage, stage),
        )

    result = production_orchestrator.run_production(
        db,
        config,
        asset_id=asset_id,
        session_id="sess-scene-gate-msg",
        task="demo",
        target_seconds=60,
        message="use scene 1",
        execute=execute,
    )

    assert calls == [("A", "magentic")]
    assert result["status"] == "awaiting_user_input"
    assert result["resume_point"] == "scene_selection"
    assert result["ok"] is True
    assert board.meta().status == "active"


def test_new_pending_proposal_parks_without_stage_b_retry(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    config = providers.resolve_from_env({})
    board = _make_board(db, asset_id, "sess-live-loop", scene_gate=True)
    board.save_scene_review(_review(1))
    calls: list[str] = []

    def execute(
        db: Database,
        config: providers.AgentConfig,
        stage: str,
        kind: str,
        task: str,
    ) -> orchestrator.StageOutcome:
        calls.append(stage)
        board.save("scene_selection", _proposal())
        return orchestrator.StageOutcome(
            status="hard_fail",
            weak=True,
            summary="Maximum number of turns 30 reached.",
            team=cast(orchestrator.TeamKind, kind),
            stage=cast(providers.Stage, stage),
        )

    result = production_orchestrator.run_production(
        db,
        config,
        asset_id=asset_id,
        session_id="sess-live-loop",
        task="demo",
        target_seconds=60,
        execute=execute,
    )

    assert calls == ["A"]
    assert result["status"] == "awaiting_user_input"
    assert result["ok"] is True
    assert result["complete"] is False
    assert result["resume_point"] == "scene_selection"
    assert board.meta().status == "active"


def test_run_gate_off_board_unaffected(tmp_path: Path) -> None:
    """A gate-off board with no scene_selection artifact must run the team exactly as before
    Gate S existed — the early-exit condition requires ``meta.scene_gate`` to be True."""
    db, asset_id = _seed_scene(tmp_path)
    config = providers.resolve_from_env({})
    board = _make_board(db, asset_id, "sess-no-gate", scene_gate=False)
    board.save_scene_review(_review(1))

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
            status="hard_fail",
            weak=False,
            summary="stub",
            team=cast(orchestrator.TeamKind, kind),
            stage=cast(providers.Stage, stage),
        )

    production_orchestrator.run_production(
        db,
        config,
        asset_id=asset_id,
        session_id="sess-no-gate",
        task="demo",
        target_seconds=60,
        execute=execute,
    )

    assert calls, "a gate-off board must still run the team as before Gate S"


def test_run_gate_on_with_no_proposal_yet_still_runs_the_team(tmp_path: Path) -> None:
    """A gate-on board where NO proposal exists yet must still run the team — phase 1 of the
    team's own job is to review scenes and call propose_scene_selection in the first place."""
    db, asset_id = _seed_scene(tmp_path)
    config = providers.resolve_from_env({})
    board = _make_board(db, asset_id, "sess-gate-no-proposal", scene_gate=True)
    board.save_scene_review(_review(1))
    assert board.load("scene_selection") is None
    assert board.resume_point([1]) == "scene_selection"

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
            status="hard_fail",
            weak=False,
            summary="stub",
            team=cast(orchestrator.TeamKind, kind),
            stage=cast(providers.Stage, stage),
        )

    production_orchestrator.run_production(
        db,
        config,
        asset_id=asset_id,
        session_id="sess-gate-no-proposal",
        task="demo",
        target_seconds=60,
        execute=execute,
    )

    assert calls, "a gate-on board with no proposal yet must run the team (phase 1 proposes)"


# --- final-review finding 1: build_production_task's charter must not repeat the propose-then-
# stop steps once the board carries a CONFIRMED selection (that invited a follow-up run to call
# propose_scene_selection again and clobber the user's pick) --------------------------------


def test_build_production_task_confirmed_selection_forbids_reproposal(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _make_board(db, asset_id, "sess-scene-gate-task-confirmed", scene_gate=True)
    board.save_scene_review(_review(1))
    board.save("scene_selection", _proposal(1, confirmed=True))

    task = production_orchestrator.build_production_task(
        db, board, asset_id=asset_id, task="demo", target_seconds=60
    )

    assert "use ONLY scenes [1]" in task
    assert "Do NOT call propose_scene_selection again" in task
    assert "SCENE SELECTION GATE (mandatory)" not in task
    assert "Call propose_scene_selection with EVERY reviewed scene" not in task
    assert "Then STOP. Do not write a storyline or script" not in task


def test_build_production_task_pending_selection_keeps_mandatory_steps(tmp_path: Path) -> None:
    """Companion: an unconfirmed proposal still gets the full propose-then-stop charter — only
    a CONFIRMED selection swaps it out."""
    db, asset_id = _seed_scene(tmp_path)
    board = _make_board(db, asset_id, "sess-scene-gate-task-pending", scene_gate=True)
    board.save_scene_review(_review(1))
    board.save("scene_selection", _proposal(1, confirmed=False))

    task = production_orchestrator.build_production_task(
        db, board, asset_id=asset_id, task="demo", target_seconds=60
    )

    assert "Call propose_scene_selection with EVERY reviewed scene" in task
    assert "Then STOP. Do not write a storyline or script" in task
    assert "use ONLY scenes" not in task


def test_build_production_task_no_proposal_yet_keeps_mandatory_steps(tmp_path: Path) -> None:
    """Same companion, for the case where no proposal exists on the board at all yet."""
    db, asset_id = _seed_scene(tmp_path)
    board = _make_board(db, asset_id, "sess-scene-gate-task-none", scene_gate=True)
    board.save_scene_review(_review(1))

    task = production_orchestrator.build_production_task(
        db, board, asset_id=asset_id, task="demo", target_seconds=60
    )

    assert "Call propose_scene_selection with EVERY reviewed scene" in task
    assert "Then STOP. Do not write a storyline or script" in task
    assert "use ONLY scenes" not in task
