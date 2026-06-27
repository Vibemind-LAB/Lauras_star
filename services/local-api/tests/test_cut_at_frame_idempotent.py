"""Tests: cut_at_frame is idempotent when at_seq_frame is already a scene boundary.

Fix 4 — calling cut_at_frame with a frame that's already a scene boundary returns
200 instead of 422.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.main import create_app

_TOKEN = "test-token"


def _setup(tmp_path: Path) -> tuple[TestClient, object, object, object]:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False, token=_TOKEN)
    app = create_app(settings)
    client = TestClient(app)
    db = app.state.db

    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a", source_path="/tmp/a.mp4"
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")

    # Two clips: [0,30) and [30,60) — boundary at frame 30
    repos.replace_timeline_clips(
        db, tl["id"],
        [
            {
                "asset_id": asset["id"],
                "src_in_frame": 0, "src_out_frame_exclusive": 30,
                "seq_in_frame": 0, "seq_out_frame_exclusive": 30,
                "lane": 0, "speed_num": 1, "speed_den": 1,
            },
            {
                "asset_id": asset["id"],
                "src_in_frame": 30, "src_out_frame_exclusive": 60,
                "seq_in_frame": 30, "seq_out_frame_exclusive": 60,
                "lane": 0, "speed_num": 1, "speed_den": 1,
            },
        ],
    )

    # Manually create scenes at [0,30) and [30,60)
    repos.replace_scenes(db, project["id"], tl["id"], [(0, 30), (30, 60)])

    return client, db, project, tl


def test_cut_at_frame_idempotent_on_existing_boundary(tmp_path: Path) -> None:
    """Cutting at frame 30 (already a scene boundary) returns 200, not 422."""
    client, db, project, tl = _setup(tmp_path)

    r = client.post(
        f"/timelines/{tl['id']}/cut-at-frame",
        json={"at_seq_frame": 30},
        headers={"X-Laura-Token": _TOKEN},
    )
    assert r.status_code == 200, r.text

    # Scene count unchanged (no new split)
    scenes = repos.list_scenes(db, tl["id"])
    assert len(scenes) == 2


def test_cut_at_frame_idempotent_twice(tmp_path: Path) -> None:
    """Calling cut_at_frame twice on the same boundary returns 200 both times."""
    client, db, project, tl = _setup(tmp_path)

    for _ in range(2):
        r = client.post(
            f"/timelines/{tl['id']}/cut-at-frame",
            json={"at_seq_frame": 30},
            headers={"X-Laura-Token": _TOKEN},
        )
        assert r.status_code == 200, r.text

    scenes = repos.list_scenes(db, tl["id"])
    assert len(scenes) == 2


def test_cut_at_frame_still_works_for_real_cut(tmp_path: Path) -> None:
    """Cutting at frame 15 (inside a scene) still creates a real split."""
    client, db, project, tl = _setup(tmp_path)

    r = client.post(
        f"/timelines/{tl['id']}/cut-at-frame",
        json={"at_seq_frame": 15},
        headers={"X-Laura-Token": _TOKEN},
    )
    assert r.status_code == 200, r.text

    scenes = repos.list_scenes(db, tl["id"])
    assert len(scenes) == 3
    boundaries = sorted(
        {s["seq_in_frame"] for s in scenes} | {s["seq_out_frame_exclusive"] for s in scenes}
    )
    assert 15 in boundaries
