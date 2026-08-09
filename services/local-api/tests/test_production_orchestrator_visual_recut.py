"""Hard-stop contracts for visual selection and contact-sheet approval gates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.short_creator import production_orchestrator, providers
from laura.short_creator.board import Board
from laura.short_creator.board_models import (
    BestWindow,
    BoardMeta,
    Chapter,
    ContactSheet,
    ContactSheetTile,
    Cutlist,
    CutSegment,
    SceneReview,
    Script,
    ScriptLine,
    Storyline,
    VisualBeatPlan,
    VisualPlan,
    VisualRecutRequest,
    VisualShotCandidate,
    VoiceArtifact,
    VoiceSegment,
    content_hash,
    lines_in_storyline_order,
    script_hash,
)
from laura.short_creator.orchestrator import StageOutcome

FPS = 30
SCENE_FRAMES = 300
PROPOSAL_HASH = "a" * 64


def _seed_board(tmp_path: Path, *, session_id: str = "visual-run") -> tuple[Database, str, Board]:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db: Database = SqliteDatabase(settings.db_path)
    db.migrate()
    workspace_root = tmp_path / "ws" / "project"
    project = repos.create_project(
        db,
        name="p",
        rate_num=FPS,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(workspace_root),
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
            "text": "show the dashboard workflow",
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

    asset_id = str(asset["id"])
    board = Board.create(
        workspace_root / "agent-runs" / session_id / "board",
        BoardMeta(
            session_id=session_id,
            asset_id=asset_id,
            created_utc="2026-08-08T00:00:00+00:00",
            task="visual recut",
            target_seconds=10.0,
        ),
    )
    board.save_scene_review(
        SceneReview(
            scene_number=1,
            src_start_frame=0,
            src_end_frame_exclusive=SCENE_FRAMES,
            description="dashboard workflow",
            whats_happening="the product changes state",
            hook_score=8,
            best_window=BestWindow(offset_s=0.0, duration_s=10.0),
            windows=[BestWindow(offset_s=0.0, duration_s=10.0)],
        )
    )
    storyline = Storyline(
        red_thread="preserve narration",
        arc=[
            Chapter(
                chapter=1,
                role="hook",
                message="show the workflow",
                scene_numbers=[1],
                target_seconds=2.0,
            )
        ],
    )
    script = Script(
        language="de",
        lines=[ScriptLine(chapter=1, scene_number=1, text="Zeige den Ablauf.")],
    )
    ordered_lines = lines_in_storyline_order(script, storyline)
    voice = VoiceArtifact(
        script_hash=script_hash(ordered_lines),
        mp3_path="voice.mp3",
        timings_path="voice.timings.json",
        voice_s=1.0,
        segments=[
            VoiceSegment(
                scene_number=1,
                chapter=1,
                line_hash="c" * 64,
                mp3_path="voice-1.mp3",
                duration_s=1.0,
                offset_s=0.0,
            )
        ],
    )
    board.save("storyline", storyline)
    board.save("script", script)
    board.save("voice", voice)
    return db, asset_id, board


def _pending_visual(board: Board) -> VisualPlan:
    script = board.load("script")
    voice = board.load("voice")
    assert isinstance(script, Script)
    assert isinstance(voice, VoiceArtifact)
    request = VisualRecutRequest(
        user_request="better shots",
        script_version=script.version,
        script_hash=content_hash(script),
        voice_version=voice.version,
        voice_hash=content_hash(voice),
    )
    candidate = VisualShotCandidate(
        candidate_id="candidate-1",
        beat_id="beat-1",
        voice_segment_index=0,
        scene_number=1,
        window_index=0,
        src_start_frame=0,
        src_end_frame_exclusive=90,
        thumb_frame=45,
        description="dashboard",
        transcript_snippet="show the dashboard",
        rationale="matches narration",
        score=1.0,
    )
    plan = VisualPlan(
        proposal_hash=PROPOSAL_HASH,
        request_hash=content_hash(request),
        beats=[
            VisualBeatPlan(
                beat_id="beat-1",
                voice_segment_index=0,
                narration_text="Zeige den Ablauf.",
                duration_s=1.0,
                candidates=[candidate],
                recommended_candidate_id=candidate.candidate_id,
            )
        ],
    )
    board.save("visual_recut_request", request)
    board.save("visual_plan", plan)
    board.clear_contact_sheet_approval(enable_gate=True)
    return plan


def _pending_contact_sheet(board: Board) -> str:
    plan = _pending_visual(board)
    confirmed = plan.model_copy(
        update={
            "beats": [
                beat.model_copy(update={"selected_candidate_id": beat.recommended_candidate_id})
                for beat in plan.beats
            ],
            "confirmed_utc": "2026-08-08T01:00:00+00:00",
        }
    )
    board.save("visual_plan", confirmed)
    cutlist = Cutlist(
        segments=[
            CutSegment(
                order=0,
                scene_number=1,
                start_frame=0,
                end_frame_exclusive=30,
            )
        ]
    )
    board.save("cutlist", cutlist)
    sheet = ContactSheet(
        png_path="sheet.png",
        cols=1,
        rows=1,
        tiles=[ContactSheetTile(order=0, scene_number=1, frame=15, label="0 S1")],
        parents={"cutlist": content_hash(cutlist)},
    )
    board.save("contact_sheet", sheet)
    return content_hash(sheet)


def _run(
    db: Database,
    asset_id: str,
    board: Board,
    *,
    message: str | None = None,
    execute: Any = None,
) -> dict[str, Any]:
    return production_orchestrator.run_production(
        db,
        providers.resolve_from_env({}),
        asset_id=asset_id,
        session_id=board.meta().session_id,
        task="visual recut",
        target_seconds=10,
        message=message,
        execute=execute,
    )


def test_plain_resume_at_visual_gate_builds_no_team(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, asset_id, board = _seed_board(tmp_path)
    _pending_visual(board)
    monkeypatch.setattr(
        production_orchestrator,
        "build_production_team",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not build team")),
    )

    result = _run(db, asset_id, board)

    assert result["status"] == "awaiting_user_input"
    assert result["gate"] == "visual_selection"
    assert result["proposal_hash"] == PROPOSAL_HASH
    assert result["required_action"] == "confirm_visual_selection"


def test_plain_resume_at_contact_sheet_gate_builds_no_team(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, asset_id, board = _seed_board(tmp_path)
    sheet_hash = _pending_contact_sheet(board)
    monkeypatch.setattr(
        production_orchestrator,
        "build_production_team",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not build team")),
    )

    result = _run(db, asset_id, board)

    assert result["status"] == "awaiting_user_input"
    assert result["gate"] == "contact_sheet"
    assert result["contact_sheet_hash"] == sheet_hash
    assert result["required_action"] == "confirm_contact_sheet"


def test_new_visual_gate_suppresses_stage_b_and_uses_board_summary(tmp_path: Path) -> None:
    db, asset_id, board = _seed_board(tmp_path)
    calls: list[str] = []

    def execute(
        db: Database,
        config: providers.AgentConfig,
        stage: str,
        kind: str,
        task: str,
    ) -> StageOutcome:
        calls.append(stage)
        _pending_visual(board)
        return StageOutcome(
            status="hard_fail",
            weak=True,
            summary="invented agent failure text",
            team="magentic",
            stage="A",
        )

    result = _run(db, asset_id, board, message="better shots", execute=execute)

    assert calls == ["A"]
    assert result["status"] == "awaiting_user_input"
    assert result["gate"] == "visual_selection"
    assert result["proposal_hash"] == PROPOSAL_HASH
    assert "invented agent failure text" not in result["summary"]
    assert result["summary"] == "awaiting user visual selection — confirm the visual proposal"


def test_agent_claimed_render_without_receipt_is_hard_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, asset_id, board = _seed_board(tmp_path)
    team_builds = 0

    class Message:
        content = "Saved the contact sheet and rendered the final export successfully."

        def to_model_text(self) -> str:
            return self.content

    class TaskResult:
        stop_reason = None
        messages = [Message()]

    class Team:
        async def run_stream(self, *, task: str) -> Any:
            yield TaskResult()

    def build_team(*args: Any, **kwargs: Any) -> Team:
        nonlocal team_builds
        team_builds += 1
        return Team()

    monkeypatch.setattr(production_orchestrator, "build_production_team", build_team)

    result = _run(db, asset_id, board, message="render now")

    assert team_builds == 2
    assert result["status"] == "hard_fail"
    assert result["export_id"] is None
    assert board.load("contact_sheet") is None
    assert board.load("render_report") is None


def test_task_contract_names_structural_visual_and_contact_sheet_gates(tmp_path: Path) -> None:
    db, asset_id, board = _seed_board(tmp_path)
    _pending_visual(board)

    task = production_orchestrator.build_production_task(
        db,
        board,
        asset_id=asset_id,
        task="visual recut",
        target_seconds=10,
        message="better shots",
    )

    assert "VISUAL-SELECTION GATE" in task
    assert "CONTACT-SHEET APPROVAL GATE" in task
    assert "start_visual_recut exactly once" in task
    assert "STOP" in task
    assert "never re-save storyline, script, or voice" in task
    assert "CONTACT-SHEET CHECKPOINT (known pattern, no extra session state)" not in task
