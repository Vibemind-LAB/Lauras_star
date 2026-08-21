"""Render + exports API — TDD for Task E4."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database
from laura.main import create_app


def _client_db(tmp_path: Path) -> tuple[TestClient, Database]:
    settings = Settings(workspace_root=tmp_path, token=None, start_runner=False)
    app = create_app(settings)
    from typing import cast
    return TestClient(app), cast(Database, app.state.db)


def _project(client: TestClient) -> str:
    resp = client.post(
        "/projects", json={"name": "p", "sequence_rate_num": 30, "sequence_rate_den": 1}
    )
    return str(resp.json()["id"])


def test_render_creates_export_and_lists_it(tmp_path: Path) -> None:
    client, db = _client_db(tmp_path)
    pid = _project(client)
    tl = repos.create_timeline(db, project_id=pid, name="cut", kind="rough_cut")
    r = client.post(f"/timelines/{tl['id']}/render", json={"format": "mp4"})
    assert r.status_code == 202
    export_id = r.json()["export_id"]

    rows = client.get(f"/projects/{pid}/exports").json()
    assert any(
        e["id"] == export_id and e["status"] == "rendering" and e["format"] == "mp4"
        for e in rows
    )


def test_render_unknown_timeline_404(tmp_path: Path) -> None:
    client, _ = _client_db(tmp_path)
    assert client.post("/timelines/nope/render", json={"format": "mp4"}).status_code == 404


def test_render_threads_caption_options_into_export_options(tmp_path: Path) -> None:
    """captions/caption_source/caption_preset from the request body land in the export's
    stored options dict, alongside the unchanged burn_captions (Task 4, spec §5)."""
    client, db = _client_db(tmp_path)
    pid = _project(client)
    tl = repos.create_timeline(db, project_id=pid, name="cut", kind="rough_cut")
    r = client.post(
        f"/timelines/{tl['id']}/render",
        json={
            "format": "mp4",
            "captions": True,
            "caption_source": "voiceover",
            "caption_preset": "wide",
            "burn_captions": True,
        },
    )
    assert r.status_code == 202
    export_id = r.json()["export_id"]

    exp = repos.get_export(db, export_id)
    assert exp is not None
    options = exp["options"]
    assert options["captions"] is True
    assert options["caption_source"] == "voiceover"
    assert options["caption_preset"] == "wide"
    assert options["burn_captions"] is True


def test_render_caption_options_default_when_omitted(tmp_path: Path) -> None:
    client, db = _client_db(tmp_path)
    pid = _project(client)
    tl = repos.create_timeline(db, project_id=pid, name="cut", kind="rough_cut")
    r = client.post(f"/timelines/{tl['id']}/render", json={"format": "mp4"})
    assert r.status_code == 202
    export_id = r.json()["export_id"]

    exp = repos.get_export(db, export_id)
    assert exp is not None
    options = exp["options"]
    assert options["captions"] is False
    assert options["caption_source"] == "auto"
    assert options["caption_preset"] == "reels"
