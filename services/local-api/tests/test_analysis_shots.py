"""Real shot-detection integration (PySceneDetect + ffmpeg). Skipped if either absent."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from laura.analysis.handlers import register_analysis_handlers
from laura.analysis.shots import detect_shots, scenedetect_available
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.ingest.ffmpeg import run_ffmpeg
from laura.ingest.handlers import register_ingest_handlers
from laura.jobs import JobRunner, default_registry, enqueue

pytestmark = pytest.mark.skipif(
    not scenedetect_available()
    or shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None,
    reason="needs the [scene] extra and ffmpeg",
)


@pytest.fixture
def cut_clip(tmp_path: Path) -> Path:
    """A 2s clip with a hard cut at frame 30 (1s testsrc -> 1s solid blue)."""
    out = tmp_path / "cut.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=30",
        "-f", "lavfi", "-i", "color=c=blue:duration=1:size=320x240:rate=30",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
    ])
    return out


def test_detect_shots_finds_the_cut(cut_clip: Path) -> None:
    shots = detect_shots(cut_clip)
    assert len(shots) >= 2
    boundaries: set[int] = set()
    for s in shots:
        boundaries.add(s.src_in_frame)
        boundaries.add(s.src_out_frame_exclusive)
    assert any(abs(b - 30) <= 3 for b in boundaries), boundaries


def test_orchestrator_inserts_shots(tmp_path: Path, cut_clip: Path) -> None:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    project_root = settings.workspace_root / "p"
    project_root.mkdir(parents=True, exist_ok=True)
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root=str(project_root)
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="cut.mp4",
        source_path=str(cut_clip),
    )

    reg = default_registry()
    register_ingest_handlers(reg)
    register_analysis_handlers(reg)
    runner = JobRunner(db, reg)

    enqueue(db, queue="ingest.io", kind="ingest.probe",
            payload={"asset_id": asset["id"]}, idempotency_key=f"probe:{asset['id']}")
    while runner.run_once():
        pass

    run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="1",
        config={"stages": {"scene": True, "asr": False, "diarize": False}},
    )
    enqueue(db, queue="analysis.scene", kind="analysis.run",
            payload={"asset_id": asset["id"], "analysis_run_id": run["id"],
                     "config": {"stages": {"scene": True, "asr": False}}},
            idempotency_key=f"analysis:{run['id']}")
    while runner.run_once():
        pass

    finished = repos.get_analysis_run(db, run["id"])
    assert finished is not None and finished["status"] == "succeeded"
    shots = repos.list_shots(db, asset["id"], run["id"])
    assert len(shots) >= 2
    # every shot got a real thumbnail on disk
    assert all(s["thumbnail_path"] for s in shots)
    assert all(os.path.exists(s["thumbnail_path"]) for s in shots)
