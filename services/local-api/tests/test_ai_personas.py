from __future__ import annotations

from fastapi.testclient import TestClient


def test_create_persona_requires_active_consent(client: TestClient) -> None:
    project = client.post(
        "/projects",
        json={
            "name": "P",
            "sequence_rate_num": 30,
            "sequence_rate_den": 1,
            "drop_frame": False,
        },
    ).json()
    consent = client.post(
        f"/projects/{project['id']}/consent",
        json={"subject_label": "Persona"},
    ).json()

    response = client.post(
        "/ai/personas",
        json={
            "project_id": project["id"],
            "name": "Persona",
            "consent_id": consent["id"],
            "allowed_effects": ["voice", "lipsync"],
            "preferred_runtimes": {"voice": "stub-voice"},
        },
    )
    assert response.status_code == 201
    persona = response.json()
    assert persona["name"] == "Persona"
    assert persona["allowed_effects"] == ["voice", "lipsync"]

    listed = client.get(f"/ai/personas?project_id={project['id']}")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == persona["id"]


def test_create_persona_rejects_revoked_consent(client: TestClient) -> None:
    project = client.post(
        "/projects",
        json={
            "name": "P",
            "sequence_rate_num": 30,
            "sequence_rate_den": 1,
            "drop_frame": False,
        },
    ).json()
    consent = client.post(
        f"/projects/{project['id']}/consent",
        json={"subject_label": "Persona"},
    ).json()
    client.post(f"/projects/{project['id']}/consent/{consent['id']}/revoke")

    response = client.post(
        "/ai/personas",
        json={"project_id": project["id"], "name": "Persona", "consent_id": consent["id"]},
    )
    assert response.status_code == 400
    assert "revoked" in response.text
