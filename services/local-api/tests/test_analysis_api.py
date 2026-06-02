"""Analysis orchestration + endpoints, without any ML extras installed."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from laura.analysis.handlers import register_analysis_handlers
from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.ingest.handlers import register_ingest_handlers
from laura.jobs import JobRunner, default_registry
from laura.main import create_app


def _runner(db: Database) -> JobRunner:
    reg = default_registry()
    register_ingest_handlers(reg)
    register_analysis_handlers(reg)
    return JobRunner(db, reg)


def _setup(tmp_path: Path) -> tuple[Settings, Database, TestClient, str]:
    settings = Settings(workspace_root=tmp_path, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    app = create_app(settings)
    client = TestClient(app)
    client.__enter__()
    project = client.post(
        "/projects", json={"name": "p", "sequence_rate_num": 30, "sequence_rate_den": 1}
    ).json()
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mp4", source_path="a.mp4"
    )
    return settings, db, client, asset["id"]


def test_analysis_noop_run_and_endpoints(tmp_path: Path) -> None:
    _settings, db, client, asset_id = _setup(tmp_path)
    try:
        resp = client.post(f"/assets/{asset_id}/analysis", json={"scene": False, "asr": False})
        assert resp.status_code == 202, resp.text
        run_id = resp.json()["analysis_run_id"]

        assert _runner(db).run_once() is True

        latest = client.get(f"/assets/{asset_id}/analysis/latest").json()
        assert latest["status"] == "succeeded"
        assert latest["id"] == run_id

        assert client.get(f"/assets/{asset_id}/shots").json() == []
        assert client.get(f"/assets/{asset_id}/transcript").json() == []
    finally:
        client.__exit__(None, None, None)


def test_analysis_graceful_skip_without_ml(tmp_path: Path) -> None:
    _settings, db, client, asset_id = _setup(tmp_path)
    try:
        resp = client.post(f"/assets/{asset_id}/analysis", json={"scene": True, "asr": True})
        assert resp.status_code == 202

        # Drain (scene may try to open a missing file; the orchestrator must not crash).
        runner = _runner(db)
        while runner.run_once():
            pass

        latest = client.get(f"/assets/{asset_id}/analysis/latest").json()
        assert latest["status"] == "succeeded"
        diag = latest["diagnostics"]
        # No audio extracted -> ASR is always skipped here.
        assert diag["asr"]["status"] == "skipped"
        # Scene stage is recorded regardless of whether the extra is installed.
        assert "scene" in diag
    finally:
        client.__exit__(None, None, None)


def test_analysis_latest_404_when_none(tmp_path: Path) -> None:
    _settings, _db, client, asset_id = _setup(tmp_path)
    try:
        assert client.get(f"/assets/{asset_id}/analysis/latest").status_code == 404
    finally:
        client.__exit__(None, None, None)
