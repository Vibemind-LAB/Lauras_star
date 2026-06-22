"""Reproduction: deleting a project that has real content (asset, analysis run, timeline + clips,
scenes, scene timeline, export, consent) must succeed — not just an empty project.

User report: created projects can't be deleted. Empty-project delete is already covered by
test_gap_closure; this exercises the populated graph that an import + analysis produces."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app


def _ctx(tmp_path: Path) -> tuple[SqliteDatabase, TestClient]:
    settings = Settings(workspace_root=tmp_path, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    client = TestClient(create_app(settings))
    client.__enter__()
    return db, client


def test_delete_project_with_full_content_graph(tmp_path: Path) -> None:
    db, client = _ctx(tmp_path)
    try:
        project = client.post(
            "/projects", json={"name": "P", "sequence_rate_num": 30, "sequence_rate_den": 1}
        ).json()
        pid = project["id"]

        asset = repos.create_asset(
            db, project_id=pid, type="video", display_name="a.mp4", source_path="a.mp4"
        )
        repos.create_analysis_run(
            db, asset_id=asset["id"], pipeline_version="v1", config={"model": "base"}
        )
        rc = repos.create_timeline(db, project_id=pid, name="RC", kind="rough_cut")
        repos.add_timeline_clip(
            db,
            timeline_id=rc["id"],
            asset_id=asset["id"],
            src_in_frame=0,
            src_out_frame_exclusive=30,
            seq_in_frame=0,
            seq_out_frame_exclusive=30,
        )
        repos.replace_scenes(db, pid, rc["id"], [(0, 30)])
        scene = repos.list_scenes(db, rc["id"])[0]
        scene_tl = repos.create_timeline(db, project_id=pid, name="S1", kind="scene")
        repos.add_timeline_clip(
            db,
            timeline_id=scene_tl["id"],
            asset_id=asset["id"],
            src_in_frame=0,
            src_out_frame_exclusive=30,
            seq_in_frame=0,
            seq_out_frame_exclusive=30,
        )
        repos.set_scene_timeline(db, scene["id"], scene_tl["id"])
        repos.create_export(db, project_id=pid, timeline_id=rc["id"], format="mp4")
        repos.create_consent_record(db, project_id=pid, subject_label="Speaker A")

        # The actual operation the user performs.
        resp = client.delete(f"/projects/{pid}")
        assert resp.status_code == 204, resp.text
        assert client.get(f"/projects/{pid}").status_code == 404
        # No orphaned project-scoped rows left behind.
        assert repos.list_scenes(db, rc["id"]) == []
        assert repos.list_consent_records(db, pid) == []
        assert repos.list_exports(db, pid) == []
    finally:
        client.__exit__(None, None, None)
