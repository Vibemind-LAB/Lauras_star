"""TDD tests for P0a: AI jobs survive a transient failure and deduplicate on retry.

Scope (brief task-1):
  1. ai.voiceover / ai.lipsync / ai.reenact enqueue sites: max_attempts 1 → 2
  2. Each enqueue carries a payload-derived idempotency_key so an identical
     re-enqueue produces the same job id (dedup) rather than a duplicate.

Test plan:
  A. test_voiceover_retry_on_transient_failure  — one transient failure on attempt 1,
     runner reqeues, attempt 2 succeeds.
  B. test_voiceover_idempotency_same_payload     — enqueuing the same payload twice
     returns the same job id (dedup).
  C. test_voiceover_idempotency_different_payload — different payload → different job.
  D. test_lipsync_idempotency_dedup              — same salient lipsync inputs → same job.
  E. test_reenact_max_attempts_2                 — reenact job has max_attempts=2 in DB.
  F. test_reenact_idempotency_dedup              — same reenact inputs → same job.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database
from laura.jobs import JobRunner, default_registry, enqueue
from laura.main import create_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_client_db(tmp_path: Path) -> tuple[TestClient, Database]:
    settings = Settings(workspace_root=tmp_path, token=None, start_runner=False)
    app = create_app(settings)
    return TestClient(app), cast(Any, app).state.db


def _seed_voiceover_setup(
    db: Database, workspace: Path
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Return (project, timeline, segment_id)."""
    project = repos.create_project(
        db,
        name="retry-test",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(workspace),
    )
    timeline = repos.create_timeline(
        db, project_id=project["id"], name="seq", kind="sequence"
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="source.mp4",
        source_path=str(workspace / "source.mp4"),
    )
    run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="test", config={}
    )
    segment_id = repos.insert_segment_with_words(
        db,
        asset_id=asset["id"],
        run_id=run["id"],
        speaker_id=None,
        segment={
            "start_sample": 0,
            "end_sample": 48_000,
            "start_frame": 0,
            "end_frame": 30,
            "text": "Test",
            "confidence": 1.0,
        },
        words=[],
    )
    return project, timeline, segment_id


# ---------------------------------------------------------------------------
# A. Retry test: transient failure on attempt 1, success on attempt 2
# ---------------------------------------------------------------------------


def test_voiceover_retry_on_transient_failure(
    tmp_path: Path,
) -> None:
    """An ai.voiceover job with max_attempts=2 survives one transient failure.

    We register a stub handler that raises on attempt 1 and succeeds on attempt 2.
    The runner must requeue after the first failure and succeed on the second run.
    """
    settings = Settings(workspace_root=tmp_path, token=None, start_runner=False)
    app = create_app(settings)
    db: Database = cast(Any, app).state.db

    call_counts: dict[str, int] = {"n": 0}

    def _flaky_voiceover(ctx: Any) -> dict[str, Any]:
        call_counts["n"] += 1
        if call_counts["n"] == 1:
            raise RuntimeError("transient GPU OOM")
        return {"ok": True, "attempt": call_counts["n"]}

    registry = default_registry()
    registry["ai.voiceover"] = _flaky_voiceover
    runner = JobRunner(db, registry, lease_seconds=60)

    # Enqueue with max_attempts=2 (what the fix must produce from the API layer;
    # here we test the runner behaviour directly so we supply max_attempts=2 explicitly)
    job_id = enqueue(
        db,
        queue="ai",
        kind="ai.voiceover",
        payload={"text": "Hello", "seq_in_frame": 0, "seq_out_frame_exclusive": 30},
        max_attempts=2,
    )

    # First run: handler raises → job must be requeued (not failed permanently)
    assert runner.run_once() is True
    job = repos.get_job(db, job_id)
    assert job is not None
    assert job["status"] == "queued", (
        f"Expected 'queued' after transient failure with max_attempts=2, got {job['status']!r}"
    )
    assert call_counts["n"] == 1

    # Second run: handler succeeds
    assert runner.run_once() is True
    job = repos.get_job(db, job_id)
    assert job is not None
    assert job["status"] == "succeeded", (
        f"Expected 'succeeded' on attempt 2, got {job['status']!r}"
    )
    assert call_counts["n"] == 2


