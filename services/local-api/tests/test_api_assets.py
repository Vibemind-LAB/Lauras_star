"""API tests for asset listing and file serving (Portion 6)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from laura.db import repos
from laura.db.database import Database


def _make_project(client: TestClient) -> str:
    resp = client.post(
        "/projects",
        json={"name": "p", "sequence_rate_num": 30, "sequence_rate_den": 1},
    )
    assert resp.status_code == 201
    return str(resp.json()["id"])


def test_list_project_assets(client: TestClient, db: Database) -> None:
    project_id = _make_project(client)
    assert client.get(f"/projects/{project_id}/assets").json() == []

    repos.create_asset(
        db, project_id=project_id, type="video", display_name="a.mp4", source_path="a.mp4"
    )
    listed = client.get(f"/projects/{project_id}/assets").json()
    assert len(listed) == 1
    assert listed[0]["display_name"] == "a.mp4"


def test_list_assets_unknown_project_404(client: TestClient) -> None:
    assert client.get("/projects/nope/assets").status_code == 404


def test_get_asset_file_serves_and_404s(
    client: TestClient, db: Database, tmp_path: Path
) -> None:
    project_id = _make_project(client)
    asset = repos.create_asset(
        db, project_id=project_id, type="video", display_name="a.mp4", source_path="a.mp4"
    )
    wf = tmp_path / "waveform.json"
    wf.write_text('{"version":1,"length":2,"peaks":[0.1,0.2]}', encoding="utf-8")
    repos.add_asset_file(db, asset_id=asset["id"], kind="waveform", path=str(wf), is_waveform=True)

    ok = client.get(f"/assets/{asset['id']}/files/waveform")
    assert ok.status_code == 200
    assert ok.json()["peaks"] == [0.1, 0.2]

    # kind that has no file -> 404
    assert client.get(f"/assets/{asset['id']}/files/poster").status_code == 404
