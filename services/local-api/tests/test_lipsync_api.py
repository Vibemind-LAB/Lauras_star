from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from laura.db import repos
from laura.db.database import Database


def _seed_lipsync_api(
    client: TestClient,
    tmp_path: Path,
) -> tuple[Database, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], str]:
    db: Database = cast(Any, client.app).state.db
    project = repos.create_project(
        db,
        name="p",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(tmp_path / "project"),
    )
    timeline = repos.create_timeline(db, project_id=project["id"], name="sequence", kind="sequence")
    video = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="video.mp4",
        source_path=str(tmp_path / "video.mp4"),
    )
    audio = repos.create_asset(
        db,
        project_id=project["id"],
        type="audio",
        display_name="voice.wav",
        source_path=str(tmp_path / "voice.wav"),
    )
    consent = repos.create_consent_record(
        db,
        project_id=project["id"],
        subject_label="Person A",
        confirmed_by="test",
    )
    return db, project, timeline, video, audio, consent["id"]


def test_lipsync_api_enqueues_consent_and_license_gated_job(
    client: TestClient,
    tmp_path: Path,
) -> None:
    db, _, timeline, _, audio, consent_id = _seed_lipsync_api(client, tmp_path)

    response = client.post(
        f"/timelines/{timeline['id']}/lipsync",
        json={
            "seq_in_frame": 5,
            "seq_out_frame_exclusive": 35,
            "audio_asset_id": audio["id"],
            "consent_id": consent_id,
            "license_accepted": True,
            "backend": "stub",
            "quality_threshold": 0.7,
        },
    )

    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    job = repos.get_job(db, job_id)
    assert job is not None
    assert job["kind"] == "ai.lipsync"
    payload = json.loads(job["payload_json"])
    assert payload == {
        "timeline_id": timeline["id"],
        "seq_in_frame": 5,
        "seq_out_frame_exclusive": 35,
        "audio_asset_id": audio["id"],
        "consent_id": consent_id,
        "license_accepted": True,
        "backend": "stub",
        "quality_threshold": 0.7,
    }


def test_lipsync_api_requires_license_confirmation(client: TestClient, tmp_path: Path) -> None:
    _, _, timeline, _, audio, consent_id = _seed_lipsync_api(client, tmp_path)

    response = client.post(
        f"/timelines/{timeline['id']}/lipsync",
        json={
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 30,
            "audio_asset_id": audio["id"],
            "consent_id": consent_id,
            "license_accepted": False,
        },
    )

    assert response.status_code == 422
    assert "license_accepted" in response.text


def test_lipsync_api_rejects_revoked_consent(client: TestClient, tmp_path: Path) -> None:
    db, _, timeline, _, audio, consent_id = _seed_lipsync_api(client, tmp_path)
    assert repos.revoke_consent_record(db, consent_id) is True

    response = client.post(
        f"/timelines/{timeline['id']}/lipsync",
        json={
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 30,
            "audio_asset_id": audio["id"],
            "consent_id": consent_id,
            "license_accepted": True,
        },
    )

    assert response.status_code == 400
    assert "revoked" in response.text


def test_lipsync_api_rejects_non_audio_asset(client: TestClient, tmp_path: Path) -> None:
    _, _, timeline, video, _, consent_id = _seed_lipsync_api(client, tmp_path)

    response = client.post(
        f"/timelines/{timeline['id']}/lipsync",
        json={
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 30,
            "audio_asset_id": video["id"],
            "consent_id": consent_id,
            "license_accepted": True,
        },
    )

    assert response.status_code == 422
    assert "audio" in response.text
