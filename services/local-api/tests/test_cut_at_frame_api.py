"""Task A3: POST /timelines/{id}/cut-at-frame — composite clip + scene split."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app

_TOKEN = "test-token"
_H = {"X-Laura-Token": _TOKEN}


def _make_client(tmp_path: Path) -> tuple[TestClient, SqliteDatabase]:
    settings = Settings(workspace_root=tmp_path / "ws", token=_TOKEN, start_runner=False)
    app = create_app(settings)
    db: SqliteDatabase = app.state.db
    return TestClient(app), db


@pytest.fixture
def client_with_scenes(
    tmp_path: Path,
) -> tuple[TestClient, str, list[dict[str, Any]]]:
    """Build a project + asset + rough_cut with 4 clips and 2 scenes.

    Clip layout (seq == src, lane 0):
        clip 0: [  0.. 20)
        clip 1: [ 20.. 40)
        clip 2: [ 40.. 60)
        clip 3: [ 60.. 90)
    Clip boundaries: {0, 20, 40, 60, 90}.

    Scene layout:
        scene 0: [  0.. 60)   mid = 30 (mid-clip inside clip 1 [20..40))
        scene 1: [ 60.. 90)

    This ensures:
    - The midpoint of scene 0 (frame 30) is NOT a clip boundary → test_cut_splits_clip.
    - clips[1].seq_in_frame = 20 IS a clip boundary but mid-scene-0 → test_skip_clip_split.
    - Frame 0 is a sequence edge → test_edge_rejected.

    Returns ``(client, timeline_id, words_placeholder)`` where words is an empty list
    (words are not needed for the cut-at-frame tests).
    """
    client, db = _make_client(tmp_path)

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

    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_timeline_clips(
        db,
        tl["id"],
        [
            {
                "asset_id": asset["id"],
                "src_in_frame": 0,
                "src_out_frame_exclusive": 20,
                "seq_in_frame": 0,
                "seq_out_frame_exclusive": 20,
                "lane": 0,
                "speed_num": 1,
                "speed_den": 1,
            },
            {
                "asset_id": asset["id"],
                "src_in_frame": 20,
                "src_out_frame_exclusive": 40,
                "seq_in_frame": 20,
                "seq_out_frame_exclusive": 40,
                "lane": 0,
                "speed_num": 1,
                "speed_den": 1,
            },
            {
                "asset_id": asset["id"],
                "src_in_frame": 40,
                "src_out_frame_exclusive": 60,
                "seq_in_frame": 40,
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

    repos.replace_scenes(
        db,
        project["id"],
        tl["id"],
        [(0, 60), (60, 90)],
    )

    return client, tl["id"], []


def test_cut_at_frame_splits_clip_then_scene(
    client_with_scenes: tuple[TestClient, str, list[dict[str, Any]]],
) -> None:
    api, timeline_id, _words = client_with_scenes
    scenes = api.get(f"/timelines/{timeline_id}/scenes", headers=_H).json()
    clips = api.get(f"/timelines/{timeline_id}", headers=_H).json()["clips"]
    # Pick a frame strictly inside the first scene AND strictly inside some clip.
    # Scene 0 = [0..60), mid = 30 which is inside clip 1 [20..40). Not a clip boundary.
    s0 = scenes[0]
    mid = (s0["seq_in_frame"] + s0["seq_out_frame_exclusive"]) // 2
    assert mid == 30, f"fixture assumption: mid={mid}"
    resp = api.post(f"/timelines/{timeline_id}/cut-at-frame", json={"at_seq_frame": mid}, headers=_H)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["clips"]) == len(clips) + 1, "clip was not split"
    assert any(c["seq_in_frame"] == mid for c in body["clips"]), "boundary not at cut frame"
    assert len(body["scenes"]) == len(scenes) + 1, "scene was not split"
    assert any(s["seq_in_frame"] == mid for s in body["scenes"]), "scene boundary not at cut frame"


def test_cut_at_existing_clip_boundary_skips_clip_split(
    client_with_scenes: tuple[TestClient, str, list[dict[str, Any]]],
) -> None:
    api, timeline_id, _words = client_with_scenes
    clips = api.get(f"/timelines/{timeline_id}", headers=_H).json()["clips"]
    # clips[1].seq_in_frame = 20 — already a clip boundary but strictly inside scene 0 [0..60).
    boundary = clips[1]["seq_in_frame"]
    assert boundary == 20, f"fixture assumption: boundary={boundary}"
    scenes_before = api.get(f"/timelines/{timeline_id}/scenes", headers=_H).json()
    resp = api.post(
        f"/timelines/{timeline_id}/cut-at-frame",
        json={"at_seq_frame": boundary},
        headers=_H,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["clips"]) == len(clips), "clip count changed (no split expected)"
    assert any(s["seq_in_frame"] == boundary for s in body["scenes"]), (
        "scene was not split at existing clip boundary"
    )
    # Scene count increased: scene 0 was [0..60), now [0..20) + [20..60).
    assert len(body["scenes"]) == len(scenes_before) + 1


def test_cut_at_frame_at_sequence_edge_is_rejected(
    client_with_scenes: tuple[TestClient, str, list[dict[str, Any]]],
) -> None:
    api, timeline_id, _words = client_with_scenes
    resp = api.post(
        f"/timelines/{timeline_id}/cut-at-frame",
        json={"at_seq_frame": 0},
        headers=_H,
    )
    assert resp.status_code == 422
