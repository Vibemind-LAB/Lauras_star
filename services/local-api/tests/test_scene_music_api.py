# services/local-api/tests/test_scene_music_api.py
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app

_TOKEN = "test-token"


def _app(tmp_path: Path):  # type: ignore[no-untyped-def]
    app = create_app(Settings(workspace_root=tmp_path / "ws", start_runner=False, token=_TOKEN))
    return TestClient(app), app.state.db


def test_set_then_clear_music(tmp_path: Path) -> None:
    client, db = _app(tmp_path)
    project = repos.create_project(db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p")
    music = repos.create_asset(db, project_id=project["id"], type="audio", display_name="m", source_path="/tmp/m.mp3")
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_scenes(db, project["id"], tl["id"], [(0, 30)])
    sid = repos.list_scenes(db, tl["id"])[0]["id"]
    h = {"X-Laura-Token": _TOKEN}
    r = client.put(f"/scenes/{sid}/music", json={"asset_id": music["id"], "gain_percent": 120}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["music_asset_id"] == music["id"] and r.json()["music_gain_percent"] == 120
    r2 = client.request("DELETE", f"/scenes/{sid}/music", headers=h)
    assert r2.status_code == 200
    assert r2.json()["music_asset_id"] is None


def test_set_music_unknown_asset_404(tmp_path: Path) -> None:
    client, db = _app(tmp_path)
    project = repos.create_project(db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p")
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_scenes(db, project["id"], tl["id"], [(0, 30)])
    sid = repos.list_scenes(db, tl["id"])[0]["id"]
    r = client.put(f"/scenes/{sid}/music", json={"asset_id": "nope", "gain_percent": 100}, headers={"X-Laura-Token": _TOKEN})
    assert r.status_code == 404
