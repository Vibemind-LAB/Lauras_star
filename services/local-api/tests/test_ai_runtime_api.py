from __future__ import annotations

from fastapi.testclient import TestClient


def test_create_and_list_stub_runtime(client: TestClient) -> None:
    response = client.post(
        "/ai/runtimes",
        json={
            "kind": "stub",
            "effect": "voice",
            "display_name": "Stub Voice",
            "license_status": "not_required",
        },
    )
    assert response.status_code == 201
    created = response.json()
    assert created["kind"] == "stub"
    assert created["status"] == {}

    listed = client.get("/ai/runtimes?effect=voice")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == created["id"]


def test_refresh_stub_runtime_returns_ready_status(client: TestClient) -> None:
    created = client.post(
        "/ai/runtimes",
        json={"kind": "stub", "effect": "lipsync", "display_name": "Stub Lipsync"},
    ).json()

    refreshed = client.post(f"/ai/runtimes/{created['id']}/refresh")
    assert refreshed.status_code == 200
    body = refreshed.json()
    assert body["status"]["state"] == "ready"
    assert body["status"]["ready"] is True
    assert body["capabilities"]["effects"] == ["lipsync"]

    events = client.get(f"/ai/runtimes/{created['id']}/events")
    assert events.status_code == 200
    assert events.json()[0]["event_type"] == "health"
