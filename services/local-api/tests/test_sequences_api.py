"""Sequences API — TDD for Task SA4 (assemble API: get-or-create, set scenes, flattened)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app

_TOKEN = "test-token"


def _app(tmp_path: Path) -> tuple[TestClient, SqliteDatabase]:
    settings = Settings(workspace_root=tmp_path / "ws", token=_TOKEN, start_runner=False)
    app = create_app(settings)
    db: SqliteDatabase = app.state.db
    return TestClient(app), db


def _seed_two_scenes(db: SqliteDatabase) -> tuple[str, list[str]]:
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a", source_path="/tmp/a.mp4"
    )
    rc = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_timeline_clips(
        db,
        rc["id"],
        [
            {
                "asset_id": asset["id"],
                "src_in_frame": 0,
                "src_out_frame_exclusive": 30,
                "seq_in_frame": 0,
                "seq_out_frame_exclusive": 30,
                "lane": 0,
                "speed_num": 1,
                "speed_den": 1,
            },
            {
                "asset_id": asset["id"],
                "src_in_frame": 30,
                "src_out_frame_exclusive": 60,
                "seq_in_frame": 30,
                "seq_out_frame_exclusive": 60,
                "lane": 0,
                "speed_num": 1,
                "speed_den": 1,
            },
        ],
    )
    repos.replace_scenes(db, project["id"], rc["id"], [(0, 30), (30, 60)])
    return project["id"], [s["id"] for s in repos.list_scenes(db, rc["id"])]


_H = {"X-Laura-Token": _TOKEN}


def test_get_creates_sequence_then_put_orders(tmp_path: Path) -> None:
    client, db = _app(tmp_path)
    pid, scene_ids = _seed_two_scenes(db)
    r = client.get(f"/projects/{pid}/sequence", headers=_H)
    assert r.status_code == 200, r.text
    seq_id = r.json()["timeline_id"]
    r2 = client.put(
        f"/sequences/{seq_id}/scenes",
        json={"scene_ids": list(reversed(scene_ids))},
        headers=_H,
    )
    assert r2.status_code == 200, r2.text
    items = r2.json()["items"]
    assert [it["scene_id"] for it in items] == list(reversed(scene_ids))
    assert [it["order_index"] for it in items] == [0, 1]
    # scenes got materialized -> flattened clips exist
    flat = client.get(f"/sequences/{seq_id}/flattened", headers=_H).json()
    assert len(flat) == 2


def test_put_unknown_scene_422(tmp_path: Path) -> None:
    client, db = _app(tmp_path)
    pid, _ = _seed_two_scenes(db)
    seq_id = client.get(f"/projects/{pid}/sequence", headers=_H).json()["timeline_id"]
    r = client.put(f"/sequences/{seq_id}/scenes", json={"scene_ids": ["nope"]}, headers=_H)
    assert r.status_code == 422