# ---------------------------------------------------------------------------
# B. Voiceover idempotency: same payload → same job id
# ---------------------------------------------------------------------------


def test_voiceover_idempotency_same_payload(tmp_path: Path) -> None:
    """POST /timelines/{id}/voiceover twice with the same payload → one job (dedup)."""
    client, db = _make_client_db(tmp_path)
    _, timeline, segment_id = _seed_voiceover_setup(db, tmp_path)

    body = {
        "segment_id": segment_id,
        "seq_in_frame": 0,
        "seq_out_frame_exclusive": 30,
        "backend": "stub",
        "voice_id": "Microsoft Katja",
        "mix_mode": "mix",
        "ducking_percent": 100,
    }

    r1 = client.post(f"/timelines/{timeline['id']}/voiceover", json=body)
    assert r1.status_code == 202, r1.text
    job_id_1 = r1.json()["job_id"]

    r2 = client.post(f"/timelines/{timeline['id']}/voiceover", json=body)
    assert r2.status_code == 202, r2.text
    job_id_2 = r2.json()["job_id"]

    assert job_id_1 == job_id_2, (
        "Identical voiceover payload must produce the same job id (idempotency dedup)"
    )


# ---------------------------------------------------------------------------
# C. Voiceover idempotency: different payload → different job ids
# ---------------------------------------------------------------------------


def test_voiceover_idempotency_different_payload(tmp_path: Path) -> None:
    """Two voiceover enqueues with different salient fields produce different jobs."""
    client, db = _make_client_db(tmp_path)
    _, timeline, segment_id = _seed_voiceover_setup(db, tmp_path)

    base = {
        "segment_id": segment_id,
        "seq_in_frame": 0,
        "seq_out_frame_exclusive": 30,
        "backend": "stub",
        "voice_id": "Microsoft Katja",
        "mix_mode": "mix",
        "ducking_percent": 100,
    }
    different = dict(base)
    different["seq_out_frame_exclusive"] = 60  # different range

    r1 = client.post(f"/timelines/{timeline['id']}/voiceover", json=base)
    assert r1.status_code == 202, r1.text

    r2 = client.post(f"/timelines/{timeline['id']}/voiceover", json=different)
    assert r2.status_code == 202, r2.text

    assert r1.json()["job_id"] != r2.json()["job_id"], (
        "Different voiceover payloads must produce different job ids"
    )


# ---------------------------------------------------------------------------
# D. Lipsync idempotency: same salient inputs → same job
# ---------------------------------------------------------------------------


def _seed_lipsync(tmp_path: Path) -> tuple[TestClient, Database, str, str, str, str]:
    """Return (client, db, timeline_id, audio_asset_id, consent_id)."""
    settings = Settings(workspace_root=tmp_path, token=None, start_runner=False)
    app = create_app(settings)
    client = TestClient(app)
    db: Database = cast(Any, app).state.db

    pid_r = client.post(
        "/projects",
        json={"name": "lip", "sequence_rate_num": 30, "sequence_rate_den": 1},
    )
    pid = pid_r.json()["id"]
    tl = repos.create_timeline(db, project_id=pid, name="cut", kind="rough_cut")
    audio = repos.create_asset(
        db,
        project_id=pid,
        type="audio",
        display_name="voice.wav",
        source_path=str(tmp_path / "voice.wav"),
    )
    consent = repos.create_consent_record(
        db, project_id=pid, subject_label="Person A", confirmed_by="test"
    )
    return client, db, tl["id"], audio["id"], consent["id"], pid


def test_lipsync_idempotency_dedup(tmp_path: Path) -> None:
    """POST /timelines/{id}/lipsync twice with identical inputs → one job."""
    client, db, tl_id, audio_id, consent_id, _ = _seed_lipsync(tmp_path)

    body = {
        "seq_in_frame": 0,
        "seq_out_frame_exclusive": 30,
        "audio_asset_id": audio_id,
        "consent_id": consent_id,
        "license_accepted": True,
        "backend": "stub",
    }

    r1 = client.post(f"/timelines/{tl_id}/lipsync", json=body)
    assert r1.status_code == 202, r1.text

    r2 = client.post(f"/timelines/{tl_id}/lipsync", json=body)
    assert r2.status_code == 202, r2.text

    assert r1.json()["job_id"] == r2.json()["job_id"], (
        "Identical lipsync payload must produce the same job id"
    )


