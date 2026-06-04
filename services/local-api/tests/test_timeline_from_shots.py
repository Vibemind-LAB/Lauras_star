"""API test: build a rough cut from an asset's detected shots (one clip per scene)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app


def _setup(tmp_path: Path) -> tuple[TestClient, SqliteDatabase, dict, dict]:
    settings = Settings(workspace_root=tmp_path, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    client = TestClient(create_app(settings))
    client.__enter__()
    project = client.post(
        "/projects", json={"name": "p", "sequence_rate_num": 30, "sequence_rate_den": 1}
    ).json()
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mov", source_path="a.mov"
    )
    return client, db, project, asset


def _add_run_with_shots(db: SqliteDatabase, asset_id: str) -> str:
    run = repos.create_analysis_run(
        db, asset_id=asset_id, pipeline_version="1", config={"stages": {}}
    )
    # Source ranges deliberately have a gap (60->100) to prove the sequence is packed
    # contiguously regardless of where the scenes sit in the source.
    repos.insert_shots(
        db, asset_id=asset_id, run_id=run["id"],
        shots=[
            {"src_in_frame": 10, "src_out_frame_exclusive": 60, "method": "test"},
            {"src_in_frame": 100, "src_out_frame_exclusive": 170, "method": "test"},
            {"src_in_frame": 170, "src_out_frame_exclusive": 200, "method": "test"},
        ],
    )
    return run["id"]


def test_from_shots_builds_one_contiguous_clip_per_scene(tmp_path: Path) -> None:
    client, db, project, asset = _setup(tmp_path)
    try:
        _add_run_with_shots(db, asset["id"])
        resp = client.post(
            f"/projects/{project['id']}/timelines/from-shots",
            json={"asset_id": asset["id"]},
        )
        assert resp.status_code == 201, resp.text
        clips = resp.json()["clips"]
        # one clip per shot, source ranges preserved, sequence packed back-to-back
        assert [(c["src_in_frame"], c["src_out_frame_exclusive"]) for c in clips] == [
            (10, 60), (100, 170), (170, 200),
        ]
        assert [(c["seq_in_frame"], c["seq_out_frame_exclusive"]) for c in clips] == [
            (0, 50), (50, 120), (120, 150),
        ]
        assert all(c["speed_num"] == 1 and c["speed_den"] == 1 for c in clips)
        # OTIO regenerated and persisted
        tl = repos.get_timeline(db, resp.json()["id"])
        assert tl is not None and "OTIO_SCHEMA" in tl["otio_json"]
    finally:
        client.__exit__(None, None, None)


def test_from_shots_fills_empty_timeline_but_refuses_non_empty(tmp_path: Path) -> None:
    client, db, project, asset = _setup(tmp_path)
    try:
        _add_run_with_shots(db, asset["id"])
        empty = client.post(
            f"/projects/{project['id']}/timelines", json={"name": "RC", "kind": "rough_cut"}
        ).json()
        # fills the existing empty rough cut (same id, now populated)
        filled = client.post(
            f"/projects/{project['id']}/timelines/from-shots",
            json={"asset_id": asset["id"], "timeline_id": empty["id"]},
        )
        assert filled.status_code == 201, filled.text
        assert filled.json()["id"] == empty["id"]
        assert len(filled.json()["clips"]) == 3
        # refuses to clobber a non-empty timeline
        again = client.post(
            f"/projects/{project['id']}/timelines/from-shots",
            json={"asset_id": asset["id"], "timeline_id": empty["id"]},
        )
        assert again.status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_from_shots_422_when_no_analysis_run(tmp_path: Path) -> None:
    client, _db, project, asset = _setup(tmp_path)
    try:
        resp = client.post(
            f"/projects/{project['id']}/timelines/from-shots",
            json={"asset_id": asset["id"]},
        )
        assert resp.status_code == 422
        assert "no analysis run" in resp.text
    finally:
        client.__exit__(None, None, None)
