"""Visual recut approval services and their HTTP endpoints.

The board and job state asserted here always lives in the real SQLite repository.  Provider
preflights are disabled in successful endpoint tests, but the resume itself still uses the
real production queue so ``latest_job_id`` proves that the endpoint enqueued a pure resume.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from laura.api.short_creator import confirm_contact_sheet
from laura.config import Settings
from laura.db import repos
from laura.db.database import Database
from laura.jobs.runner import enqueue
from laura.short_creator.board import Board
from laura.short_creator.board_models import (
    BoardMeta,
    Chapter,
    ContactSheet,
    ContactSheetTile,
    Script,
    ScriptLine,
    Storyline,
    VisualBeatPlan,
    VisualPlan,
    VisualRecutRequest,
    VisualShotCandidate,
    VoiceArtifact,
    content_hash,
)
from laura.short_creator.production_orchestrator import board_root_for

_NOW = "2026-08-08T12:00:00Z"
_HASH_A = "a" * 64
_HASH_B = "b" * 64
_TOKEN = "test-token"
_HEADERS = {"X-Laura-Token": _TOKEN}


def _candidate(beat: int, choice: int) -> VisualShotCandidate:
    start = beat * 300 + choice * 90
    return VisualShotCandidate(
        candidate_id=f"beat-{beat}-candidate-{choice}",
        beat_id=f"beat-{beat}",
        voice_segment_index=beat,
        scene_number=choice + 1,
        window_index=choice,
        src_start_frame=start,
        src_end_frame_exclusive=start + 75,
        thumb_frame=start + 37,
        description=f"visual {beat}/{choice}",
        transcript_snippet="relevant words",
        rationale="distributed candidate",
        score=1.0 - choice / 10,
    )


def _plan(*, confirmed: bool = False) -> VisualPlan:
    beats: list[VisualBeatPlan] = []
    for beat_index in range(2):
        candidates = [_candidate(beat_index, choice) for choice in range(2)]
        beats.append(
            VisualBeatPlan(
                beat_id=f"beat-{beat_index}",
                voice_segment_index=beat_index,
                narration_text=f"Narration {beat_index}",
                duration_s=2.0,
                candidates=candidates,
                recommended_candidate_id=candidates[0].candidate_id,
                selected_candidate_id=candidates[0].candidate_id if confirmed else None,
            )
        )
    return VisualPlan(
        proposal_hash=_HASH_A,
        request_hash=_HASH_B,
        beats=beats,
        confirmed_utc=_NOW if confirmed else None,
    )


def _sheet() -> ContactSheet:
    return ContactSheet(
        png_path="/tmp/contact-sheet.png",
        cols=2,
        rows=1,
        tiles=[
            ContactSheetTile(
                order=i,
                scene_number=i + 1,
                frame=30 + i * 100,
                label=f"{i} S{i + 1}",
                src_start_frame=i * 100,
                src_end_frame_exclusive=(i + 1) * 100,
            )
            for i in range(2)
        ],
    )


def _seed_board(
    db: Database,
    tmp_path: Path,
    *,
    session_id: str,
    plan: VisualPlan | None = None,
    sheet: ContactSheet | None = None,
    contact_sheet_gate: bool = False,
    request: bool = True,
) -> tuple[str, Board]:
    project = repos.create_project(
        db,
        name=f"project-{session_id}",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(tmp_path / session_id),
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="source",
        source_path="/tmp/source.mp4",
    )
    repos.create_production_session(
        db, session_id=session_id, asset_id=asset["id"], created_utc=_NOW
    )
    board = Board.create(
        board_root_for(db, str(asset["id"]), session_id),
        BoardMeta(
            session_id=session_id,
            asset_id=str(asset["id"]),
            created_utc=_NOW,
            task="visual recut",
            target_seconds=30.0,
            contact_sheet_gate=contact_sheet_gate,
        ),
    )
    storyline = Storyline(
        red_thread="premise",
        arc=[
            Chapter(
                chapter=1,
                role="hook",
                message="purpose",
                scene_numbers=[1],
                target_seconds=4.0,
            )
        ],
    )
    script = Script(
        language="English",
        lines=[ScriptLine(chapter=1, scene_number=1, text="Approved narration")],
    )
    voice = VoiceArtifact(script_hash=_HASH_B, mp3_path="/tmp/voice.mp3", voice_s=4.0)
    board.save("storyline", storyline)
    board.save("script", script)
    board.save("voice", voice)
    if request:
        board.save(
            "visual_recut_request",
            VisualRecutRequest(
                user_request="rebuild visuals",
                script_version=script.version,
                script_hash=content_hash(script),
                voice_version=voice.version,
                voice_hash=content_hash(voice),
            ),
        )
    if plan is not None:
        board.save("visual_plan", plan)
    if sheet is not None:
        board.save("contact_sheet", sheet)
        if contact_sheet_gate:
            board.clear_contact_sheet_approval(enable_gate=True)
    return str(asset["id"]), board


def _client(tmp_path: Path, monkeypatch: Any) -> tuple[TestClient, Database]:
    from laura.main import create_app

    monkeypatch.setattr("laura.api.short_creator._require_autoshort", lambda: None)
    monkeypatch.setattr("laura.api.short_creator._require_usable_agent_config", lambda: None)
    app = create_app(
        Settings(workspace_root=tmp_path / "workspace", token=_TOKEN, start_runner=False)
    )
    return TestClient(app), app.state.db


def _latest_job_id(db: Database, session_id: str) -> str | None:
    session = repos.get_production_session(db, session_id)
    assert session is not None
    value = session.get("latest_job_id")
    return str(value) if value else None


def _seed_job(db: Database, session_id: str, *, job_status: str) -> str:
    job_id = enqueue(db, queue="production", kind="production.run", payload={}, max_attempts=1)
    repos.set_production_session_job(db, session_id, job_id)
    with db.transaction() as conn:
        conn.execute("UPDATE jobs SET status=? WHERE id=?", (job_status, job_id))
    return job_id


def _unchanged_core(board: Board) -> dict[str, str]:
    return {
        name: board.load(name).model_dump_json()  # type: ignore[union-attr]
        for name in ("storyline", "script", "voice")
    }


def test_confirm_visual_selection_rejects_stale_proposal(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client, db = _client(tmp_path, monkeypatch)
    _seed_board(db, tmp_path, session_id="pending", plan=_plan())

    response = client.post(
        "/production/pending/visual-selection:confirm",
        json={
            "proposal_hash": "0" * 64,
            "selected_candidate_ids": ["beat-0-candidate-0", "beat-1-candidate-0"],
        },
        headers=_HEADERS,
    )

    assert response.status_code == 409
    assert "stale visual proposal" in response.text
    assert _latest_job_id(db, "pending") is None


@pytest.mark.parametrize(
    ("selected", "detail"),
    [
        (["missing"], "not among"),
        (["beat-0-candidate-0"], "exactly one"),
        (["beat-0-candidate-0", "beat-0-candidate-1"], "exactly one"),
    ],
)
def test_confirm_visual_selection_validates_one_candidate_per_beat(
    tmp_path: Path, monkeypatch: Any, selected: list[str], detail: str
) -> None:
    client, db = _client(tmp_path, monkeypatch)
    _seed_board(db, tmp_path, session_id="pending", plan=_plan())

    response = client.post(
        "/production/pending/visual-selection:confirm",
        json={"proposal_hash": _HASH_A, "selected_candidate_ids": selected},
        headers=_HEADERS,
    )

    assert response.status_code == 422
    assert detail in response.text
    assert _latest_job_id(db, "pending") is None


def test_confirm_visual_selection_busy_conflict_writes_nothing(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client, db = _client(tmp_path, monkeypatch)
    _, board = _seed_board(db, tmp_path, session_id="pending", plan=_plan())
    before = board.load("visual_plan").model_dump_json()  # type: ignore[union-attr]
    _seed_job(db, "pending", job_status="running")

    response = client.post(
        "/production/pending/visual-selection:confirm",
        json={
            "proposal_hash": _HASH_A,
            "selected_candidate_ids": ["beat-0-candidate-0", "beat-1-candidate-0"],
        },
        headers=_HEADERS,
    )

    assert response.status_code == 409
    assert "production run is in progress" in response.text
    assert board.load("visual_plan").model_dump_json() == before  # type: ignore[union-attr]


def test_confirm_visual_selection_stamps_choices_and_enqueues_real_resume(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client, db = _client(tmp_path, monkeypatch)
    _, board = _seed_board(db, tmp_path, session_id="pending", plan=_plan())
    core_before = _unchanged_core(board)

    response = client.post(
        "/production/pending/visual-selection:confirm",
        json={
            "proposal_hash": _HASH_A,
            "selected_candidate_ids": ["beat-0-candidate-1", "beat-1-candidate-0"],
        },
        headers=_HEADERS,
    )

    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    assert job_id and _latest_job_id(db, "pending") == job_id
    job = repos.get_job(db, job_id)
    assert job is not None and "message" not in json.loads(str(job["payload_json"]))
    saved = board.load("visual_plan")
    assert isinstance(saved, VisualPlan)
    assert [beat.selected_candidate_id for beat in saved.beats] == [
        "beat-0-candidate-1",
        "beat-1-candidate-0",
    ]
    assert saved.confirmed_utc is not None
    assert _unchanged_core(board) == core_before


def test_visual_reconfirm_is_noop_and_heals_missing_resume(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client, db = _client(tmp_path, monkeypatch)
    _, board = _seed_board(db, tmp_path, session_id="confirmed", plan=_plan(confirmed=True))
    version = board.load("visual_plan").version  # type: ignore[union-attr]

    response = client.post(
        "/production/confirmed/visual-selection:confirm",
        json={
            "proposal_hash": _HASH_A,
            "selected_candidate_ids": ["beat-0-candidate-0", "beat-1-candidate-0"],
        },
        headers=_HEADERS,
    )

    assert response.status_code == 202, response.text
    assert response.json()["already_current"] is True
    assert response.json()["job_id"] == _latest_job_id(db, "confirmed")
    assert board.load("visual_plan").version == version  # type: ignore[union-attr]


def test_visual_confirm_keeps_stamp_after_resume_failure_then_heals_forward(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client, db = _client(tmp_path, monkeypatch)
    _, board = _seed_board(db, tmp_path, session_id="pending", plan=_plan())
    real_resume = __import__(
        "laura.api.short_creator", fromlist=["run_production_resume"]
    ).run_production_resume
    calls = 0

    def flaky_resume(db: Database, session_id: str) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPException(503, "resume unavailable")
        return real_resume(db, session_id)

    monkeypatch.setattr("laura.api.short_creator.run_production_resume", flaky_resume)
    payload = {
        "proposal_hash": _HASH_A,
        "selected_candidate_ids": ["beat-0-candidate-0", "beat-1-candidate-0"],
    }

    first = client.post(
        "/production/pending/visual-selection:confirm", json=payload, headers=_HEADERS
    )
    assert first.status_code == 503
    stamped = board.load("visual_plan")
    assert isinstance(stamped, VisualPlan) and stamped.confirmed_utc is not None
    stamped_version = stamped.version

    second = client.post(
        "/production/pending/visual-selection:confirm", json=payload, headers=_HEADERS
    )
    assert second.status_code == 202, second.text
    assert second.json()["already_current"] is True
    assert board.load("visual_plan").version == stamped_version  # type: ignore[union-attr]
    assert _latest_job_id(db, "pending") == second.json()["job_id"]


def test_confirm_contact_sheet_stamps_current_hash_and_enqueues_resume(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client, db = _client(tmp_path, monkeypatch)
    sheet = _sheet()
    _, board = _seed_board(
        db,
        tmp_path,
        session_id="sheet",
        plan=_plan(confirmed=True),
        sheet=sheet,
        contact_sheet_gate=True,
    )
    core_before = _unchanged_core(board)

    response = client.post(
        "/production/sheet/contact-sheet:confirm",
        json={"contact_sheet_hash": content_hash(sheet)},
        headers=_HEADERS,
    )

    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    assert job_id == _latest_job_id(db, "sheet")
    job = repos.get_job(db, job_id)
    assert job is not None and "message" not in json.loads(str(job["payload_json"]))
    assert board.status()["contact_sheet_gate"]["approved"] is True
    assert _unchanged_core(board) == core_before


def test_confirm_contact_sheet_rejects_stale_hash_without_job(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client, db = _client(tmp_path, monkeypatch)
    _seed_board(
        db,
        tmp_path,
        session_id="sheet",
        plan=_plan(confirmed=True),
        sheet=_sheet(),
        contact_sheet_gate=True,
    )

    response = client.post(
        "/production/sheet/contact-sheet:confirm",
        json={"contact_sheet_hash": "0" * 64},
        headers=_HEADERS,
    )

    assert response.status_code == 409
    assert "stale contact sheet" in response.text
    assert _latest_job_id(db, "sheet") is None


def test_confirm_contact_sheet_busy_conflict_writes_nothing(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client, db = _client(tmp_path, monkeypatch)
    sheet = _sheet()
    _, board = _seed_board(
        db,
        tmp_path,
        session_id="sheet",
        plan=_plan(confirmed=True),
        sheet=sheet,
        contact_sheet_gate=True,
    )
    _seed_job(db, "sheet", job_status="queued")

    response = client.post(
        "/production/sheet/contact-sheet:confirm",
        json={"contact_sheet_hash": content_hash(sheet)},
        headers=_HEADERS,
    )

    assert response.status_code == 409
    assert board.status()["contact_sheet_gate"]["approved"] is False


def test_contact_sheet_reconfirm_is_noop_and_heals_missing_resume(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client, db = _client(tmp_path, monkeypatch)
    sheet = _sheet()
    _, board = _seed_board(
        db,
        tmp_path,
        session_id="sheet",
        plan=_plan(confirmed=True),
        sheet=sheet,
        contact_sheet_gate=True,
    )
    board.set_contact_sheet_approved(_NOW, content_hash(sheet))

    response = client.post(
        "/production/sheet/contact-sheet:confirm",
        json={"contact_sheet_hash": content_hash(sheet)},
        headers=_HEADERS,
    )

    assert response.status_code == 202, response.text
    assert response.json()["already_current"] is True
    assert response.json()["job_id"] == _latest_job_id(db, "sheet")
    assert board.meta().contact_sheet_approved_utc == _NOW


def test_contact_sheet_confirm_keeps_stamp_after_resume_failure_then_heals_forward(
    tmp_path: Path, monkeypatch: Any
) -> None:
    db = _client(tmp_path, monkeypatch)[1]
    sheet = _sheet()
    _, board = _seed_board(
        db,
        tmp_path,
        session_id="sheet",
        plan=_plan(confirmed=True),
        sheet=sheet,
        contact_sheet_gate=True,
    )
    calls = 0

    def flaky_resume(db: Database, session_id: str) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPException(503, "resume unavailable")
        return {"session_id": session_id, "job_id": "job-healed", "warnings": []}

    monkeypatch.setattr("laura.api.short_creator.run_production_resume", flaky_resume)
    current_hash = content_hash(sheet)

    with pytest.raises(HTTPException):
        confirm_contact_sheet(db, "sheet", current_hash)
    assert board.status()["contact_sheet_gate"]["approved"] is True
    assert confirm_contact_sheet(db, "sheet", current_hash)["already_current"] is True
    assert calls == 2