# ---------------------------------------------------------------------------
# E. Reenact: explicit max_attempts=2 in DB row
# ---------------------------------------------------------------------------


def _seed_reenact(tmp_path: Path) -> tuple[TestClient, Database, str, str, str]:
    """Return (client, db, timeline_id, portrait_asset_id, consent_id)."""
    settings = Settings(workspace_root=tmp_path, token=None, start_runner=False)
    app = create_app(settings)
    client = TestClient(app)
    db: Database = cast(Any, app).state.db

    pid_r = client.post(
        "/projects",
        json={"name": "reenact-test", "sequence_rate_num": 30, "sequence_rate_den": 1},
    )
    pid = pid_r.json()["id"]
    tl = repos.create_timeline(db, project_id=pid, name="cut", kind="rough_cut")
    portrait = repos.create_asset(
        db,
        project_id=pid,
        type="video",
        display_name="portrait.mp4",
        source_path=str(tmp_path / "portrait.mp4"),
    )
    consent = repos.create_consent_record(
        db, project_id=pid, subject_label="Subject B", confirmed_by="test"
    )
    return client, db, tl["id"], portrait["id"], consent["id"]


def test_reenact_max_attempts_2(tmp_path: Path) -> None:
    """POST /timelines/{id}/reenact must store max_attempts=2 in the job row."""
    client, db, tl_id, portrait_id, consent_id = _seed_reenact(tmp_path)

    r = client.post(
        f"/timelines/{tl_id}/reenact",
        json={
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 30,
            "portrait_asset_id": portrait_id,
            "consent_id": consent_id,
        },
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    job = repos.get_job(db, job_id)
    assert job is not None
    assert int(job["max_attempts"]) == 2, (
        f"Expected max_attempts=2, got {job['max_attempts']!r}"
    )


# ---------------------------------------------------------------------------
# F. Reenact idempotency: same inputs → same job
# ---------------------------------------------------------------------------


def test_reenact_idempotency_dedup(tmp_path: Path) -> None:
    """POST /timelines/{id}/reenact twice with same payload → one job."""
    client, db, tl_id, portrait_id, consent_id = _seed_reenact(tmp_path)

    body = {
        "seq_in_frame": 0,
        "seq_out_frame_exclusive": 30,
        "portrait_asset_id": portrait_id,
        "consent_id": consent_id,
    }

    r1 = client.post(f"/timelines/{tl_id}/reenact", json=body)
    assert r1.status_code == 202, r1.text

    r2 = client.post(f"/timelines/{tl_id}/reenact", json=body)
    assert r2.status_code == 202, r2.text

    assert r1.json()["job_id"] == r2.json()["job_id"], (
        "Identical reenact payload must produce the same job id"
    )


# ---------------------------------------------------------------------------
# G. Lipsync and voiceover max_attempts = 2 stored in DB
# ---------------------------------------------------------------------------


def test_lipsync_max_attempts_2(tmp_path: Path) -> None:
    """POST /timelines/{id}/lipsync must store max_attempts=2 in the job row."""
    client, db, tl_id, audio_id, consent_id, _ = _seed_lipsync(tmp_path)

    r = client.post(
        f"/timelines/{tl_id}/lipsync",
        json={
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 30,
            "audio_asset_id": audio_id,
            "consent_id": consent_id,
            "license_accepted": True,
            "backend": "stub",
        },
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    job = repos.get_job(db, job_id)
    assert job is not None
    assert int(job["max_attempts"]) == 2, (
        f"Expected max_attempts=2 for lipsync, got {job['max_attempts']!r}"
    )


def test_voiceover_max_attempts_2(tmp_path: Path) -> None:
    """POST /timelines/{id}/voiceover must store max_attempts=2 in the job row."""
    client, db = _make_client_db(tmp_path)
    _, timeline, segment_id = _seed_voiceover_setup(db, tmp_path)

    r = client.post(
        f"/timelines/{timeline['id']}/voiceover",
        json={
            "segment_id": segment_id,
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 30,
            "backend": "stub",
        },
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    job = repos.get_job(db, job_id)
    assert job is not None
    assert int(job["max_attempts"]) == 2, (
        f"Expected max_attempts=2 for voiceover, got {job['max_attempts']!r}"
    )
