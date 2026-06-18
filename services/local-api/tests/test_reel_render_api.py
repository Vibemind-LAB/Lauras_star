"""API tests for POST /timelines/{id}/render-reel (Task R0.5)."""

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
    return TestClient(app), app.state.db


def _project(client: TestClient) -> str:
    resp = client.post(
        "/projects", json={"name": "p", "sequence_rate_num": 30, "sequence_rate_den": 1}
    )
    return str(resp.json()["id"])


def test_render_reel_creates_export_with_options(tmp_path: Path) -> None:
    client, db = _client_db(tmp_path)
    pid = _project(client)
    tl = repos.create_timeline(db, project_id=pid, name="cut", kind="rough_cut")

    r = client.post(
        f"/timelines/{tl['id']}/render-reel",
        json={"hook_text": "H", "disclosure_text": "KI"},
    )
    assert r.status_code == 202
    body = r.json()
    assert "export_id" in body
    assert "job_id" in body

    export_id = body["export_id"]
    export = repos.get_export(db, export_id)
    assert export is not None
    assert export["options"] == {
        "vertical": True,
        "hook_text": "H",
        "disclosure_text": "KI",
        "captions": False,
        "caption_preset": "reels",
        "caption_mode": "karaoke",
        "caption_position": "bottom",
        "caption_fontsize": 72,
        "caption_safe_margin": 250,
        "max_duration_seconds": None,
    }


def test_render_reel_captions_stored_in_options(tmp_path: Path) -> None:
    """POST render-reel with captions=true stores options.captions=True in the export, and a
    provided max_duration_seconds (the reel length cap) is stored too."""
    client, db = _client_db(tmp_path)
    pid = _project(client)
    tl = repos.create_timeline(db, project_id=pid, name="cut", kind="rough_cut")

    r = client.post(
        f"/timelines/{tl['id']}/render-reel",
        json={"captions": True, "hook_text": "H", "max_duration_seconds": 60},
    )
    assert r.status_code == 202

    export_id = r.json()["export_id"]
    export = repos.get_export(db, export_id)
    assert export is not None
    assert export["options"]["captions"] is True
    assert export["options"]["max_duration_seconds"] == 60


def test_render_reel_caption_direction_stored_in_options(tmp_path: Path) -> None:
    client, db = _client_db(tmp_path)
    pid = _project(client)
    tl = repos.create_timeline(db, project_id=pid, name="cut", kind="rough_cut")

    r = client.post(
        f"/timelines/{tl['id']}/render-reel",
        json={
            "captions": True,
            "caption_preset": "reels",
            "caption_mode": "normal",
            "caption_position": "top",
            "caption_fontsize": 84,
            "caption_safe_margin": 180,
        },
    )
    assert r.status_code == 202

    export = repos.get_export(db, r.json()["export_id"])
    assert export is not None
    assert export["options"] == {
        "vertical": True,
        "hook_text": None,
        "disclosure_text": "KI · synthetisch",
        "captions": True,
        "caption_preset": "reels",
        "caption_mode": "normal",
        "caption_position": "top",
        "caption_fontsize": 84,
        "caption_safe_margin": 180,
        "max_duration_seconds": None,
    }


def test_render_reel_unknown_timeline_404(tmp_path: Path) -> None:
    client, _ = _client_db(tmp_path)
    r = client.post("/timelines/nope/render-reel", json={})
    assert r.status_code == 404
