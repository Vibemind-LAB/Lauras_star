"""API tests for consent management + POST /timelines/{id}/reenact."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.main import create_app


def _client_db(tmp_path: Path) -> tuple[TestClient, object]:
    settings = Settings(workspace_root=tmp_path, token=None, start_runner=False)
    app = create_app(settings)
    return TestClient(app), app.state.db


def _project(client: TestClient) -> str:
    resp = client.post(
        "/projects", json={"name": "p", "sequence_rate_num": 30, "sequence_rate_den": 1}
    )
    return str(resp.json()["id"])


def _setup(tmp_path: Path) -> tuple[TestClient, object, str, str, str]:
    """Return (client, db, project_id, timeline_id, portrait_asset_id)."""
    client, db = _client_db(tmp_path)
    pid = _project(client)
    tl = repos.create_timeline(db, project_id=pid, name="cut", kind="rough_cut")
    portrait = repos.create_asset(
        db,
        project_id=pid,
        type="video",
        display_name="portrait.mp4",
        source_path="/fake/portrait.mp4",
    )
    return client, db, pid, tl["id"], portrait["id"]


# ---------------------------------------------------------------------------
# Consent CRUD
# ---------------------------------------------------------------------------


def test_create_consent_201(tmp_path: Path) -> None:
    """POST /projects/{id}/consent with subject_label returns 201 with an id."""
    client, db, pid, _tl_id, _portrait_id = _setup(tmp_path)

    r = client.post(f"/projects/{pid}/consent", json={"subject_label": "Anna"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert "id" in body
    assert body["subject_label"] == "Anna"
    assert body["project_id"] == pid
    assert body["revoked_at"] is None

    # record is fetchable via repos
    rec = repos.get_consent_record(db, body["id"])
    assert rec is not None
    assert rec["subject_label"] == "Anna"


def test_list_consent_200(tmp_path: Path) -> None:
    """GET /projects/{id}/consent returns list of records."""
    client, _db, pid, _tl_id, _portrait_id = _setup(tmp_path)

    client.post(f"/projects/{pid}/consent", json={"subject_label": "A"})
    client.post(f"/projects/{pid}/consent", json={"subject_label": "B"})

    r = client.get(f"/projects/{pid}/consent")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 2
    labels = {i["subject_label"] for i in items}
    assert labels == {"A", "B"}


def test_revoke_consent_200(tmp_path: Path) -> None:
    """POST …/revoke flips revoked_at and returns updated record."""
    client, db, pid, _tl_id, _portrait_id = _setup(tmp_path)

    create_r = client.post(f"/projects/{pid}/consent", json={"subject_label": "Bob"})
    cid = create_r.json()["id"]

    revoke_r = client.post(f"/projects/{pid}/consent/{cid}/revoke")
    assert revoke_r.status_code == 200, revoke_r.text
    body = revoke_r.json()
    assert body["revoked_at"] is not None

    # repos confirms it
    rec = repos.get_consent_record(db, cid)
    assert rec is not None
    assert rec["revoked_at"] is not None


def test_revoke_consent_404_unknown(tmp_path: Path) -> None:
    client, _db, pid, _tl_id, _portrait_id = _setup(tmp_path)
    r = client.post(f"/projects/{pid}/consent/nonexistent/revoke")
    assert r.status_code == 404


def test_create_consent_404_unknown_project(tmp_path: Path) -> None:
    client, _db, _pid, _tl_id, _portrait_id = _setup(tmp_path)
    r = client.post("/projects/bad-project/consent", json={"subject_label": "X"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Reenact job enqueue
# ---------------------------------------------------------------------------


def test_reenact_missing_consent_id_422(tmp_path: Path) -> None:
    """POST /timelines/{id}/reenact without consent_id → 422 (pydantic required field)."""
    client, _db, _pid, tl_id, portrait_id = _setup(tmp_path)

    r = client.post(
        f"/timelines/{tl_id}/reenact",
        json={
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 30,
            "portrait_asset_id": portrait_id,
            # consent_id intentionally omitted
        },
    )
    assert r.status_code == 422


def test_reenact_valid_202(tmp_path: Path) -> None:
    """POST with valid consent + portrait + range → 202 with job_id."""
    client, db, pid, tl_id, portrait_id = _setup(tmp_path)

    consent_r = client.post(f"/projects/{pid}/consent", json={"subject_label": "Eve"})
    cid = consent_r.json()["id"]

    r = client.post(
        f"/timelines/{tl_id}/reenact",
        json={
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 30,
            "portrait_asset_id": portrait_id,
            "consent_id": cid,
        },
    )
    assert r.status_code == 202, r.text
    assert "job_id" in r.json()


def test_reenact_revoked_consent_400(tmp_path: Path) -> None:
    """POST with a revoked consent → 400."""
    client, db, pid, tl_id, portrait_id = _setup(tmp_path)

    consent_r = client.post(f"/projects/{pid}/consent", json={"subject_label": "Zara"})
    cid = consent_r.json()["id"]
    repos.revoke_consent_record(db, cid)

    r = client.post(
        f"/timelines/{tl_id}/reenact",
        json={
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 30,
            "portrait_asset_id": portrait_id,
            "consent_id": cid,
        },
    )
    assert r.status_code == 400


def test_reenact_unknown_timeline_404(tmp_path: Path) -> None:
    """404 when the timeline does not exist."""
    client, db, pid, _tl_id, portrait_id = _setup(tmp_path)

    consent_r = client.post(f"/projects/{pid}/consent", json={"subject_label": "X"})
    cid = consent_r.json()["id"]

    r = client.post(
        "/timelines/nonexistent-tl/reenact",
        json={
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 30,
            "portrait_asset_id": portrait_id,
            "consent_id": cid,
        },
    )
    assert r.status_code == 404


def test_reenact_invalid_range_400(tmp_path: Path) -> None:
    """400 when seq_out_frame_exclusive <= seq_in_frame."""
    client, db, pid, tl_id, portrait_id = _setup(tmp_path)

    consent_r = client.post(f"/projects/{pid}/consent", json={"subject_label": "Y"})
    cid = consent_r.json()["id"]

    # equal
    r = client.post(
        f"/timelines/{tl_id}/reenact",
        json={
            "seq_in_frame": 10,
            "seq_out_frame_exclusive": 10,
            "portrait_asset_id": portrait_id,
            "consent_id": cid,
        },
    )
    assert r.status_code == 400

    # inverted
    r2 = client.post(
        f"/timelines/{tl_id}/reenact",
        json={
            "seq_in_frame": 20,
            "seq_out_frame_exclusive": 5,
            "portrait_asset_id": portrait_id,
            "consent_id": cid,
        },
    )
    assert r2.status_code == 400
