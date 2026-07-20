"""``production.run`` job handler: run_production wiring + coarse session run log (Slice 4,
Task 4).

The fixture mirrors ``tests/test_production_orchestrator.py``'s ``_seed_scene``, duplicated here
per this repo's "self-contained test file" convention (see that file's own docstring). The
escalation-ladder itself (stage A/B, hard-fail, resume, follow-up messages, ...) is already
exhaustively covered there via a fake ``ExecuteFn`` — these tests are only about the handler's
OWN contract on top of it: payload parsing (including the whitespace-message normalization),
returning ``run_production``'s result unchanged, the coarse NDJSON run log, registry wiring, and
queue routing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.jobs.queues import QUEUE_ANALYSIS_CPU, queue_for
from laura.jobs.runner import JobContext, JobHandler
from laura.short_creator import handlers, orchestrator, providers

FPS = 30
SCENE_FRAMES = 150  # 150 frames @ 30fps = 5.0s


def _seed_scene(tmp_path: Path) -> tuple[Database, str]:
    """Project + asset + succeeded analysis run w/ transcript + a ONE-scene rough cut.

    Returns ``(db, asset_id)``. Mirrors ``test_production_orchestrator.py``'s ``_seed_scene``.
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


def _ctx(db: Database, payload: dict[str, Any]) -> JobContext:
    return JobContext(
        job_id="job-test", kind="production.run", queue=QUEUE_ANALYSIS_CPU, payload=payload, db=db
    )


def _ok_execute(
    db: Database,
    config: providers.AgentConfig,
    stage: providers.Stage,
    kind: orchestrator.TeamKind,
    task: str,
) -> orchestrator.StageOutcome:
    return orchestrator.StageOutcome(
        status="ok", weak=False, summary="done", team=kind, stage=stage
    )


def _runs_dir(tmp_path: Path, session_id: str) -> Path:
    return tmp_path / "ws" / "proj" / "agent-runs" / session_id / "runs"


# --- happy path / pass-through ----------------------------------------------------------------


def test_handle_production_run_returns_run_production_dict_unchanged(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    payload = {
        "asset_id": asset_id,
        "session_id": "sess1",
        "task": "overview short",
        "target_seconds": 20,
    }

    out = handlers.handle_production_run(_ctx(db, payload), execute=_ok_execute)

    assert out["ok"] is True
    assert out["status"] == "ok"
    assert out["stage"] == "A"
    assert out["team"] == "magentic"
    assert out["escalated"] is False
    assert out["session_id"] == "sess1"
    assert out["export_id"] is None
    assert out["resume_point"]  # non-empty
    assert out["board"]["meta"]["session_id"] == "sess1"


# --- asset missing -----------------------------------------------------------------------------


def test_handle_production_run_asset_missing_returns_ok_false_no_raise(tmp_path: Path) -> None:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db: Database = SqliteDatabase(settings.db_path)
    db.migrate()
    payload = {"asset_id": "does-not-exist", "session_id": "sess1", "task": "t"}

    out = handlers.handle_production_run(_ctx(db, payload))

    assert out == {"ok": False, "error": "asset not found", "session_id": "sess1", "restored": []}


# --- coarse NDJSON run log -----------------------------------------------------------------


def test_handle_production_run_writes_run_log_meta_and_done_lines(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    payload = {"asset_id": asset_id, "session_id": "sess2", "task": "overview short"}

    out = handlers.handle_production_run(_ctx(db, payload), execute=_ok_execute)
    assert out["ok"] is True

    logs = sorted(_runs_dir(tmp_path, "sess2").glob("*.ndjson"))
    assert len(logs) == 1, "run log file missing"
    raw_lines = logs[0].read_text(encoding="utf-8").splitlines()
    lines = [json.loads(line) for line in raw_lines if line.strip()]
    assert len(lines) == 2
    meta, done = lines
    assert meta == {
        "type": "meta",
        "asset_id": asset_id,
        "session_id": "sess2",
        "task": "overview short",
    }
    assert done["type"] == "done"
    assert done["ok"] is True
    assert done["stage"] == "A"
    assert done["weak"] is False
    assert done["escalated"] is False
    assert done["export_id"] is None
    assert done["resume_point"] == out["resume_point"]


# --- registry + queue routing -------------------------------------------------------------------


def test_register_short_creator_handlers_includes_production_run() -> None:
    registry: dict[str, JobHandler] = {}

    handlers.register_short_creator_handlers(registry)

    assert registry["production.run"] is handlers.handle_production_run
    assert registry["short_creator.run"] is handlers.handle_short_creator_run


def test_queue_for_production_run_routes_to_analysis_cpu() -> None:
    assert queue_for("production.run") == QUEUE_ANALYSIS_CPU


# --- sanctioned addition: blank message normalizes to None ---------------------------------


def test_handle_production_run_blank_message_normalizes_to_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A whitespace-only ``message`` must behave like no message at all: ``run_production``
    receives ``message=None``, and the run-log meta line (which only carries the key when a
    message is present) omits it too."""
    db, asset_id = _seed_scene(tmp_path)
    captured: dict[str, Any] = {}

    def fake_run_production(
        db_: Database, config: providers.AgentConfig, **kwargs: Any
    ) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "ok": True,
            "status": "ok",
            "stage": "A",
            "team": "magentic",
            "weak": False,
            "escalated": False,
            "summary": "",
            "session_id": kwargs["session_id"],
            "board": {},
            "export_id": None,
            "resume_point": "done",
        }

    monkeypatch.setattr(
        "laura.short_creator.production_orchestrator.run_production", fake_run_production
    )
    payload = {"asset_id": asset_id, "session_id": "sess3", "task": "t", "message": "   "}

    out = handlers.handle_production_run(_ctx(db, payload))

    assert out["ok"] is True
    assert captured["message"] is None

    logs = sorted(_runs_dir(tmp_path, "sess3").glob("*.ndjson"))
    meta = json.loads(logs[0].read_text(encoding="utf-8").splitlines()[0])
    assert "message" not in meta
