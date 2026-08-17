"""Acceptance contract for durable multi-day visual-selection resumes."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database
from laura.main import create_app
from laura.short_creator.board import Board
from laura.short_creator.board_models import (
    BoardMeta,
    Chapter,
    Script,
    ScriptLine,
    Storyline,
    VisualPlan,
    VisualRecutRequest,
    VisualSceneCandidate,
    VisualSceneChoice,
    VoiceArtifact,
    VoiceSegment,
    content_hash,
)
from laura.short_creator.production_orchestrator import board_root_for
from laura.short_creator.production_tools import _rough_cut_source_hash
from laura.short_creator.visual_selection_state import capture_source_media_snapshot

_TOKEN = "test-token"
_HEADERS = {"X-Laura-Token": _TOKEN}
_NOW = "2026-08-17T10:00:00Z"
_SEVEN_DAYS_AGO = "2026-08-10T10:00:00Z"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        workspace_root=tmp_path / "workspace",
        token=_TOKEN,
        start_runner=False,
    )


def _candidate(order: int, choice: int) -> VisualSceneCandidate:
    start = order * 900 + choice * 330
    return VisualSceneCandidate(
        candidate_id=f"scene-{order}-candidate-{choice}",
        rough_cut_order=order,
        scene_number=order + 1,
        window_index=choice,
        src_start_frame=start,
        src_end_frame_exclusive=start + 300,
        thumb_frame=start + 150,
        max_duration_s=10,
        description=f"Rough-Cut scene {order + 1}",
        transcript_snippet=f"workflow step {order + 1}",
        rationale="covers this Rough-Cut row",
        score=1.0 - choice / 10,
    )


def _seed_pending_session(db: Database, tmp_path: Path) -> tuple[str, str, Board]:
    session_id = "week-old-visual-selection"
    source_path = tmp_path / "drive" / "rough-cut.mp4"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"original-drive-video")
    project = repos.create_project(
        db,
        name="Drive VibeMind",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(tmp_path / "project-workspace"),
    )
    asset = repos.create_asset(
        db,
        project_id=str(project["id"]),
        type="video",
        display_name="Rough Cut Source",
        source_path=str(source_path),
    )
    asset_id = str(asset["id"])
    repos.create_production_session(
        db,
        session_id=session_id,
        asset_id=asset_id,
        created_utc=_NOW,
        brief_text="Choose the best Rough-Cut scenes for the existing voiceover",
    )
    repos.create_conversation(db, conversation_id="resume-chat", created_utc=_NOW)
    repos.link_production_session_conversation(
        db,
        session_id,
        "resume-chat",
        updated_utc=_NOW,
    )

    board = Board.create(
        board_root_for(db, asset_id, session_id),
        BoardMeta(
            session_id=session_id,
            asset_id=asset_id,
            created_utc=_NOW,
            task="Choose the best Rough-Cut scenes for the existing voiceover",
            target_seconds=20.0,
        ),
    )
    storyline = Storyline(
        red_thread="workflow",
        arc=[
            Chapter(
                chapter=1,
                role="hook",
                message="show the workflow",
                scene_numbers=[1],
                target_seconds=4.0,
            )
        ],
    )
    script = Script(
        language="English",
        lines=[ScriptLine(chapter=1, scene_number=1, text="Approved narration")],
    )
    voice = VoiceArtifact(
        script_hash=content_hash(script),
        mp3_path=str(tmp_path / "voice.mp3"),
        voice_s=20.0,
        segments=[
            VoiceSegment(
                scene_number=1,
                chapter=1,
                line_hash="c" * 64,
                mp3_path=str(tmp_path / "voice-1.mp3"),
                duration_s=19.7,
                offset_s=0.0,
            )
        ],
    )
    board.save("storyline", storyline)
    board.save("script", script)
    board.save("voice", voice)
    request = VisualRecutRequest(
        user_request="rebuild only the visuals",
        script_version=script.version,
        script_hash=content_hash(script),
        voice_version=voice.version,
        voice_hash=content_hash(voice),
    )
    board.save("visual_recut_request", request)

    timeline = repos.create_timeline(
        db,
        project_id=str(project["id"]),
        name="Rough Cut",
        kind="rough_cut",
        created_from=asset_id,
    )
    repos.add_timeline_clip(
        db,
        timeline_id=str(timeline["id"]),
        asset_id=asset_id,
        src_in_frame=0,
        src_out_frame_exclusive=3600,
        seq_in_frame=0,
        seq_out_frame_exclusive=3600,
        lane=0,
        role="base",
    )
    repos.replace_scenes(
        db,
        str(project["id"]),
        str(timeline["id"]),
        [(order * 900, (order + 1) * 900) for order in range(4)],
    )
    rough_cut_hash = _rough_cut_source_hash(db, asset_id)
    assert rough_cut_hash is not None
    source = capture_source_media_snapshot(
        db,
        asset_id=asset_id,
        rough_cut_hash=rough_cut_hash,
        fps=30.0,
        voice_hash=content_hash(voice),
        voice_total_frames=600,
        script_hash=content_hash(script),
        request_hash=content_hash(request),
        strong=True,
    )
    assert source.strong_hash is not None
    plan = VisualPlan(
        version=2,
        proposal_hash="a" * 64,
        request_hash=content_hash(request),
        parents={
            "visual_recut_request": content_hash(request),
            "script": content_hash(script),
            "voice": content_hash(voice),
            "rough_cut": rough_cut_hash,
            "source_media": source.strong_hash,
            "source_media_quick": source.quick_hash,
        },
        scene_choices=[
            VisualSceneChoice(
                rough_cut_order=order,
                scene_number=order + 1,
                description=f"Rough-Cut scene {order + 1}",
                transcript=f"workflow step {order + 1}",
                rationale="keeps every Rough-Cut row available",
                candidates=[_candidate(order, choice) for choice in range(2)],
                recommended_candidate_id=f"scene-{order}-candidate-0",
                recommended_included=True,
                recommended_duration_s=5,
            )
            for order in range(4)
        ],
        rough_cut_scene_count=4,
        voice_total_frames=600,
        fps=30.0,
    )
    board.save("visual_plan", plan)
    return session_id, asset_id, board


def _decisions() -> list[dict[str, Any]]:
    return [
        {
            "rough_cut_order": order,
            "candidate_id": f"scene-{order}-candidate-{order % 2}",
            "included": order != 2,
            "requested_duration_s": [7, 6, 1, 7][order],
        }
        for order in range(4)
    ]


def _job_count(db: Database) -> int:
    return sum(job["kind"] == "production.run" for job in repos.list_jobs(db))


def test_visual_selection_survives_backend_restart_and_seven_day_pause(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr("laura.api.short_creator._require_autoshort", lambda: None)
    monkeypatch.setattr("laura.api.short_creator._require_usable_agent_config", lambda: None)
    settings = _settings(tmp_path)
    first_app = create_app(settings)
    first_db = cast(Database, first_app.state.db)
    session_id, asset_id, board = _seed_pending_session(first_db, tmp_path)
    plan = cast(VisualPlan, board.load("visual_plan"))
    script_before = board.load("script")
    voice_before = board.load("voice")
    assert isinstance(script_before, Script)
    assert isinstance(voice_before, VoiceArtifact)
    script_hash_before = content_hash(script_before)
    voice_hash_before = content_hash(voice_before)
    decisions = _decisions()

    with TestClient(first_app) as first_client:
        saved = first_client.put(
            f"/production/{session_id}/visual-selection/draft",
            json={
                "proposal_hash": plan.proposal_hash,
                "expected_revision": None,
                "selections": decisions,
            },
            headers=_HEADERS,
        )
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1
    assert _job_count(first_db) == 0

    repos.touch_production_session(first_db, session_id, _SEVEN_DAYS_AGO)
    with first_db.transaction() as conn:
        conn.execute(
            "UPDATE visual_selection_drafts SET updated_utc=? WHERE session_id=?",
            (_SEVEN_DAYS_AGO, session_id),
        )

    del board
    del first_db
    del first_app

    restarted_app = create_app(settings)
    restarted_db = cast(Database, restarted_app.state.db)
    with TestClient(restarted_app) as restarted_client:
        sessions = restarted_client.get("/production-sessions/open", headers=_HEADERS)
        assert sessions.status_code == 200, sessions.text
        resumable = next(row for row in sessions.json() if row["session_id"] == session_id)
        assert resumable["conversation_id"] == "resume-chat"
        assert resumable["state"] == "awaiting-approval"
        assert resumable["draft_updated_utc"] == _SEVEN_DAYS_AGO
        assert resumable["stale"] is False

        status = restarted_client.get(f"/production/{session_id}", headers=_HEADERS)
        assert status.status_code == 200, status.text
        gate = status.json()["visual_selection_gate"]
        assert gate["proposal_id"] == plan.proposal_hash
        assert gate["draft"]["revision"] == 1
        assert gate["draft"]["selections"] == decisions

        confirmed = restarted_client.post(
            f"/production/{session_id}/visual-selection:confirm",
            json={"proposal_hash": plan.proposal_hash, "selections": decisions},
            headers=_HEADERS,
        )
    assert confirmed.status_code == 202, confirmed.text
    assert _job_count(restarted_db) == 1
    job = repos.get_job(restarted_db, confirmed.json()["job_id"])
    assert job is not None
    assert "message" not in json.loads(str(job["payload_json"]))
    assert repos.get_visual_selection_draft(restarted_db, session_id) is None

    reopened = Board.open(board_root_for(restarted_db, asset_id, session_id))
    script_after = reopened.load("script")
    voice_after = reopened.load("voice")
    assert isinstance(script_after, Script)
    assert isinstance(voice_after, VoiceArtifact)
    assert content_hash(script_after) == script_hash_before
    assert content_hash(voice_after) == voice_hash_before
    confirmed_plan = cast(VisualPlan, reopened.load("visual_plan"))
    assert [choice.included for choice in confirmed_plan.scene_choices] == [
        True,
        True,
        False,
        True,
    ]
    assert [choice.requested_duration_s for choice in confirmed_plan.scene_choices] == [
        7,
        6,
        1,
        7,
    ]


def test_changed_drive_metadata_marks_session_stale_and_blocks_writes(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr("laura.api.short_creator._require_autoshort", lambda: None)
    monkeypatch.setattr("laura.api.short_creator._require_usable_agent_config", lambda: None)
    app = create_app(_settings(tmp_path))
    db = cast(Database, app.state.db)
    session_id, asset_id, board = _seed_pending_session(db, tmp_path)
    plan = cast(VisualPlan, board.load("visual_plan"))
    plan_before = plan.model_dump_json()
    asset = repos.get_asset(db, asset_id)
    assert asset is not None
    source = Path(str(asset["source_path"]))
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    with TestClient(app) as client:
        sessions = client.get("/production-sessions/open", headers=_HEADERS)
        stale = next(row for row in sessions.json() if row["session_id"] == session_id)
        assert stale["stale"] is True
        assert stale["stale_reason"] == "source_metadata_changed"

        saved = client.put(
            f"/production/{session_id}/visual-selection/draft",
            json={
                "proposal_hash": plan.proposal_hash,
                "expected_revision": None,
                "selections": _decisions(),
            },
            headers=_HEADERS,
        )
        confirmed = client.post(
            f"/production/{session_id}/visual-selection:confirm",
            json={"proposal_hash": plan.proposal_hash, "selections": _decisions()},
            headers=_HEADERS,
        )

    assert saved.status_code == 409
    assert confirmed.status_code == 409
    assert _job_count(db) == 0
    assert repos.get_visual_selection_draft(db, session_id) is None
    assert cast(VisualPlan, board.load("visual_plan")).model_dump_json() == plan_before
