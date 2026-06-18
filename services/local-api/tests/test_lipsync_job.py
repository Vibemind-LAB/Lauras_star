"""Tests for the consent/license-gated ai.lipsync job."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from laura.ai import handlers as ai_handlers
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.ingest.ffmpeg import run_ffmpeg
from laura.jobs import JobRunner, default_registry, enqueue

_FFMPEG_BIN = os.environ.get("LAURA_FFMPEG", "ffmpeg")

pytestmark = pytest.mark.skipif(
    shutil.which(_FFMPEG_BIN) is None,
    reason="ffmpeg not available on PATH",
)


def _drain(runner: JobRunner, limit: int = 60) -> int:
    ran = 0
    while runner.run_once():
        ran += 1
        if ran >= limit:
            break
    return ran


def _registry() -> dict[str, Any]:
    from laura.ai.handlers import register_ai_handlers
    from laura.render.handlers import register_render_handlers

    registry = default_registry()
    register_render_handlers(registry)
    register_ai_handlers(registry)
    return registry


def _setup_scene(
    tmp_path: Path,
) -> tuple[SqliteDatabase, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    workspace = tmp_path / "ws" / "project"
    workspace.mkdir(parents=True, exist_ok=True)
    video = tmp_path / "base.mp4"
    audio = tmp_path / "voice.wav"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=green:s=320x240:r=30:d=1",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:d=1",
        "-shortest",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(video),
    ])
    run_ffmpeg([
        "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:d=1",
        "-c:a", "pcm_s16le",
        str(audio),
    ])

    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    project = repos.create_project(
        db,
        name="lipsync",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(workspace),
    )
    video_asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="base.mp4",
        source_path=str(video),
    )
    audio_asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="audio",
        display_name="voice.wav",
        source_path=str(audio),
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="cut", kind="rough_cut")
    repos.add_timeline_clip(
        db,
        timeline_id=tl["id"],
        asset_id=video_asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=30,
        seq_in_frame=0,
        seq_out_frame_exclusive=30,
    )
    return db, project, video_asset, audio_asset, tl


def _enqueue_lipsync(
    db: SqliteDatabase,
    *,
    timeline_id: str,
    audio_asset_id: str,
    consent_id: str | None,
    license_accepted: bool | None,
    quality_threshold: float = 0.6,
) -> str:
    payload: dict[str, Any] = {
        "timeline_id": timeline_id,
        "seq_in_frame": 0,
        "seq_out_frame_exclusive": 20,
        "audio_asset_id": audio_asset_id,
        "backend": "stub",
        "quality_threshold": quality_threshold,
    }
    if consent_id is not None:
        payload["consent_id"] = consent_id
    if license_accepted is not None:
        payload["license_accepted"] = license_accepted
    return enqueue(db, queue="analysis.gpu", kind="ai.lipsync", payload=payload, max_attempts=1)


def _synthetic_lipsync_assets(db: SqliteDatabase, project_id: str) -> list[dict[str, Any]]:
    return [
        asset
        for asset in repos.list_assets(db, project_id)
        if asset.get("synthetic") and asset.get("ai_effect") == "lipsync"
    ]


class _Probe:
    def __init__(
        self,
        *,
        face_detected: bool,
        mouth_visible: bool,
        audio_present: bool,
        reason: str | None = None,
    ) -> None:
        self.face_detected = face_detected
        self.mouth_visible = mouth_visible
        self.audio_present = audio_present
        self.reason = reason


class _Quality:
    def __init__(
        self,
        *,
        sync_score: float,
        mouth_score: float,
        temporal_score: float,
        passed: bool,
    ) -> None:
        self.sync_score = sync_score
        self.mouth_score = mouth_score
        self.temporal_score = temporal_score
        self.passed = passed


def test_lipsync_refuses_missing_license_before_creating_assets(tmp_path: Path) -> None:
    db, project, _, audio_asset, tl = _setup_scene(tmp_path)
    consent = repos.create_consent_record(
        db, project_id=project["id"], subject_label="Person A", confirmed_by="test",
    )
    runner = JobRunner(db, _registry())
    before = repos.list_assets(db, project["id"])

    job_id = _enqueue_lipsync(
        db,
        timeline_id=tl["id"],
        audio_asset_id=audio_asset["id"],
        consent_id=consent["id"],
        license_accepted=None,
    )
    _drain(runner)

    job = repos.get_job(db, job_id)
    assert job is not None
    assert job["status"] == "failed"
    assert "license_accepted" in job["error_json"]
    assert len(repos.list_assets(db, project["id"])) == len(before)
    assert _synthetic_lipsync_assets(db, project["id"]) == []
    assert [c for c in repos.list_timeline_clips(db, tl["id"]) if c.get("role") == "replace"] == []


def test_lipsync_refuses_revoked_consent_before_sidecar(tmp_path: Path) -> None:
    db, project, _, audio_asset, tl = _setup_scene(tmp_path)
    consent = repos.create_consent_record(
        db, project_id=project["id"], subject_label="Person A", confirmed_by="test",
    )
    assert repos.revoke_consent_record(db, consent["id"]) is True
    runner = JobRunner(db, _registry())

    job_id = _enqueue_lipsync(
        db,
        timeline_id=tl["id"],
        audio_asset_id=audio_asset["id"],
        consent_id=consent["id"],
        license_accepted=True,
    )
    _drain(runner)

    job = repos.get_job(db, job_id)
    assert job is not None
    assert job["status"] == "failed"
    assert "revoked" in job["error_json"]
    assert _synthetic_lipsync_assets(db, project["id"]) == []


def test_lipsync_stub_e2e_creates_synthetic_replace_asset(tmp_path: Path) -> None:
    db, project, _, audio_asset, tl = _setup_scene(tmp_path)
    consent = repos.create_consent_record(
        db, project_id=project["id"], subject_label="Person A", confirmed_by="test",
    )
    runner = JobRunner(db, _registry())

    job_id = _enqueue_lipsync(
        db,
        timeline_id=tl["id"],
        audio_asset_id=audio_asset["id"],
        consent_id=consent["id"],
        license_accepted=True,
    )
    _drain(runner)

    job = repos.get_job(db, job_id)
    assert job is not None
    assert job["status"] == "succeeded", job["error_json"]
    result = json.loads(job["result_json"])
    assert result["consent_id"] == consent["id"]
    assert result["seq_in_frame"] == 0
    assert result["seq_out_frame_exclusive"] == 20
    assert result["probe"] == {
        "face_detected": True,
        "mouth_visible": True,
        "audio_present": True,
        "reason": None,
    }
    assert result["quality"]["passed"] is True
    assert result["quality"]["sync_score"] >= 0.6

    synthetic = _synthetic_lipsync_assets(db, project["id"])
    assert len(synthetic) == 1
    asset = synthetic[0]
    assert asset["type"] == "video"
    assert Path(asset["source_path"]).exists()

    replace = [c for c in repos.list_timeline_clips(db, tl["id"]) if c.get("role") == "replace"]
    assert len(replace) == 1
    assert replace[0]["asset_id"] == asset["id"]
    assert replace[0]["seq_in_frame"] == 0
    assert replace[0]["seq_out_frame_exclusive"] == 20
    assert replace[0]["lane"] >= 1


def test_lipsync_probe_failure_creates_no_synthetic_asset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class BadProbeBackend:
        name = "stub"

        def available(self) -> bool:
            return True

        def probe(self, *, video_path: Path, audio_path: Path) -> _Probe:
            return _Probe(
                face_detected=False,
                mouth_visible=False,
                audio_present=True,
                reason="no face in selected range",
            )

        def lipsync(
            self,
            *,
            video_path: Path,
            audio_path: Path,
            out_path: Path,
            fps_num: int,
            fps_den: int,
        ) -> _Quality:
            raise AssertionError("lipsync must not run after a failed probe")

    monkeypatch.setattr(ai_handlers, "resolve_lipsync_backend", lambda _name: BadProbeBackend())
    db, project, _, audio_asset, tl = _setup_scene(tmp_path)
    consent = repos.create_consent_record(
        db, project_id=project["id"], subject_label="Person A", confirmed_by="test",
    )
    runner = JobRunner(db, _registry())

    job_id = _enqueue_lipsync(
        db,
        timeline_id=tl["id"],
        audio_asset_id=audio_asset["id"],
        consent_id=consent["id"],
        license_accepted=True,
    )
    _drain(runner)

    job = repos.get_job(db, job_id)
    assert job is not None
    assert job["status"] == "failed"
    assert "no face in selected range" in job["error_json"]
    assert _synthetic_lipsync_assets(db, project["id"]) == []


def test_lipsync_quality_gate_failure_creates_no_synthetic_asset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class LowQualityBackend:
        name = "stub"

        def available(self) -> bool:
            return True

        def probe(self, *, video_path: Path, audio_path: Path) -> _Probe:
            return _Probe(face_detected=True, mouth_visible=True, audio_present=True)

        def lipsync(
            self,
            *,
            video_path: Path,
            audio_path: Path,
            out_path: Path,
            fps_num: int,
            fps_den: int,
        ) -> _Quality:
            out_path.write_bytes(video_path.read_bytes())
            return _Quality(
                sync_score=0.2,
                mouth_score=0.3,
                temporal_score=0.4,
                passed=False,
            )

    monkeypatch.setattr(ai_handlers, "resolve_lipsync_backend", lambda _name: LowQualityBackend())
    db, project, _, audio_asset, tl = _setup_scene(tmp_path)
    consent = repos.create_consent_record(
        db, project_id=project["id"], subject_label="Person A", confirmed_by="test",
    )
    runner = JobRunner(db, _registry())

    job_id = _enqueue_lipsync(
        db,
        timeline_id=tl["id"],
        audio_asset_id=audio_asset["id"],
        consent_id=consent["id"],
        license_accepted=True,
        quality_threshold=0.8,
    )
    _drain(runner)

    job = repos.get_job(db, job_id)
    assert job is not None
    assert job["status"] == "failed"
    assert "quality gate failed" in job["error_json"]
    assert _synthetic_lipsync_assets(db, project["id"]) == []
