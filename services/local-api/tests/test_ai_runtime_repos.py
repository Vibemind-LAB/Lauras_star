from __future__ import annotations

from pathlib import Path

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, create_database


def _db(tmp_path: Path) -> Database:
    db = create_database(Settings(workspace_root=tmp_path, start_runner=False))
    db.migrate()
    return db


def test_ai_runtime_round_trips_json_fields(tmp_path: Path) -> None:
    db = _db(tmp_path)
    rt = repos.create_ai_runtime(
        db,
        kind="container",
        effect="lipsync",
        display_name="MuseTalk",
        container_image="laura-runtime-lipsync:local",
        container_name="laura-lipsync",
        port=8901,
        workspace_mount="workspace/ai-runtime/io",
        model_mount="E:/LauraModels/lipsync",
        requires_gpu=True,
        license_status="accepted",
    )

    assert rt["kind"] == "container"
    assert rt["effect"] == "lipsync"
    assert rt["requires_gpu"] is True
    assert rt["enabled"] is True

    ok = repos.update_ai_runtime_status(
        db,
        rt["id"],
        status={"state": "ready", "ready": True},
        capabilities={"effects": ["lipsync"], "metrics": ["sync_score"]},
    )
    assert ok is True

    loaded = repos.get_ai_runtime(db, rt["id"])
    assert loaded is not None
    assert loaded["status"] == {"state": "ready", "ready": True}
    assert loaded["capabilities"] == {"effects": ["lipsync"], "metrics": ["sync_score"]}
    assert repos.list_ai_runtimes(db, effect="lipsync")[0]["id"] == rt["id"]


def test_runtime_events_are_ordered_newest_first(tmp_path: Path) -> None:
    db = _db(tmp_path)
    rt = repos.create_ai_runtime(db, kind="stub", effect="voice", display_name="Stub Voice")
    repos.create_ai_runtime_event(
        db,
        runtime_id=rt["id"],
        event_type="health",
        level="info",
        message="ready",
        payload={"ready": True},
    )
    repos.create_ai_runtime_event(
        db,
        runtime_id=rt["id"],
        event_type="error",
        level="error",
        message="boom",
        payload={"code": "test"},
    )

    events = repos.list_ai_runtime_events(db, rt["id"])
    assert [event["message"] for event in events] == ["boom", "ready"]
    assert events[0]["payload"] == {"code": "test"}


def test_ai_persona_round_trips_policy_fields(tmp_path: Path) -> None:
    db = _db(tmp_path)
    project = repos.create_project(
        db,
        name="P",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(tmp_path),
    )
    consent = repos.create_consent_record(
        db,
        project_id=project["id"],
        subject_label="Laura Persona",
    )
    persona = repos.create_ai_persona(
        db,
        project_id=project["id"],
        name="Laura Persona",
        consent_id=consent["id"],
        allowed_effects=["voice", "lipsync"],
        preferred_runtimes={"voice": "rt-voice", "lipsync": "rt-lipsync"},
        style={"tone": "clear"},
    )

    loaded = repos.get_ai_persona(db, persona["id"])
    assert loaded is not None
    assert loaded["allowed_effects"] == ["voice", "lipsync"]
    assert loaded["preferred_runtimes"] == {"voice": "rt-voice", "lipsync": "rt-lipsync"}
    assert loaded["style"] == {"tone": "clear"}
    assert repos.list_ai_personas(db, project_id=project["id"])[0]["id"] == persona["id"]
