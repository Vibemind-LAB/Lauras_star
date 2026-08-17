"""Visual recut approval services and their HTTP endpoints.

The board and job state asserted here always lives in the real SQLite repository.  Provider
preflights are disabled in successful endpoint tests, but the resume itself still uses the
real production queue so ``latest_job_id`` proves that the endpoint enqueued a pure resume.
"""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from laura.api.short_creator import (
    confirm_contact_sheet,
    confirm_visual_selection,
    run_production_resume,
)
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
    VisualSceneCandidate,
    VisualSceneChoice,
    VisualSceneSelection,
    VisualShotCandidate,
    VoiceArtifact,
    VoiceSegment,
    content_hash,
)
from laura.short_creator.production_orchestrator import board_root_for
from laura.short_creator.production_tools import _rough_cut_source_hash
from laura.short_creator.visual_selection_state import capture_source_media_snapshot
from laura.short_creator.visual_timeline import apply_scene_selections

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


def _scene_candidate(
    order: int, choice: int = 0, *, max_duration_s: int = 10
) -> VisualSceneCandidate:
    start = order * 900 + choice * 330
    return VisualSceneCandidate(
        candidate_id=f"scene-{order}-candidate-{choice}",
        rough_cut_order=order,
        scene_number=order + 1,
        window_index=choice,
        src_start_frame=start,
        src_end_frame_exclusive=start + max_duration_s * 30,
        thumb_frame=start + max_duration_s * 15,
        max_duration_s=max_duration_s,
        description=f"Rough-Cut scene {order + 1}",
        transcript_snippet=f"workflow step {order + 1}",
        rationale="covers the Rough-Cut row",
        score=1.0 - choice / 10,
    )


def _v2_plan(
    *, scene_count: int = 4, voice_frames: int = 600, max_duration_s: int = 10
) -> VisualPlan:
    return VisualPlan(
        version=2,
        proposal_hash=_HASH_A,
        request_hash=_HASH_B,
        scene_choices=[
            VisualSceneChoice(
                rough_cut_order=order,
                scene_number=order + 1,
                description=f"Rough-Cut scene {order + 1}",
                transcript=f"workflow step {order + 1}",
                rationale="keeps the Rough-Cut row available",
                candidates=[
                    _scene_candidate(order, choice, max_duration_s=max_duration_s)
                    for choice in range(2)
                ],
                recommended_candidate_id=f"scene-{order}-candidate-0",
                recommended_included=True,
                recommended_duration_s=max_duration_s,
            )
            for order in range(scene_count)
        ],
        rough_cut_scene_count=scene_count,
        voice_total_frames=voice_frames,
        fps=30.0,
    )


def _scene_selections(
    durations: list[int],
    *,
    included: list[bool] | None = None,
    first_choice: int = 0,
) -> list[VisualSceneSelection]:
    include_flags = included if included is not None else [True] * len(durations)
    return [
        VisualSceneSelection(
            rough_cut_order=order,
            candidate_id=f"scene-{order}-candidate-{first_choice if order == 0 else 0}",
            included=include_flags[order],
            requested_duration_s=duration,
        )
        for order, duration in enumerate(durations)
    ]


