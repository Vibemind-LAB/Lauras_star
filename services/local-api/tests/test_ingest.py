"""Integration test for the ingest pipeline using REAL ffmpeg/ffprobe.

Generates a tiny testsrc+sine clip, runs probe -> proxy -> audio -> waveform via the
job runner, and asserts metadata + artifacts. Skipped if ffmpeg is not on PATH.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.ingest.ffmpeg import run_ffmpeg
from laura.ingest.handlers import register_ingest_handlers
from laura.jobs import JobRunner, default_registry, enqueue
from laura.main import create_app

pytestmark = pytest.mark.skipif(
    shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None,
    reason="ffmpeg not available on PATH",
)


@pytest.fixture
def sample_media(tmp_path: Path) -> Path:
    out = tmp_path / "sample.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2:sample_rate=48000",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out),
    ])
    return out


def _drain(runner: JobRunner, limit: int = 50) -> int:
    ran = 0
    while runner.run_once():
        ran += 1
        if ran >= limit:
            break
    return ran


def test_full_ingest_pipeline(
    tmp_path: Path, sample_media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # This test exercises the ingest chain only (probe→proxy→audio→waveform); auto-analyze is a
    # separate pipeline with its own tests. Disable it so draining the queue doesn't also enqueue
    # an analysis.run that this ingest-only runner has no handler for (it would fail the run).
    monkeypatch.setenv("LAURA_AUTO_ANALYZE", "0")
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    project_root = settings.workspace_root / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    project = repos.create_project(
        db, name="t", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(project_root),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="sample.mp4", source_path=str(sample_media),
    )
    enqueue(db, queue="ingest.io", kind="ingest.probe",
            payload={"asset_id": asset["id"]}, idempotency_key=f"probe:{asset['id']}")

    registry = default_registry()
    register_ingest_handlers(registry)
    runner = JobRunner(db, registry)

    ran = _drain(runner)
    assert ran >= 4, f"expected probe+proxy+audio+waveform, ran {ran}"

    # --- probed metadata ---
    a = repos.get_asset(db, asset["id"])
    assert a is not None
    assert (a["width"], a["height"]) == (320, 240)
    assert (a["rate_num"], a["rate_den"]) == (30, 1)
    assert a["audio_sample_rate"] == 48000
    assert a["is_vfr"] == 0
    assert a["sha256"]
    assert a["duration_frames"] and a["duration_frames"] >= 50

    # --- derived artifacts on disk ---
    files = {f["kind"]: f for f in repos.list_asset_files(db, asset["id"])}
    for kind in ("original", "poster", "proxy", "audio_mono16k", "audio_mix48k", "waveform"):
        assert kind in files, f"missing {kind}"
        assert os.path.exists(files[kind]["path"]), files[kind]["path"]

    # --- waveform sanity ---
    wf = json.loads(Path(files["waveform"]["path"]).read_text(encoding="utf-8"))
    assert wf["length"] > 0
    assert len(wf["peaks"]) == wf["length"]
    assert all(0.0 <= p <= 1.0 for p in wf["peaks"])

    # --- no failed jobs ---
    with db.connection() as conn:
        failed = conn.execute("SELECT COUNT(*) AS c FROM jobs WHERE status='failed'").fetchone()
    assert failed["c"] == 0


def test_import_endpoint_accepts_and_registers(tmp_path: Path, sample_media: Path) -> None:
    from fastapi.testclient import TestClient

    settings = Settings(workspace_root=tmp_path / "ws2", start_runner=False)
    app = create_app(settings)
    with TestClient(app) as client:
        project = client.post(
            "/projects",
            json={"name": "x", "sequence_rate_num": 30, "sequence_rate_den": 1},
        ).json()
        resp = client.post(
            f"/projects/{project['id']}/assets/import",
            json={"source_path": str(sample_media)},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["asset_id"] and body["job_id"]

        asset = client.get(f"/assets/{body['asset_id']}").json()
        assert asset["display_name"] == "sample.mp4"
        assert asset["type"] == "video"

        job = client.get(f"/jobs/{body['job_id']}").json()
        assert job["kind"] == "ingest.probe"
