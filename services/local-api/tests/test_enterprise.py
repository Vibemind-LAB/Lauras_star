"""Enterprise: RBAC via API keys, revocation, audit log, metrics endpoint."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from laura.config import Settings
from laura.main import create_app


@pytest.fixture
def ent_client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(workspace_root=tmp_path, start_runner=False)
    with TestClient(create_app(settings)) as client:
        yield client


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _make_key(client: TestClient, org_id: str, role: str) -> str:
    resp = client.post(f"/admin/orgs/{org_id}/keys", json={"role": role, "name": role})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["key"])


def test_rbac_enforced_per_role(ent_client: TestClient) -> None:
    org = ent_client.post("/admin/orgs", json={"name": "Acme"}).json()
    editor = _make_key(ent_client, org["id"], "editor")
    reviewer = _make_key(ent_client, org["id"], "reviewer")

    body = {"name": "p", "sequence_rate_num": 30, "sequence_rate_den": 1}

    # editor may create projects
    assert ent_client.post("/projects", json=body, headers=_bearer(editor)).status_code == 201
    # reviewer may NOT (lacks project:write)
    denied = ent_client.post("/projects", json=body, headers=_bearer(reviewer))
    assert denied.status_code == 403
    # reviewer may read
    assert ent_client.get("/projects", headers=_bearer(reviewer)).status_code == 200


def test_invalid_role_rejected(ent_client: TestClient) -> None:
    org = ent_client.post("/admin/orgs", json={"name": "Acme"}).json()
    resp = ent_client.post(f"/admin/orgs/{org['id']}/keys", json={"role": "superhero"})
    assert resp.status_code == 422


def test_key_revocation(ent_client: TestClient) -> None:
    org = ent_client.post("/admin/orgs", json={"name": "Acme"}).json()
    created = ent_client.post(f"/admin/orgs/{org['id']}/keys", json={"role": "editor"}).json()
    key, key_id = created["key"], created["id"]

    body = {"name": "p", "sequence_rate_num": 30, "sequence_rate_den": 1}
    assert ent_client.post("/projects", json=body, headers=_bearer(key)).status_code == 201

    assert ent_client.delete(f"/admin/keys/{key_id}").status_code == 204
    # revoked key is rejected
    assert ent_client.post("/projects", json=body, headers=_bearer(key)).status_code == 401


def test_audit_records_actions(ent_client: TestClient) -> None:
    org = ent_client.post("/admin/orgs", json={"name": "Acme"}).json()
    editor = _make_key(ent_client, org["id"], "editor")
    ent_client.post(
        "/projects",
        json={"name": "p", "sequence_rate_num": 30, "sequence_rate_den": 1},
        headers=_bearer(editor),
    )
    events = ent_client.get("/admin/audit").json()
    actions = {e["action"] for e in events}
    assert "org.create" in actions
    assert "key.create" in actions
    assert "project.create" in actions
    # the project.create was performed by an API key principal
    proj_event = next(e for e in events if e["action"] == "project.create")
    assert proj_event["principal_kind"] == "key"


def test_metrics_endpoint(ent_client: TestClient) -> None:
    ent_client.get("/healthz")
    resp = ent_client.get("/metrics")
    assert resp.status_code == 200
    assert "laura_http_requests_total" in resp.text
