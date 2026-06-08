"""TDD for Task 4: materialize scene into editable sub-timeline + POST /scenes/{id}/open."""

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


def _seed(db: SqliteDatabase):  # type: ignore[no-untyped-def]
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a", source_path="/tmp/a.mp4"
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_timeline_clips(
        db,
        tl["id"],
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
            {
                "asset_id": asset["id"],
                "src_in_frame": 60,
                "src_out_frame_exclusive": 90,
                "seq_in_frame": 60,
                "seq_out_frame_exclusive": 90,
                "lane": 0,
                "speed_num": 1,
                "speed_den": 1,
            },
        ],
    )
    # two scenes: scene[0] = seq 0..30, scene[1] = seq 30..90
    repos.replace_scenes(db, project["id"], tl["id"], [(0, 30), (30, 90)])
    return project["id"], tl["id"], repos.list_scenes(db, tl["id"])


def test_open_materializes_rebased_to_zero(tmp_path: Path) -> None:
    client, db = _app(tmp_path)
    _pid, _tl, scenes = _seed(db)
    sid = scenes[1]["id"]  # seq 30..90 — spans clips 2 and 3
    r = client.post(f"/scenes/{sid}/open", headers={"X-Laura-Token": _TOKEN})
    assert r.status_code == 200, r.text
    tl = r.json()
    assert tl["kind"] == "scene"
    assert [(c["seq_in_frame"], c["seq_out_frame_exclusive"]) for c in tl["clips"]] == [
        (0, 30),
        (30, 60),
    ]
    # idempotent: re-open must return the same timeline id
    again = client.post(f"/scenes/{sid}/open", headers={"X-Laura-Token": _TOKEN})
    assert again.status_code == 200, again.text
    assert again.json()["id"] == tl["id"]
    # scene row must have been linked
    assert repos.get_scene(db, sid)["scene_timeline_id"] == tl["id"]
