from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from laura.db import repos
from laura.db.database import Database


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
    runtime = client.post(
        "/ai/runtimes",
        json={
            "kind": "stub",
            "effect": "voice",
            "display_name": "Stub Voice",
            "license_status": "not_required",
        },
    ).json()

    response = client.post(
        "/ai/personas",
        json={
            "project_id": project["id"],
            "name": "Persona",
            "consent_id": consent["id"],
            "allowed_effects": ["voice", "lipsync"],
            "preferred_runtimes": {"voice": runtime["id"]},
        },
    )
    assert response.status_code == 201
    persona = response.json()
    assert persona["name"] == "Persona"
    assert persona["allowed_effects"] == ["voice", "lipsync"]

    listed = client.get(f"/ai/personas?project_id={project['id']}")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == persona["id"]


def test_create_persona_rejects_unknown_preferred_runtime(client: TestClient) -> None:
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
            "allowed_effects": ["voice"],
            "preferred_runtimes": {"voice": "missing-runtime"},
        },
    )

    assert response.status_code == 404
    assert "preferred runtime not found" in response.text


def test_create_persona_rejects_preferred_runtime_for_disallowed_effect(
    client: TestClient,
) -> None:
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
    runtime = client.post(
        "/ai/runtimes",
        json={
            "kind": "stub",
            "effect": "voice",
            "display_name": "Stub Voice",
            "license_status": "not_required",
        },
    ).json()

    response = client.post(
        "/ai/personas",
        json={
            "project_id": project["id"],
            "name": "Persona",
            "consent_id": consent["id"],
            "allowed_effects": ["lipsync"],
            "preferred_runtimes": {"voice": runtime["id"]},
        },
    )

    assert response.status_code == 422
    assert "preferred runtime effect is not allowed" in response.text


def test_create_persona_rejects_preferred_runtime_with_wrong_effect(
    client: TestClient,
) -> None:
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
    runtime = client.post(
        "/ai/runtimes",
        json={
            "kind": "stub",
            "effect": "lipsync",
            "display_name": "Stub Lipsync",
            "license_status": "not_required",
        },
    ).json()

    response = client.post(
        "/ai/personas",
        json={
            "project_id": project["id"],
            "name": "Persona",
            "consent_id": consent["id"],
            "allowed_effects": ["voice"],
            "preferred_runtimes": {"voice": runtime["id"]},
        },
    )

    assert response.status_code == 422
    assert "preferred runtime effect must be voice" in response.text


def test_create_persona_rejects_disabled_preferred_runtime(client: TestClient) -> None:
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
    runtime = client.post(
        "/ai/runtimes",
        json={
            "kind": "stub",
            "effect": "voice",
            "display_name": "Disabled Voice",
            "enabled": False,
            "license_status": "not_required",
        },
    ).json()

    response = client.post(
        "/ai/personas",
        json={
            "project_id": project["id"],
            "name": "Persona",
            "consent_id": consent["id"],
            "allowed_effects": ["voice"],
            "preferred_runtimes": {"voice": runtime["id"]},
        },
    )

    assert response.status_code == 422
    assert "preferred runtime is disabled" in response.text


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


def test_create_persona_without_project_id_uses_consent_project(
    client: TestClient,
) -> None:
    project = client.post(
        "/projects",
        json={
            "name": "Scoped",
            "sequence_rate_num": 30,
            "sequence_rate_den": 1,
            "drop_frame": False,
        },
    ).json()
    other_project = client.post(
        "/projects",
        json={
            "name": "Other",
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
            "name": "Scoped Persona",
            "consent_id": consent["id"],
        },
    )

    assert response.status_code == 201
    persona = response.json()
    assert persona["project_id"] == project["id"]

    scoped = client.get(f"/ai/personas?project_id={project['id']}")
    assert scoped.status_code == 200
    assert [item["id"] for item in scoped.json()] == [persona["id"]]

    other = client.get(f"/ai/personas?project_id={other_project['id']}")
    assert other.status_code == 200
    assert other.json() == []


@pytest.mark.parametrize(
    ("field_name", "missing_asset_id", "expected_message"),
    [
        ("face_reference_asset_id", "missing-face", "face reference asset not found"),
        ("voice_reference_asset_id", "missing-voice", "voice reference asset not found"),
    ],
)
def test_create_persona_rejects_missing_reference_asset_ids(
    client: TestClient,
    field_name: str,
    missing_asset_id: str,
    expected_message: str,
) -> None:
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
            field_name: missing_asset_id,
        },
    )

    assert response.status_code == 404
    assert expected_message in response.text


@pytest.mark.parametrize(
    ("field_name", "display_name"),
    [
        ("face_reference_asset_id", "face.mov"),
        ("voice_reference_asset_id", "voice.wav"),
    ],
)
def test_create_persona_rejects_reference_asset_from_another_project(
    client: TestClient,
    db: Database,
    field_name: str,
    display_name: str,
) -> None:
    project = client.post(
        "/projects",
        json={
            "name": "P",
            "sequence_rate_num": 30,
            "sequence_rate_den": 1,
            "drop_frame": False,
        },
    ).json()
    other_project = client.post(
        "/projects",
        json={
            "name": "Other",
            "sequence_rate_num": 30,
            "sequence_rate_den": 1,
            "drop_frame": False,
        },
    ).json()
    consent = client.post(
        f"/projects/{project['id']}/consent",
        json={"subject_label": "Persona"},
    ).json()
    asset = repos.create_asset(
        db,
        project_id=other_project["id"],
        type="video",
        display_name=display_name,
        source_path=f"/tmp/{display_name}",
    )

    response = client.post(
        "/ai/personas",
        json={
            "project_id": project["id"],
            "name": "Persona",
            "consent_id": consent["id"],
            field_name: asset["id"],
        },
    )

    assert response.status_code == 422
    assert "belongs to another project" in response.text
