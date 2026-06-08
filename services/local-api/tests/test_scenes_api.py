"""Scenes API — TDD for Task RC4 (generate / list / split / merge / rename)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app

_TOKEN = "test-token"


def _client(tmp_path: Path) -> tuple[TestClient, SqliteDatabase]:
    settings = Settings(workspace_root=tmp_path / "ws", token=_TOKEN, start_runner=False)
    app = create_app(settings)
    db: SqliteDatabase = app.state.db
    return TestClient(app), db


def _seed_rough_cut(db: SqliteDatabase) -> tuple[str, str, str]:
    """Project + asset + a 3-clip rough_cut (no transcript)."""
    proot = "/tmp/p"
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root=proot
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a",
        source_path="/tmp/a.mp4",
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
    return project["id"], asset["id"], tl["id"]


_H = {"X-Laura-Token": _TOKEN}


def test_generate_without_transcript_one_scene_per_clip(tmp_path: Path) -> None:
    client, db = _client(tmp_path)
    _pid, asset_id, tl_id = _seed_rough_cut(db)
    # analysis run required; config is mandatory
    repos.create_analysis_run(db, asset_id=asset_id, pipeline_version="t", config={})
    r = client.post(
        f"/timelines/{tl_id}/scenes:generate",
        json={"asset_id": asset_id},
        headers=_H,
    )
    assert r.status_code == 200, r.text
    scenes = r.json()
    assert len(scenes) == 3
    assert scenes[0]["seq_in_frame"] == 0 and scenes[0]["seq_out_frame_exclusive"] == 30


def test_merge_then_split_roundtrip(tmp_path: Path) -> None:
    client, db = _client(tmp_path)
    _pid, asset_id, tl_id = _seed_rough_cut(db)
    repos.create_analysis_run(db, asset_id=asset_id, pipeline_version="t", config={})
    client.post(
        f"/timelines/{tl_id}/scenes:generate",
        json={"asset_id": asset_id},
        headers=_H,
    )
    scenes = client.get(f"/timelines/{tl_id}/scenes", headers=_H).json()
    # merge scene 0 with its successor -> 2 scenes
    merged = client.post(
        f"/timelines/{tl_id}/scenes/merge",
        json={"scene_id": scenes[0]["id"]},
        headers=_H,
    ).json()
    assert len(merged) == 2
    assert merged[0]["seq_in_frame"] == 0 and merged[0]["seq_out_frame_exclusive"] == 60
    # split it back at the clip boundary 30
    split = client.post(
        f"/timelines/{tl_id}/scenes/{merged[0]['id']}/split",
        json={"at_seq_frame": 30},
        headers=_H,
    ).json()
    assert [(s["seq_in_frame"], s["seq_out_frame_exclusive"]) for s in split] == [
        (0, 30),
        (30, 60),
        (60, 90),
    ]


def test_split_at_non_boundary_is_422(tmp_path: Path) -> None:
    client, db = _client(tmp_path)
    _pid, asset_id, tl_id = _seed_rough_cut(db)
    repos.create_analysis_run(db, asset_id=asset_id, pipeline_version="t", config={})
    client.post(
        f"/timelines/{tl_id}/scenes:generate",
        json={"asset_id": asset_id},
        headers=_H,
    )
    scenes = client.get(f"/timelines/{tl_id}/scenes", headers=_H).json()
    merged = client.post(
        f"/timelines/{tl_id}/scenes/merge",
        json={"scene_id": scenes[0]["id"]},
        headers=_H,
    ).json()
    r = client.post(
        f"/timelines/{tl_id}/scenes/{merged[0]['id']}/split",
        json={"at_seq_frame": 15},
        headers=_H,
    )
    assert r.status_code == 422


def test_generate_on_empty_timeline_is_422(tmp_path: Path) -> None:
    client, db = _client(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a",
        source_path="/tmp/a.mp4",
    )
    repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="t", config={})
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    r = client.post(
        f"/timelines/{tl['id']}/scenes:generate",
        json={"asset_id": asset["id"]},
        headers=_H,
    )
    assert r.status_code == 422
