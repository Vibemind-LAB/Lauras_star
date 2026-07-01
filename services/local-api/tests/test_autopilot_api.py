"""Auto-pilot endpoint (Axis 1, Slice C) — drive an asset toward a target.

``POST /assets/{id}/auto-pilot`` advances the asset as far as possible WITHOUT blocking: a
synchronous step (build_roughcut) runs inline; the next async step (analysis/render) is enqueued
and the call returns (re-invoke after the job completes). Uses the conftest ``client``/``db``
fixtures (authenticated for timeline:edit).
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from laura.db import repos
from laura.db.database import Database


def _seed_edit_ready_asset(db: Database) -> dict[str, Any]:
    """Project + asset + proxy + succeeded analysis + one kept shot → next_action = roughcut."""
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a", source_path="/a.mov"
    )
    repos.add_asset_file(
        db, asset_id=asset["id"], kind="proxy", path="/tmp/proxy.mp4", is_proxy=True
    )
    run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="1", config={})
    repos.start_analysis_run(db, run["id"])
    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})
    repos.insert_shots(
        db,
        asset_id=asset["id"],
        run_id=run["id"],
        shots=[{"src_in_frame": 0, "src_out_frame_exclusive": 100, "method": "test"}],
    )
    return asset


def test_auto_pilot_roughcut_builds_and_stops(client: TestClient, db: Database) -> None:
    asset = _seed_edit_ready_asset(db)
    r = client.post(f"/assets/{asset['id']}/auto-pilot?target=roughcut")
    assert r.status_code == 200, r.text
    body = r.json()
    # analysis already succeeded → the pilot runs build_roughcut (sync), then stops before render
    assert body["status"] == "target_reached"
    assert [s["tool"] for s in body["steps"]] == ["roughcut_from_shots"]
    # the rough cut now exists with clips
    scenes = repos.list_scenes(db, body["final_action"]["args"]["timeline_id"])
    assert len(scenes) >= 1


def test_auto_pilot_unknown_asset_404(client: TestClient) -> None:
    assert client.post("/assets/does-not-exist/auto-pilot").status_code == 404


def test_auto_pilot_invalid_target_422(client: TestClient, db: Database) -> None:
    asset = _seed_edit_ready_asset(db)
    r = client.post(f"/assets/{asset['id']}/auto-pilot?target=bogus")
    assert r.status_code == 422