def _selection_json(selections: list[VisualSceneSelection]) -> list[dict[str, Any]]:
    return [selection.model_dump() for selection in selections]


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
    source_path = tmp_path / session_id / "source.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"visual-source")
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
        source_path=str(source_path),
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
    voice_s = (
        float(plan.voice_total_frames) / float(plan.fps)
        if plan is not None
        and plan.scene_choices
        and plan.voice_total_frames is not None
        and plan.fps is not None
        else 4.0
    )
    voice = VoiceArtifact(
        script_hash=_HASH_B,
        mp3_path="/tmp/voice.mp3",
        voice_s=voice_s,
        segments=[
            VoiceSegment(
                scene_number=1,
                chapter=1,
                line_hash="c" * 64,
                mp3_path="/tmp/voice-1.mp3",
                duration_s=max(0.1, voice_s - 0.3),
                offset_s=0.0,
            )
        ],
    )
    board.save("storyline", storyline)
    board.save("script", script)
    board.save("voice", voice)
    visual_request = VisualRecutRequest(
        user_request="rebuild visuals",
        script_version=script.version,
        script_hash=content_hash(script),
        voice_version=voice.version,
        voice_hash=content_hash(voice),
    )
    if request:
        board.save("visual_recut_request", visual_request)
    if plan is not None and plan.scene_choices:
        assert plan.fps is not None
        assert plan.voice_total_frames is not None
        timeline = repos.create_timeline(
            db,
            project_id=str(project["id"]),
            name="Rough Cut",
            kind="rough_cut",
            created_from=str(asset["id"]),
        )
        scene_frames = 900
        scene_count = len(plan.scene_choices)
        repos.add_timeline_clip(
            db,
            timeline_id=str(timeline["id"]),
            asset_id=str(asset["id"]),
            src_in_frame=0,
            src_out_frame_exclusive=scene_frames * scene_count,
            seq_in_frame=0,
            seq_out_frame_exclusive=scene_frames * scene_count,
            lane=0,
            role="base",
        )
        repos.replace_scenes(
            db,
            str(project["id"]),
            str(timeline["id"]),
            [
                (order * scene_frames, (order + 1) * scene_frames)
                for order in range(scene_count)
            ],
        )
        rough_cut_hash = _rough_cut_source_hash(db, str(asset["id"]))
        assert rough_cut_hash is not None
        snapshot = capture_source_media_snapshot(
            db,
            asset_id=str(asset["id"]),
            rough_cut_hash=rough_cut_hash,
            fps=plan.fps,
            voice_hash=content_hash(voice),
            voice_total_frames=plan.voice_total_frames,
            script_hash=content_hash(script),
            request_hash=content_hash(visual_request),
            strong=True,
        )
        assert snapshot.strong_hash is not None
        plan = plan.model_copy(
            update={
                "parents": {
                    "visual_recut_request": content_hash(visual_request),
                    "script": content_hash(script),
                    "voice": content_hash(voice),
                    "rough_cut": rough_cut_hash,
                    "source_media": snapshot.strong_hash,
                    "source_media_quick": snapshot.quick_hash,
                }
            }
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


def _synchronize_first_board_open(monkeypatch: Any) -> None:
    """Make two callers enter a confirmation with the same pre-lock session snapshot."""
    from laura.api import short_creator

    original = short_creator._open_board_or_404
    barrier = threading.Barrier(2)
    local = threading.local()

    def synchronized(db: Database, asset_id: str, session_id: str) -> Board:
        board = original(db, asset_id, session_id)
        if not getattr(local, "synchronized", False):
            local.synchronized = True
            barrier.wait(timeout=5)
        return board

    monkeypatch.setattr(short_creator, "_open_board_or_404", synchronized)


def _parallel_outcomes(calls: list[Any]) -> list[tuple[str, int | None]]:
    def capture(call: Any) -> tuple[str, int | None]:
        try:
            call()
        except HTTPException as exc:
            return ("error", exc.status_code)
        return ("ok", None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        return sorted(pool.map(capture, calls))


def _production_job_count(db: Database) -> int:
    return sum(job["kind"] == "production.run" for job in repos.list_jobs(db))


def test_visual_selection_draft_defaults_save_and_survive_status_reload(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Catches partial choices living only in Electron memory or starting a job."""
    client, db = _client(tmp_path, monkeypatch)
    _, board = _seed_board(db, tmp_path, session_id="draft-roundtrip", plan=_v2_plan())
    plan = cast(VisualPlan, board.load("visual_plan"))

    initial = client.get(
        "/production/draft-roundtrip/visual-selection/draft", headers=_HEADERS
    )
    assert initial.status_code == 200, initial.text
    assert initial.json()["revision"] is None
    assert [row["rough_cut_order"] for row in initial.json()["selections"]] == [0, 1, 2, 3]

    selections = _scene_selections(
        [1, 2, 3, 4], included=[False, False, False, False], first_choice=1
    )
    saved = client.put(
        "/production/draft-roundtrip/visual-selection/draft",
        json={
            "proposal_hash": plan.proposal_hash,
            "expected_revision": None,
            "selections": _selection_json(selections),
        },
        headers=_HEADERS,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1
    assert saved.json()["selections"] == _selection_json(selections)

    status_response = client.get("/production/draft-roundtrip", headers=_HEADERS)
    assert status_response.status_code == 200
    assert status_response.json()["visual_selection_gate"]["draft"] == saved.json()
    assert _latest_job_id(db, "draft-roundtrip") is None
    assert _production_job_count(db) == 0


def test_visual_selection_draft_compare_and_swap_returns_current_server_state(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Catches two windows silently overwriting the newer visual-selection revision."""
    client, db = _client(tmp_path, monkeypatch)
    _, board = _seed_board(db, tmp_path, session_id="draft-conflict", plan=_v2_plan())
    plan = cast(VisualPlan, board.load("visual_plan"))
    first = _scene_selections([2, 2, 2, 2])
    second = _scene_selections([3, 3, 3, 3], included=[True, True, True, False])
    stale = _scene_selections([4, 4, 4, 4])

    created = client.put(
        "/production/draft-conflict/visual-selection/draft",
        json={
            "proposal_hash": plan.proposal_hash,
            "expected_revision": None,
            "selections": _selection_json(first),
        },
        headers=_HEADERS,
    )
    assert created.status_code == 200
    updated = client.put(
        "/production/draft-conflict/visual-selection/draft",
        json={
            "proposal_hash": plan.proposal_hash,
            "expected_revision": 1,
            "selections": _selection_json(second),
        },
        headers=_HEADERS,
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2

    conflict = client.put(
        "/production/draft-conflict/visual-selection/draft",
        json={
            "proposal_hash": plan.proposal_hash,
            "expected_revision": 1,
            "selections": _selection_json(stale),
        },
        headers=_HEADERS,
    )
    assert conflict.status_code == 409
    detail = conflict.json()["detail"]
    assert detail["code"] == "revision_conflict"
    assert detail["current"]["revision"] == 2
    assert detail["current"]["selections"] == _selection_json(second)
    assert _production_job_count(db) == 0


def test_visual_selection_draft_rejects_stale_proposal_and_malformed_order(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Catches a structurally unsafe or old proposal becoming the durable draft."""
    client, db = _client(tmp_path, monkeypatch)
    _, board = _seed_board(db, tmp_path, session_id="draft-invalid", plan=_v2_plan())
    plan = cast(VisualPlan, board.load("visual_plan"))
    selections = _scene_selections([2, 2, 2, 2])

    stale = client.put(
        "/production/draft-invalid/visual-selection/draft",
        json={
            "proposal_hash": "0" * 64,
            "expected_revision": None,
            "selections": _selection_json(selections),
        },
        headers=_HEADERS,
    )
    assert stale.status_code == 409

    malformed = client.put(
        "/production/draft-invalid/visual-selection/draft",
        json={
            "proposal_hash": plan.proposal_hash,
            "expected_revision": None,
            "selections": _selection_json(list(reversed(selections))),
        },
        headers=_HEADERS,
    )
    assert malformed.status_code == 422
    assert repos.get_visual_selection_draft(db, "draft-invalid") is None
    assert _production_job_count(db) == 0


def test_confirm_v2_visual_selection_deletes_saved_draft_before_resume(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Catches a confirmed selection resurfacing later as an open partial draft."""
    client, db = _client(tmp_path, monkeypatch)
    _, board = _seed_board(db, tmp_path, session_id="draft-confirm", plan=_v2_plan())
    plan = cast(VisualPlan, board.load("visual_plan"))
    selections = _scene_selections([5, 5, 5, 5])
    saved = client.put(
        "/production/draft-confirm/visual-selection/draft",
        json={
            "proposal_hash": plan.proposal_hash,
            "expected_revision": None,
            "selections": _selection_json(selections),
        },
        headers=_HEADERS,
    )
    assert saved.status_code == 200

    confirmed = client.post(
        "/production/draft-confirm/visual-selection:confirm",
        json={
            "proposal_hash": plan.proposal_hash,
            "selections": _selection_json(selections),
        },
        headers=_HEADERS,
    )

    assert confirmed.status_code == 202, confirmed.text
    assert repos.get_visual_selection_draft(db, "draft-confirm") is None
    assert _production_job_count(db) == 1


def test_confirm_v2_visual_selection_rejects_same_metadata_source_content_change(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Catches final confirmation skipping the strong Drive-file content check."""
    client, db = _client(tmp_path, monkeypatch)
    asset_id, board = _seed_board(db, tmp_path, session_id="draft-source-stale", plan=_v2_plan())
    plan = cast(VisualPlan, board.load("visual_plan"))
    before = plan.model_dump_json()
    asset = repos.get_asset(db, asset_id)
    assert asset is not None
    source = Path(str(asset["source_path"]))
    original_stat = source.stat()
    source.write_bytes(b"changed-video")
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    confirmed = client.post(
        "/production/draft-source-stale/visual-selection:confirm",
        json={
            "proposal_hash": plan.proposal_hash,
            "selections": _selection_json(_scene_selections([5, 5, 5, 5])),
        },
        headers=_HEADERS,
    )

    assert confirmed.status_code == 409
    assert "source_content_changed" in confirmed.text
    assert cast(VisualPlan, board.load("visual_plan")).model_dump_json() == before
    assert _production_job_count(db) == 0


def test_confirm_v2_visual_selection_binds_candidate_inclusion_and_duration(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client, db = _client(tmp_path, monkeypatch)
    _, board = _seed_board(db, tmp_path, session_id="v2-pending", plan=_v2_plan())
    chosen = _scene_selections([5, 5, 5, 5])

    response = client.post(
        "/production/v2-pending/visual-selection:confirm",
        json={"proposal_hash": _HASH_A, "selections": _selection_json(chosen)},
        headers=_HEADERS,
    )

    assert response.status_code == 202, response.text
    saved = cast(VisualPlan, board.load("visual_plan"))
    assert saved.selection_hash is not None
    assert [choice.selected_candidate_id for choice in saved.scene_choices] == [
        f"scene-{order}-candidate-0" for order in range(4)
    ]
    assert [choice.included for choice in saved.scene_choices] == [True] * 4
    assert [choice.requested_duration_s for choice in saved.scene_choices] == [5] * 4
    assert _latest_job_id(db, "v2-pending") == response.json()["job_id"]


def test_confirm_v2_visual_selection_rejects_stale_hash_without_job(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client, db = _client(tmp_path, monkeypatch)
    _, board = _seed_board(db, tmp_path, session_id="v2-stale", plan=_v2_plan())
    before = cast(VisualPlan, board.load("visual_plan")).model_dump_json()

    response = client.post(
        "/production/v2-stale/visual-selection:confirm",
        json={
            "proposal_hash": "0" * 64,
            "selections": _selection_json(_scene_selections([5, 5, 5, 5])),
        },
        headers=_HEADERS,
    )

    assert response.status_code == 409
    assert "stale visual proposal" in response.text
    assert _latest_job_id(db, "v2-stale") is None
    assert cast(VisualPlan, board.load("visual_plan")).model_dump_json() == before


@pytest.mark.parametrize(
    "order",
    [[3, 2, 1, 0], [1, 0, 3, 2]],
    ids=("reversed", "shuffled"),
)
def test_http_v2_visual_selection_requires_exact_rough_cut_order_without_mutation(
    tmp_path: Path, monkeypatch: Any, order: list[int]
) -> None:
    client, db = _client(tmp_path, monkeypatch)
    _, board = _seed_board(
        db,
        tmp_path,
        session_id="v2-http-order",
        plan=_v2_plan(),
        sheet=_sheet(),
    )
    before_plan = cast(VisualPlan, board.load("visual_plan"))
    before_sheet = cast(ContactSheet, board.load("contact_sheet")).model_dump_json()
    chosen = _scene_selections([5, 5, 5, 5])

    response = client.post(
        "/production/v2-http-order/visual-selection:confirm",
        json={
            "proposal_hash": _HASH_A,
            "selections": _selection_json([chosen[index] for index in order]),
        },
        headers=_HEADERS,
    )

    assert response.status_code == 422
    assert "Rough-Cut order" in response.text
    after_plan = cast(VisualPlan, board.load("visual_plan"))
    assert after_plan.version == before_plan.version
    assert after_plan.selection_hash == before_plan.selection_hash
    assert after_plan.model_dump_json() == before_plan.model_dump_json()
    assert cast(ContactSheet, board.load("contact_sheet")).model_dump_json() == before_sheet
    assert _latest_job_id(db, "v2-http-order") is None
    assert _production_job_count(db) == 0


@pytest.mark.parametrize(
    "order",
    [[3, 2, 1, 0], [2, 0, 3, 1]],
    ids=("reversed", "shuffled"),
)
def test_shared_v2_visual_service_requires_exact_rough_cut_order_without_mutation(
    tmp_path: Path, monkeypatch: Any, order: list[int]
) -> None:
    db = _client(tmp_path, monkeypatch)[1]
    _, board = _seed_board(
        db,
        tmp_path,
        session_id="v2-service-order",
        plan=_v2_plan(),
        sheet=_sheet(),
    )
    before_plan = cast(VisualPlan, board.load("visual_plan"))
    before_sheet = cast(ContactSheet, board.load("contact_sheet")).model_dump_json()
    chosen = _scene_selections([5, 5, 5, 5])

    with pytest.raises(HTTPException, match="Rough-Cut order") as exc_info:
        confirm_visual_selection(
            db,
            "v2-service-order",
            _HASH_A,
            selections=[chosen[index] for index in order],
        )

    assert exc_info.value.status_code == 422
    after_plan = cast(VisualPlan, board.load("visual_plan"))
    assert after_plan.version == before_plan.version
    assert after_plan.selection_hash == before_plan.selection_hash
    assert after_plan.model_dump_json() == before_plan.model_dump_json()
    assert cast(ContactSheet, board.load("contact_sheet")).model_dump_json() == before_sheet
    assert _latest_job_id(db, "v2-service-order") is None
    assert _production_job_count(db) == 0


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("rough_cut_order", "0"),
        ("included", 1),
        ("requested_duration_s", "5"),
    ],
)
def test_http_v2_visual_selection_rejects_coercible_nested_scalars_without_mutation(
    tmp_path: Path, monkeypatch: Any, field: str, invalid_value: object
) -> None:
    client, db = _client(tmp_path, monkeypatch)
    _, board = _seed_board(db, tmp_path, session_id="v2-strict", plan=_v2_plan())
    before = cast(VisualPlan, board.load("visual_plan")).model_dump_json()
    selected = _selection_json(_scene_selections([5, 5, 5, 5]))
    selected[0][field] = invalid_value

    response = client.post(
        "/production/v2-strict/visual-selection:confirm",
        json={"proposal_hash": _HASH_A, "selections": selected},
        headers=_HEADERS,
    )

    assert response.status_code == 422
    assert cast(VisualPlan, board.load("visual_plan")).model_dump_json() == before
    assert _latest_job_id(db, "v2-strict") is None
    assert _production_job_count(db) == 0


@pytest.mark.parametrize("invalid_hash", ["A" * 64, "g" * 64], ids=("uppercase", "nonhex"))
def test_http_visual_selection_requires_lowercase_sha256_without_mutation(
    tmp_path: Path, monkeypatch: Any, invalid_hash: str
) -> None:
    client, db = _client(tmp_path, monkeypatch)
    _, board = _seed_board(db, tmp_path, session_id="v2-hash", plan=_v2_plan())
    before = cast(VisualPlan, board.load("visual_plan")).model_dump_json()

    response = client.post(
        "/production/v2-hash/visual-selection:confirm",
        json={
            "proposal_hash": invalid_hash,
            "selections": _selection_json(_scene_selections([5, 5, 5, 5])),
        },
        headers=_HEADERS,
    )

    assert response.status_code == 422
    assert cast(VisualPlan, board.load("visual_plan")).model_dump_json() == before
    assert _latest_job_id(db, "v2-hash") is None
    assert _production_job_count(db) == 0


@pytest.mark.parametrize(
    ("plan", "chosen", "detail"),
    [
        (_v2_plan(), _scene_selections([5, 5, 5]), "exactly once"),
        (
            _v2_plan(),
            _scene_selections([5, 5, 5, 5])[:-1]
            + [_scene_selections([5, 5, 5, 5])[0]],
            "exactly once",
        ),
        (
            _v2_plan(),
            _scene_selections(
                [5, 5, 5, 5], included=[True, True, False, False]
            ),
            "at least three",
        ),
        (_v2_plan(voice_frames=900), _scene_selections([5, 5, 5, 5]), "cover the Voice"),
        (
            _v2_plan(max_duration_s=4),
            _scene_selections([5, 5, 5, 5]),
            "candidate capacity",
        ),
    ],
    ids=("missing-row", "duplicate-row", "too-few-included", "undercoverage", "capacity"),
)
def test_confirm_v2_visual_selection_rejects_invalid_decisions_without_job(
    tmp_path: Path,
    monkeypatch: Any,
    plan: VisualPlan,
    chosen: list[VisualSceneSelection],
    detail: str,
) -> None:
    client, db = _client(tmp_path, monkeypatch)
    _, board = _seed_board(db, tmp_path, session_id="v2-invalid", plan=plan)
    before = cast(VisualPlan, board.load("visual_plan")).model_dump_json()

    response = client.post(
        "/production/v2-invalid/visual-selection:confirm",
        json={"proposal_hash": _HASH_A, "selections": _selection_json(chosen)},
        headers=_HEADERS,
    )

    assert response.status_code == 422
    assert detail in response.text
    assert _latest_job_id(db, "v2-invalid") is None
    assert cast(VisualPlan, board.load("visual_plan")).model_dump_json() == before


def test_confirm_v2_visual_selection_requires_exactly_one_payload_shape(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client, db = _client(tmp_path, monkeypatch)
    _seed_board(db, tmp_path, session_id="v2-shape", plan=_v2_plan())
    selected = _selection_json(_scene_selections([5, 5, 5, 5]))

    neither = client.post(
        "/production/v2-shape/visual-selection:confirm",
        json={"proposal_hash": _HASH_A},
        headers=_HEADERS,
    )
    both = client.post(
        "/production/v2-shape/visual-selection:confirm",
        json={
            "proposal_hash": _HASH_A,
            "selections": selected,
            "selected_candidate_ids": ["scene-0-candidate-0"],
        },
        headers=_HEADERS,
    )

    assert neither.status_code == 422
    assert both.status_code == 422
    assert _latest_job_id(db, "v2-shape") is None


def test_v2_visual_reconfirm_is_idempotent_and_heals_missing_resume(
    tmp_path: Path, monkeypatch: Any
) -> None:
    chosen = _scene_selections([5, 5, 5, 5])
    confirmed = apply_scene_selections(_v2_plan(), chosen, _NOW)
    client, db = _client(tmp_path, monkeypatch)
    _, board = _seed_board(db, tmp_path, session_id="v2-confirmed", plan=confirmed)
    version = cast(VisualPlan, board.load("visual_plan")).version

    response = client.post(
        "/production/v2-confirmed/visual-selection:confirm",
        json={"proposal_hash": _HASH_A, "selections": _selection_json(chosen)},
        headers=_HEADERS,
    )

    assert response.status_code == 202, response.text
    assert response.json()["already_current"] is True
    assert response.json()["job_id"] == _latest_job_id(db, "v2-confirmed")
    assert cast(VisualPlan, board.load("visual_plan")).version == version
    assert _production_job_count(db) == 1


def test_changed_v2_confirmation_reopens_completed_visual_tail_only(
    tmp_path: Path, monkeypatch: Any
) -> None:
    original = _scene_selections([5, 5, 5, 5])
    confirmed = apply_scene_selections(_v2_plan(), original, _NOW)
    client, db = _client(tmp_path, monkeypatch)
    _, board = _seed_board(
        db,
        tmp_path,
        session_id="v2-complete",
        plan=confirmed,
        sheet=_sheet(),
        contact_sheet_gate=True,
    )
    board.set_status("complete")
    core_before = _unchanged_core(board)
    changed = _scene_selections([5, 5, 5, 5], first_choice=1)

    response = client.post(
        "/production/v2-complete/visual-selection:confirm",
        json={"proposal_hash": _HASH_A, "selections": _selection_json(changed)},
        headers=_HEADERS,
    )

    assert response.status_code == 202, response.text
    saved = cast(VisualPlan, board.load("visual_plan"))
    assert saved.scene_choices[0].selected_candidate_id == "scene-0-candidate-1"
    assert board.load("contact_sheet") is None
    assert _latest_job_id(db, "v2-complete") == response.json()["job_id"]
    assert _production_job_count(db) == 1
    assert _unchanged_core(board) == core_before


def test_parallel_v2_visual_submit_enqueues_exactly_one_resume(
    tmp_path: Path, monkeypatch: Any
) -> None:
    db = _client(tmp_path, monkeypatch)[1]
    _seed_board(db, tmp_path, session_id="parallel-v2", plan=_v2_plan())
    _synchronize_first_board_open(monkeypatch)
    chosen = _scene_selections([5, 5, 5, 5])

    outcomes = _parallel_outcomes(
        [
            lambda: confirm_visual_selection(
                db, "parallel-v2", _HASH_A, selections=chosen
            ),
            lambda: confirm_visual_selection(
                db, "parallel-v2", _HASH_A, selections=chosen
            ),
        ]
    )

    assert outcomes == [("error", 409), ("ok", None)]
    assert _production_job_count(db) == 1


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


def test_parallel_visual_reconfirm_enqueues_exactly_one_resume(
    tmp_path: Path, monkeypatch: Any
) -> None:
    db = _client(tmp_path, monkeypatch)[1]
    _seed_board(db, tmp_path, session_id="parallel-visual", plan=_plan())
    _synchronize_first_board_open(monkeypatch)
    selected = ["beat-0-candidate-0", "beat-1-candidate-0"]

    outcomes = _parallel_outcomes(
        [
            lambda: confirm_visual_selection(
                db,
                "parallel-visual",
                _HASH_A,
                selected_candidate_ids=selected,
            ),
            lambda: confirm_visual_selection(
                db,
                "parallel-visual",
                _HASH_A,
                selected_candidate_ids=selected,
            ),
        ]
    )

    assert outcomes == [("error", 409), ("ok", None)]
    assert _production_job_count(db) == 1


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
    real_resume = run_production_resume
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


def test_parallel_contact_sheet_reconfirm_enqueues_exactly_one_resume(
    tmp_path: Path, monkeypatch: Any
) -> None:
    db = _client(tmp_path, monkeypatch)[1]
    sheet = _sheet()
    _seed_board(
        db,
        tmp_path,
        session_id="parallel-sheet",
        plan=_plan(confirmed=True),
        sheet=sheet,
        contact_sheet_gate=True,
    )
    _synchronize_first_board_open(monkeypatch)
    sheet_hash = content_hash(sheet)

    outcomes = _parallel_outcomes(
        [
            lambda: confirm_contact_sheet(db, "parallel-sheet", sheet_hash),
            lambda: confirm_contact_sheet(db, "parallel-sheet", sheet_hash),
        ]
    )

    assert outcomes == [("error", 409), ("ok", None)]
    assert _production_job_count(db) == 1


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
