from __future__ import annotations

import pytest
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


def test_start_and_stop_stub_runtime_return_stateful_status(client: TestClient) -> None:
    created = client.post(
        "/ai/runtimes",
        json={"kind": "stub", "effect": "voice", "display_name": "Stub Voice"},
    ).json()

    started = client.post(f"/ai/runtimes/{created['id']}/start")
    assert started.status_code == 200
    assert started.json()["status"]["state"] == "ready"
    assert started.json()["status"]["ready"] is True

    stopped = client.post(f"/ai/runtimes/{created['id']}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["status"]["state"] == "stopped"
    assert stopped.json()["status"]["ready"] is False


@pytest.mark.parametrize(
    "path",
    [
        "/ai/runtimes/missing-runtime/refresh",
        "/ai/runtimes/missing-runtime/start",
        "/ai/runtimes/missing-runtime/stop",
        "/ai/runtimes/missing-runtime/events",
    ],
)
def test_runtime_endpoints_return_404_for_missing_runtime(
    client: TestClient, path: str
) -> None:
    if path.endswith(("/refresh", "/start", "/stop")):
        response = client.post(path)
    else:
        response = client.get(path)
    assert response.status_code == 404
