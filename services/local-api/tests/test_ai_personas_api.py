"""The persona endpoints: a persona binds a consent record to reference material.

The table (`ai_personas`, migration 0025), the repos functions and the request/response
models all shipped long ago -- only the two HTTP routes were missing, so nothing could
create or read a persona over the API. These tests pin the gating that makes the
endpoint safe to expose: a persona may only be built on a consent record that exists,
is not revoked, and belongs to the project whose material it points at.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from laura.db import repos
from laura.db.database import Database


def _project(client: TestClient, name: str = "p") -> str:
    response = client.post(
        "/projects",
        json={"name": name, "rate_num": 30, "rate_den": 1, "drop_frame": False},
    )
    assert response.status_code in (200, 201), response.text
    return str(response.json()["id"])


def _consent(client: TestClient, project_id: str, label: str = "Alex") -> str:
    response = client.post(
        f"/projects/{project_id}/consent", json={"subject_label": label}
    )
    assert response.status_code in (200, 201), response.text
    return str(response.json()["id"])


def _persona_body(consent_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"name": "Alex", "consent_id": consent_id}
    body.update(overrides)
    return body


def test_create_and_list_persona(client: TestClient) -> None:
    project_id = _project(client)
    consent_id = _consent(client, project_id)

    created = client.post("/ai/personas", json=_persona_body(consent_id))

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "Alex"
    assert body["consent_id"] == consent_id
    # The project is taken from the consent record, never from the caller.
    assert body["project_id"] == project_id

    listed = client.get(f"/ai/personas?project_id={project_id}")
    assert listed.status_code == 200
    assert [p["id"] for p in listed.json()] == [body["id"]]


def test_persona_needs_an_existing_consent_record(client: TestClient) -> None:
    _project(client)

    response = client.post("/ai/personas", json=_persona_body("does-not-exist"))

    assert response.status_code == 404


def test_persona_refuses_a_revoked_consent(client: TestClient, db: Database) -> None:
    """Consent can be withdrawn. A persona built after that would outlive the permission.

    Revoking has no HTTP route yet, so this goes through the repos layer -- the `db`
    fixture shares its Settings, and therefore its database file, with `client`.
    """
    project_id = _project(client)
    consent_id = _consent(client, project_id)
    assert repos.revoke_consent_record(db, consent_id) is True

    response = client.post("/ai/personas", json=_persona_body(consent_id))

    assert response.status_code == 400


def test_persona_refuses_a_consent_from_another_project(client: TestClient) -> None:
    """The caller may name a project, but it must be the consent's own."""
    owner = _project(client, "owner")
    stranger = _project(client, "stranger")
    consent_id = _consent(client, owner)

    response = client.post(
        "/ai/personas", json=_persona_body(consent_id, project_id=stranger)
    )

    assert response.status_code == 422


def test_persona_refuses_a_reference_asset_from_another_project(
    client: TestClient, db: Database
) -> None:
    """Face/voice material must belong to the project that holds the consent -- otherwise
    a persona could point at footage nobody consented to."""
    owner = _project(client, "owner")
    stranger = _project(client, "stranger")
    consent_id = _consent(client, owner)
    foreign_asset = repos.create_asset(
        db,
        project_id=stranger,
        type="video",
        display_name="other.mp4",
        source_path="/tmp/o.mp4",
    )

    response = client.post(
        "/ai/personas",
        json=_persona_body(
            consent_id, face_reference_asset_id=str(foreign_asset["id"])
        ),
    )

    assert response.status_code == 422


def test_persona_refuses_a_preferred_runtime_for_a_disallowed_effect(
    client: TestClient
) -> None:
    """`preferred_runtimes` may only name effects the persona is allowed to perform."""
    project_id = _project(client)
    consent_id = _consent(client, project_id)
    runtime = client.post(
        "/ai/runtimes",
        json={"kind": "stub", "effect": "voice", "display_name": "Stub Voice"},
    )
    assert runtime.status_code == 201, runtime.text

    response = client.post(
        "/ai/personas",
        json=_persona_body(
            consent_id,
            allowed_effects=["lipsync"],
            preferred_runtimes={"voice": str(runtime.json()["id"])},
        ),
    )

    assert response.status_code == 422
