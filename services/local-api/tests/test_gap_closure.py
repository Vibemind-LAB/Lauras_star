"""Wave-A gap closure: search, transcript edit, CRUD delete/rename, multi-tenancy."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


def _project(client: TestClient) -> dict[str, Any]:
    data: dict[str, Any] = client.post(
        "/projects", json={"name": "P", "sequence_rate_num": 30, "sequence_rate_den": 1}
    ).json()
    return data


def _make_key(client: TestClient, org_id: str) -> str:
    resp = client.post(f"/admin/orgs/{org_id}/keys", json={"role": "editor"})
    return str(resp.json()["key"])


def test_project_rename_and_delete(tmp_path: Path) -> None:
    _db, client = _ctx(tmp_path)
    try:
        p = _project(client)
        renamed = client.patch(f"/projects/{p['id']}", json={"name": "P2"})
        assert renamed.status_code == 200 and renamed.json()["name"] == "P2"
        assert client.delete(f"/projects/{p['id']}").status_code == 204
        assert client.get(f"/projects/{p['id']}").status_code == 404
    finally:
        client.__exit__(None, None, None)


def test_asset_delete(tmp_path: Path) -> None:
    db, client = _ctx(tmp_path)
    try:
        p = _project(client)
        asset = repos.create_asset(
            db, project_id=p["id"], type="video", display_name="a.mov", source_path="a.mov"
        )
        assert client.delete(f"/assets/{asset['id']}").status_code == 204
        assert client.get(f"/assets/{asset['id']}").status_code == 404
    finally:
        client.__exit__(None, None, None)


def test_search_and_transcript_patch(tmp_path: Path) -> None:
    db, client = _ctx(tmp_path)
    try:
        p = _project(client)
        asset = repos.create_asset(
            db, project_id=p["id"], type="video", display_name="a.mov", source_path="a.mov"
        )
        run = repos.create_analysis_run(
            db, asset_id=asset["id"], pipeline_version="1", config={}
        )
        seg_id = repos.insert_segment_with_words(
            db, asset_id=asset["id"], run_id=run["id"], speaker_id=None,
            segment={"start_sample": 0, "end_sample": 48000, "start_frame": 0,
                     "end_frame": 30, "text": "Hallo Welt", "confidence": 0.9},
            words=[],
        )
        # search_transcript resolves each asset's transcript run the same way
        # repos.get_latest_transcript_run does: has-segments, then succeeded, then newest
        # (stale-run segments must not double-count after a re-analysis) — mark this run
        # succeeded so it unambiguously wins, matching every other seed helper in this repo.
        repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})
        found = client.post("/search", json={"project_id": p["id"], "query": "hallo"}).json()
        assert len(found) == 1 and found[0]["asset_id"] == asset["id"]
        assert client.post("/search", json={"project_id": p["id"], "query": "zzz"}).json() == []

        patched = client.patch(f"/transcript/segments/{seg_id}", json={"text": "Korrigiert"})
        assert patched.status_code == 200 and patched.json()["text"] == "Korrigiert"
    finally:
        client.__exit__(None, None, None)


def test_fcp7_export_via_api(tmp_path: Path) -> None:
    db, client = _ctx(tmp_path)
    try:
        p = _project(client)
        tl = client.post(
            f"/projects/{p['id']}/timelines", json={"name": "RC", "kind": "rough_cut"}
        ).json()
        asset = repos.create_asset(
            db, project_id=p["id"], type="video", display_name="a.mov", source_path="a.mov"
        )
        repos.add_timeline_clip(
            db, timeline_id=tl["id"], asset_id=asset["id"], src_in_frame=0,
            src_out_frame_exclusive=30, seq_in_frame=0, seq_out_frame_exclusive=30,
        )
        resp = client.post(f"/timelines/{tl['id']}/exports", json={"format": "fcp7xml"})
        assert resp.status_code == 201, resp.text
        assert '<xmeml version="5">' in resp.json()["content"]
    finally:
        client.__exit__(None, None, None)


def test_multitenancy_isolation(tmp_path: Path) -> None:
    _db, client = _ctx(tmp_path)
    try:
        org_a = client.post("/admin/orgs", json={"name": "A"}).json()
        org_b = client.post("/admin/orgs", json={"name": "B"}).json()
        key_a = _make_key(client, org_a["id"])
        key_b = _make_key(client, org_b["id"])
        head_a = {"Authorization": f"Bearer {key_a}"}
        head_b = {"Authorization": f"Bearer {key_b}"}

        proj = client.post(
            "/projects",
            json={"name": "PA", "sequence_rate_num": 30, "sequence_rate_den": 1},
            headers=head_a,
        ).json()

        # org A sees its project; org B does not
        assert any(x["id"] == proj["id"] for x in client.get("/projects", headers=head_a).json())
        assert all(x["id"] != proj["id"] for x in client.get("/projects", headers=head_b).json())
        # org B cannot read A's project, and editor lacks project:delete
        assert client.get(f"/projects/{proj['id']}", headers=head_b).status_code == 404
        assert client.delete(f"/projects/{proj['id']}", headers=head_b).status_code == 403
    finally:
        client.__exit__(None, None, None)
