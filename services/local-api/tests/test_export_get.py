"""GET /exports/{export_id} — single-export read for the chat preview's export lane (Task 8)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database
from laura.main import create_app


def _client_db(tmp_path: Path) -> tuple[TestClient, Database]:
    settings = Settings(workspace_root=tmp_path, token=None, start_runner=False)
    app = create_app(settings)
    from typing import cast
    return TestClient(app), cast(Database, app.state.db)


def _project(client: TestClient) -> str:
    resp = client.post(
        "/projects", json={"name": "p", "sequence_rate_num": 30, "sequence_rate_den": 1}
    )
    return str(resp.json()["id"])


def test_get_export_ready_returns_status_and_path(tmp_path: Path) -> None:
    client, db = _client_db(tmp_path)
    pid = _project(client)
    exp = repos.create_export(db, project_id=pid, timeline_id=None, format="mp4")
    repos.set_export_done(db, exp["id"], path=str(tmp_path / "out.mp4"), size_bytes=1234)

    r = client.get(f"/exports/{exp['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == exp["id"]
    assert body["status"] == "ready"
    assert body["path"] == str(tmp_path / "out.mp4")
    assert body["size_bytes"] == 1234


def test_get_export_unknown_id_404(tmp_path: Path) -> None:
    client, _ = _client_db(tmp_path)
    assert client.get("/exports/nope").status_code == 404
