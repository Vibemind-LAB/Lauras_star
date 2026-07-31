"""analysis.run always leaves its run in a terminal state, and best-effort stages never kill it.

Both properties were missing and produced the corpses in workspace-livetest: get_index()
sat outside the best-effort try in _run_transcript, so an unreachable Qdrant raised straight
through handle_analysis_run -- which had no try/except, so finish_analysis_run never ran and
the row stayed 'running' forever with its 165 segments attached and diagnostics_json '{}'.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from laura.analysis import handlers
from laura.analysis.types import SegmentResult
from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.jobs.runner import JobContext

FPS = 30


def _db(tmp_path: Path) -> Database:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db: Database = SqliteDatabase(settings.db_path)
    db.migrate()
    return db


def _seed(db: Database, tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    """(project, asset, run_id) — an asset probed with audio, plus a queued analysis run."""
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False,
        workspace_root=str(tmp_path / "ws"),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mp4",
        source_path=str(tmp_path / "a.mp4"),
    )
    repos.update_asset_probe(
        db, str(asset["id"]), type="video", duration_frames=600, rate_num=FPS, rate_den=1,
        audio_sample_rate=16000, start_timecode=None, width=1920, height=1080,
        codec_video="h264", codec_audio="aac", is_vfr=False, sha256=None,
    )
    probed_asset = repos.get_asset(db, str(asset["id"]))
    assert probed_asset is not None
    asset = probed_asset
    run = repos.create_analysis_run(
        db, asset_id=str(asset["id"]), pipeline_version="t", config={}
    )
    return project, asset, str(run["id"])


def _ctx(db: Database, payload: dict[str, Any]) -> JobContext:
    return JobContext(
        job_id="job-1", kind="analysis.run", queue="analysis.scene", payload=payload, db=db
    )


def test_unreachable_index_does_not_fail_the_transcript_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A down Qdrant raises inside get_index() itself (client/collection construction). That
    is best-effort work -- it belongs in the diagnostics, not in the caller's face."""
    db = _db(tmp_path)
    project, asset, run_id = _seed(db, tmp_path)

    def _raise_index() -> None:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(handlers, "asr_available", lambda: True)
    monkeypatch.setattr(
        handlers,
        "transcribe",
        lambda path, model_size=None, language=None: [
            SegmentResult(text="mission talk", start_sec=0.5, end_sec=2.0, confidence=1.0)
        ],
    )
    monkeypatch.setattr(handlers, "get_index", _raise_index)

    result = handlers._run_transcript(
        db, asset, project, run_id,
        {"audio_mono16k": {"path": str(tmp_path / "a.wav")}},
        {"stages": {}, "model": "base", "language": None},
    )

    assert result["status"] == "ok"
    assert result["segments"] == 1
    assert result["embedded"] == 0
    assert "embed failed: RuntimeError" not in result["diarization"]
    assert result["diarization"] == "skipped"
    assert "RuntimeError" in result["embed"]
    # The segments are what matter: they must be committed even though the embed blew up.
    assert [s["text"] for s in repos.get_transcript(db, str(asset["id"]), run_id)] == [
        "mission talk"
    ]


def test_crashing_stage_finalizes_the_run_as_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The jobs table has a reaper; analysis_runs had nothing. A handler that dies must still
    leave a terminal row behind -- otherwise the run says 'running' forever and every reader
    that filters on status silently loses its artifacts."""
    db = _db(tmp_path)
    _project, asset, run_id = _seed(db, tmp_path)

    def _boom(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("scene detector exploded")

    monkeypatch.setattr(handlers, "_run_scene", _boom)

    with pytest.raises(RuntimeError, match="scene detector exploded"):
        handlers.handle_analysis_run(
            _ctx(db, {
                "asset_id": str(asset["id"]),
                "analysis_run_id": run_id,
                "config": {"stages": {"scene": True, "asr": False}},
            })
        )

    run = repos.get_analysis_run(db, run_id)
    assert run is not None
    assert run["status"] == "failed"
    assert run["finished_at"] is not None
    diagnostics = json.loads(run["diagnostics_json"] or "{}")
    assert "RuntimeError: scene detector exploded" in diagnostics["error"]


def test_failed_run_keeps_the_stages_that_did_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever finished before the crash stays in the diagnostics -- that is what makes the
    failure diagnosable instead of an empty '{}'."""
    db = _db(tmp_path)
    _project, asset, run_id = _seed(db, tmp_path)

    monkeypatch.setattr(
        handlers, "_run_scene", lambda *a, **k: {"status": "ok", "count": 3}
    )

    def _boom(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("asr exploded")

    monkeypatch.setattr(handlers, "_run_transcript", _boom)

    with pytest.raises(RuntimeError, match="asr exploded"):
        handlers.handle_analysis_run(
            _ctx(db, {
                "asset_id": str(asset["id"]),
                "analysis_run_id": run_id,
                "config": {"stages": {"scene": True, "asr": True}},
            })
        )

    run = repos.get_analysis_run(db, run_id)
    assert run is not None
    diagnostics = json.loads(run["diagnostics_json"] or "{}")
    assert diagnostics["scene"] == {"status": "ok", "count": 3}
    assert "RuntimeError: asr exploded" in diagnostics["error"]


def test_clean_run_still_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard on the happy path: the wrap must not change a healthy run."""
    db = _db(tmp_path)
    _project, asset, run_id = _seed(db, tmp_path)

    monkeypatch.setattr(
        handlers, "_run_scene", lambda *a, **k: {"status": "ok", "count": 2}
    )

    diagnostics = handlers.handle_analysis_run(
        _ctx(db, {
            "asset_id": str(asset["id"]),
            "analysis_run_id": run_id,
            "config": {"stages": {"scene": True, "asr": False}},
        })
    )

    run = repos.get_analysis_run(db, run_id)
    assert run is not None
    assert run["status"] == "succeeded"
    assert diagnostics["scene"] == {"status": "ok", "count": 2}
    assert "error" not in diagnostics
