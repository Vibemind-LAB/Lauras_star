"""TDD for RV1: per-asset rough-cut endpoint + project-wide scene list.

Covered:
  GET /projects/{project_id}/assets/{asset_id}/rough-cut
    - idempotent: two calls return the same timeline id
    - created timeline has kind == "rough_cut"
    - two different assets get two distinct rough cuts
    - if a *newer* rough_cut with that created_from is added, the endpoint returns it
    - 404 on unknown project

  GET /projects/{project_id}/scenes
    - one set of scenes per source asset (the newest rough cut that has scenes)
    - stale scenes from older bias-rebuild timelines are dropped
    - an empty newer rough cut does not hide the older timeline's scenes
    - each scene is enriched with asset_id (its source video)
    - 404 on unknown project
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app

_TOKEN = "test-token"
_H = {"X-Laura-Token": _TOKEN}


def _client(tmp_path: Path) -> tuple[TestClient, SqliteDatabase]:
    settings = Settings(workspace_root=tmp_path / "ws", token=_TOKEN, start_runner=False)
    app = create_app(settings)
    db: SqliteDatabase = app.state.db
    return TestClient(app), db


def _seed(db: SqliteDatabase) -> tuple[str, str, str]:
    """Return (project_id, asset_id_a, asset_id_b)."""
    project = repos.create_project(
        db,
        name="proj",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root="/tmp/proj",
    )
    asset_a = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a",
        source_path="/tmp/a.mp4",
    )
    asset_b = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="b",
        source_path="/tmp/b.mp4",
    )
    return project["id"], asset_a["id"], asset_b["id"]


# ---------------------------------------------------------------------------
# rough-cut endpoint
# ---------------------------------------------------------------------------


def test_rough_cut_is_idempotent(tmp_path: Path) -> None:
    client, db = _client(tmp_path)
    pid, aid, _ = _seed(db)

    r1 = client.get(f"/projects/{pid}/assets/{aid}/rough-cut", headers=_H)
    assert r1.status_code == 200, r1.text
    r2 = client.get(f"/projects/{pid}/assets/{aid}/rough-cut", headers=_H)
    assert r2.status_code == 200, r2.text

    assert r1.json()["id"] == r2.json()["id"]


def test_rough_cut_kind_is_rough_cut(tmp_path: Path) -> None:
    client, db = _client(tmp_path)
    pid, aid, _ = _seed(db)

    r = client.get(f"/projects/{pid}/assets/{aid}/rough-cut", headers=_H)
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "rough_cut"


def test_different_assets_get_different_rough_cuts(tmp_path: Path) -> None:
    client, db = _client(tmp_path)
    pid, aid_a, aid_b = _seed(db)

    ra = client.get(f"/projects/{pid}/assets/{aid_a}/rough-cut", headers=_H)
    rb = client.get(f"/projects/{pid}/assets/{aid_b}/rough-cut", headers=_H)
    assert ra.status_code == 200, ra.text
    assert rb.status_code == 200, rb.text

    assert ra.json()["id"] != rb.json()["id"]


def test_newer_rough_cut_is_returned(tmp_path: Path) -> None:
    """When a newer rough_cut with created_from=asset_id is inserted later,
    the endpoint must return that newer timeline (ORDER BY created_at DESC)."""
    client, db = _client(tmp_path)
    pid, aid, _ = _seed(db)

    # first call materialises the initial rough cut
    r1 = client.get(f"/projects/{pid}/assets/{aid}/rough-cut", headers=_H)
    assert r1.status_code == 200
    old_id = r1.json()["id"]

    # simulate the UI creating a fresh rough cut for the same asset
    newer = repos.create_timeline(
        db,
        project_id=pid,
        name="Rough Cut v2",
        kind="rough_cut",
        created_from=aid,
    )

    r2 = client.get(f"/projects/{pid}/assets/{aid}/rough-cut", headers=_H)
    assert r2.status_code == 200
    assert r2.json()["id"] == newer["id"]
    assert r2.json()["id"] != old_id


def test_rough_cut_404_on_unknown_project(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    r = client.get("/projects/no-such-project/assets/no-such-asset/rough-cut", headers=_H)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# project scenes endpoint
# ---------------------------------------------------------------------------


def test_list_project_scenes_aggregates_all_timelines(tmp_path: Path) -> None:
    client, db = _client(tmp_path)
    pid, aid_a, aid_b = _seed(db)

    # two rough-cut timelines, each with its own set of scenes
    tl_a = repos.create_timeline(
        db, project_id=pid, name="RC A", kind="rough_cut", created_from=aid_a
    )
    tl_b = repos.create_timeline(
        db, project_id=pid, name="RC B", kind="rough_cut", created_from=aid_b
    )

    repos.replace_scenes(db, pid, tl_a["id"], [(0, 30), (30, 60)])
    repos.replace_scenes(db, pid, tl_b["id"], [(0, 90)])

    r = client.get(f"/projects/{pid}/scenes", headers=_H)
    assert r.status_code == 200, r.text
    scenes = r.json()

    # 2 from tl_a + 1 from tl_b = 3 total
    assert len(scenes) == 3

    tl_ids = {s["source_timeline_id"] for s in scenes}
    assert tl_ids == {tl_a["id"], tl_b["id"]}


def test_list_project_scenes_404_on_unknown_project(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    r = client.get("/projects/no-such-project/scenes", headers=_H)
    assert r.status_code == 404


def test_list_project_scenes_dedups_to_newest_rough_cut_per_asset(tmp_path: Path) -> None:
    """A bias rebuild creates a *new* rough_cut for the same asset; the bin must
    show only the newest timeline's scenes, not the stale older copies."""
    client, db = _client(tmp_path)
    pid, aid, _ = _seed(db)

    old = repos.create_timeline(
        db, project_id=pid, name="RC old", kind="rough_cut", created_from=aid
    )
    repos.replace_scenes(db, pid, old["id"], [(0, 30), (30, 60)])
    new = repos.create_timeline(
        db, project_id=pid, name="RC new", kind="rough_cut", created_from=aid
    )
    repos.replace_scenes(db, pid, new["id"], [(0, 45)])

    scenes = client.get(f"/projects/{pid}/scenes", headers=_H).json()

    assert {s["source_timeline_id"] for s in scenes} == {new["id"]}
    assert len(scenes) == 1
    assert all(s["asset_id"] == aid for s in scenes)


def test_list_project_scenes_keeps_older_when_newest_has_no_scenes(tmp_path: Path) -> None:
    """A freshly rebuilt rough cut that has no scenes yet must not hide the
    older timeline's scenes (otherwise the bin would go empty after a rebuild)."""
    client, db = _client(tmp_path)
    pid, aid, _ = _seed(db)

    old = repos.create_timeline(
        db, project_id=pid, name="RC old", kind="rough_cut", created_from=aid
    )
    repos.replace_scenes(db, pid, old["id"], [(0, 30)])
    repos.create_timeline(
        db, project_id=pid, name="RC new empty", kind="rough_cut", created_from=aid
    )

    scenes = client.get(f"/projects/{pid}/scenes", headers=_H).json()

    assert {s["source_timeline_id"] for s in scenes} == {old["id"]}
    assert len(scenes) == 1
