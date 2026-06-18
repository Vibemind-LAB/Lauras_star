"""API tests for asset listing and file serving (Portion 6)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from laura.db import repos
from laura.db.database import Database


def _make_project(client: TestClient) -> str:
    resp = client.post(
        "/projects",
        json={"name": "p", "sequence_rate_num": 30, "sequence_rate_den": 1},
    )
    assert resp.status_code == 201
    return str(resp.json()["id"])


def test_list_project_assets(client: TestClient, db: Database) -> None:
    project_id = _make_project(client)
    assert client.get(f"/projects/{project_id}/assets").json() == []

    repos.create_asset(
        db, project_id=project_id, type="video", display_name="a.mp4", source_path="a.mp4"
    )
    listed = client.get(f"/projects/{project_id}/assets").json()
    assert len(listed) == 1
    assert listed[0]["display_name"] == "a.mp4"


def test_list_assets_unknown_project_404(client: TestClient) -> None:
    assert client.get("/projects/nope/assets").status_code == 404


def test_get_asset_file_serves_and_404s(
    client: TestClient, db: Database, tmp_path: Path
) -> None:
    project_id = _make_project(client)
    asset = repos.create_asset(
        db, project_id=project_id, type="video", display_name="a.mp4", source_path="a.mp4"
    )
    wf = tmp_path / "waveform.json"
    wf.write_text('{"version":1,"length":2,"peaks":[0.1,0.2]}', encoding="utf-8")
    repos.add_asset_file(db, asset_id=asset["id"], kind="waveform", path=str(wf), is_waveform=True)

    ok = client.get(f"/assets/{asset['id']}/files/waveform")
    assert ok.status_code == 200
    assert ok.json()["peaks"] == [0.1, 0.2]

    # kind that has no file -> 404
    assert client.get(f"/assets/{asset['id']}/files/poster").status_code == 404


def test_get_asset_provenance_returns_manifest(
    client: TestClient, db: Database, tmp_path: Path
) -> None:
    project_id = _make_project(client)
    media = tmp_path / "ai.wav"
    media.write_bytes(b"voice")
    asset = repos.create_asset(
        db,
        project_id=project_id,
        type="audio",
        display_name="voice.wav",
        source_path=str(media),
        synthetic=True,
        ai_effect="voiceover",
    )
    manifest = {
        "schema": "laura.ai.provenance.v1",
        "asset_id": asset["id"],
        "project_id": project_id,
        "synthetic": True,
        "ai_effect": "voiceover",
        "media_sha256": "abc",
        "source": {"timeline_id": "tl-1"},
    }
    Path(f"{media}.laura-provenance.json").write_text(json.dumps(manifest), encoding="utf-8")

    resp = client.get(f"/assets/{asset['id']}/provenance")

    assert resp.status_code == 200
    assert resp.json()["asset_id"] == asset["id"]
    assert resp.json()["source"]["timeline_id"] == "tl-1"


def test_import_from_url_queues_fetch(client: TestClient, db: Database) -> None:
    project_id = _make_project(client)
    resp = client.post(
        f"/projects/{project_id}/assets/import",
        json={"source_url": "http://example.invalid/big.mp4"},
    )
    assert resp.status_code == 202
    asset_id = resp.json()["asset_id"]

    asset = repos.get_asset(db, asset_id)
    assert asset is not None
    assert asset["online"] == 0                 # not yet downloaded
    assert asset["display_name"] == "big.mp4"   # derived from the URL

    # Confirm that an ingest.fetch job was actually enqueued for this asset.
    with db.connection() as conn:
        row = conn.execute(
            "SELECT kind, idempotency_key FROM jobs WHERE idempotency_key = ?",
            (f"fetch:{asset_id}",),
        ).fetchone()
    assert row is not None
    assert row["kind"] == "ingest.fetch"


def test_import_from_url_threads_format_and_cookies(
    client: TestClient, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = _make_project(client)
    # Force the single-asset path (no playlist expansion).
    import laura.api.assets as assets_mod

    monkeypatch.setattr(assets_mod, "ytdlp_available", lambda: False)
    resp = client.post(
        f"/projects/{project_id}/assets/import",
        json={
            "source_url": "https://www.youtube.com/watch?v=abc",
            "format": "1080",
            "cookies_from_browser": "chrome",
        },
    )
    assert resp.status_code == 202
    asset_id = resp.json()["asset_id"]
    assert resp.json()["extra_asset_ids"] == []

    # The chosen options ride on the fetch job payload.
    with db.connection() as conn:
        row = conn.execute(
            "SELECT payload_json FROM jobs WHERE idempotency_key = ?",
            (f"fetch:{asset_id}",),
        ).fetchone()
    assert row is not None
    import json

    payload = json.loads(row["payload_json"])
    assert payload["format"] == "1080"
    assert payload["cookies_from_browser"] == "chrome"


def test_import_from_playlist_url_fans_out(
    client: TestClient, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = _make_project(client)
    import laura.api.assets as assets_mod

    entry_urls = [
        "https://www.youtube.com/watch?v=a",
        "https://www.youtube.com/watch?v=b",
        "https://www.youtube.com/watch?v=c",
    ]
    monkeypatch.setattr(assets_mod, "ytdlp_available", lambda: True)
    monkeypatch.setattr(
        assets_mod, "expand_playlist", lambda url, **kw: list(entry_urls)
    )

    resp = client.post(
        f"/projects/{project_id}/assets/import",
        json={"source_url": "https://www.youtube.com/playlist?list=PL", "format": "720"},
    )
    assert resp.status_code == 202
    body = resp.json()
    # One primary asset + the rest in extra_asset_ids — three assets total.
    all_ids = [body["asset_id"], *body["extra_asset_ids"]]
    assert len(all_ids) == 3
    assert len(set(all_ids)) == 3

    # Each asset got its own fetch job carrying its entry URL + the chosen format.
    import json

    for asset_id, url in zip(all_ids, entry_urls, strict=True):
        with db.connection() as conn:
            row = conn.execute(
                "SELECT payload_json FROM jobs WHERE idempotency_key = ?",
                (f"fetch:{asset_id}",),
            ).fetchone()
        assert row is not None
        payload = json.loads(row["payload_json"])
        assert payload["source_url"] == url
        assert payload["format"] == "720"


def test_import_rejects_both_sources(client: TestClient) -> None:
    project_id = _make_project(client)
    resp = client.post(
        f"/projects/{project_id}/assets/import",
        json={"source_path": "/tmp/x.mp4", "source_url": "http://example.invalid/x.mp4"},
    )
    assert resp.status_code == 422


def test_import_rejects_no_source(client: TestClient) -> None:
    project_id = _make_project(client)
    resp = client.post(f"/projects/{project_id}/assets/import", json={})
    assert resp.status_code == 422
