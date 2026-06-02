"""API tests for health, project CRUD, framerate validation, and token auth."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.main import create_app


def test_healthz(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["schema_version"] >= 1
    assert body["pipeline_version"]


def test_create_get_list_project(client: TestClient) -> None:
    resp = client.post(
        "/projects",
        json={"name": "Doku A", "sequence_rate_num": 30000, "sequence_rate_den": 1001,
              "drop_frame": True},
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["id"]
    assert created["drop_frame"] is True
    assert created["workspace_root"].endswith(f"project-{created['id']}")

    got = client.get(f"/projects/{created['id']}")
    assert got.status_code == 200
    assert got.json() == created

    listed = client.get("/projects")
    assert listed.status_code == 200
    assert any(p["id"] == created["id"] for p in listed.json())


def test_create_project_rejects_invalid_dropframe(client: TestClient) -> None:
    # drop-frame is invalid for 24p — the time core rejects it -> 422.
    resp = client.post(
        "/projects",
        json={"name": "bad", "sequence_rate_num": 24, "sequence_rate_den": 1, "drop_frame": True},
    )
    assert resp.status_code == 422


def test_get_missing_project_404(client: TestClient) -> None:
    assert client.get("/projects/does-not-exist").status_code == 404


def test_token_auth(tmp_path: Path) -> None:
    settings = Settings(workspace_root=tmp_path, token="s3cret", start_runner=False)
    app = create_app(settings)
    with TestClient(app) as client:
        # health needs no token
        assert client.get("/healthz").status_code == 200
        # projects requires the token
        assert client.get("/projects").status_code == 401
        ok = client.get("/projects", headers={"X-Laura-Token": "s3cret"})
        assert ok.status_code == 200
