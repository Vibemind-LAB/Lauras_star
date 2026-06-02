"""Portion 15.5 — editorial OTIO import with relink + offline placeholders."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app

MEDIA = "C:/media/a.mov"


def _ctx(tmp_path: Path) -> tuple[SqliteDatabase, TestClient]:
    settings = Settings(workspace_root=tmp_path, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    client = TestClient(create_app(settings))
    client.__enter__()
    return db, client


def _project(client: TestClient, name: str = "P") -> dict[str, Any]:
    return dict(client.post(
        "/projects", json={"name": name, "sequence_rate_num": 30, "sequence_rate_den": 1}
    ).json())


def _exported_otio(db: SqliteDatabase, client: TestClient, project: dict[str, Any]) -> str:
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mov", source_path=MEDIA
    )
    tl = client.post(
        f"/projects/{project['id']}/timelines", json={"name": "RC", "kind": "rough_cut"}
    ).json()
    client.post(
        f"/timelines/{tl['id']}/operations",
        json={"op": "append_clip", "asset_id": asset["id"],
              "src_in_frame": 0, "src_out_frame_exclusive": 30},
    )
    export = client.post(f"/timelines/{tl['id']}/exports", json={"format": "otio"})
    assert export.status_code == 201, export.text
    return str(export.json()["content"])


def test_import_relinks_same_project(tmp_path: Path) -> None:
    db, client = _ctx(tmp_path)
    try:
        p = _project(client)
        otio = _exported_otio(db, client, p)
        asset = repos.list_assets(db, p["id"])[0]

        resp = client.post(
            f"/projects/{p['id']}/timelines/import",
            json={"format": "otio", "content": otio, "name": "Imported"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["matched_media"] == 1
        assert body["offline_media"] == 0
        clips = body["timeline"]["clips"]
        assert len(clips) == 1
        assert clips[0]["asset_id"] == asset["id"]       # relinked to the same asset
        assert clips[0]["seq_out_frame_exclusive"] == 30
    finally:
        client.__exit__(None, None, None)


def test_import_creates_offline_placeholder_in_other_project(tmp_path: Path) -> None:
    db, client = _ctx(tmp_path)
    try:
        source = _project(client, "SRC")
        otio = _exported_otio(db, client, source)
        target = _project(client, "DST")

        resp = client.post(
            f"/projects/{target['id']}/timelines/import",
            json={"format": "otio", "content": otio},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["matched_media"] == 0
        assert body["offline_media"] == 1

        assets = client.get(f"/projects/{target['id']}/assets").json()
        placeholder = next(a for a in assets if a["source_path"] == MEDIA)
        assert placeholder["online"] is False
        assert body["timeline"]["clips"][0]["asset_id"] == placeholder["id"]
    finally:
        client.__exit__(None, None, None)


def test_import_preserves_speed(tmp_path: Path) -> None:
    db, client = _ctx(tmp_path)
    try:
        p = _project(client)
        asset = repos.create_asset(
            db, project_id=p["id"], type="video", display_name="a.mov", source_path=MEDIA
        )
        tl = client.post(
            f"/projects/{p['id']}/timelines", json={"name": "RC", "kind": "rough_cut"}
        ).json()
        client.post(
            f"/timelines/{tl['id']}/operations",
            json={"op": "append_clip", "asset_id": asset["id"],
                  "src_in_frame": 0, "src_out_frame_exclusive": 40},
        )
        client.post(
            f"/timelines/{tl['id']}/operations",
            json={"op": "set_speed", "at_seq_frame": 0, "speed_num": 2, "speed_den": 1},
        )
        otio = str(client.post(
            f"/timelines/{tl['id']}/exports", json={"format": "otio"}
        ).json()["content"])

        resp = client.post(
            f"/projects/{p['id']}/timelines/import",
            json={"format": "otio", "content": otio},
        )
        clip = resp.json()["timeline"]["clips"][0]
        assert clip["speed_num"] == 2
        assert clip["seq_out_frame_exclusive"] == 20     # retimed length
        assert clip["src_out_frame_exclusive"] == 40     # true media preserved
    finally:
        client.__exit__(None, None, None)


def test_import_rejects_bad_input(tmp_path: Path) -> None:
    _db, client = _ctx(tmp_path)
    try:
        p = _project(client)
        assert client.post(
            f"/projects/{p['id']}/timelines/import",
            json={"format": "edl", "content": "x"},
        ).status_code == 422
        assert client.post(
            f"/projects/{p['id']}/timelines/import",
            json={"format": "otio", "content": "not valid otio json"},
        ).status_code == 422
    finally:
        client.__exit__(None, None, None)
