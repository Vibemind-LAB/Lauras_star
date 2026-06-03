"""End-to-end: ingest.fetch over a flaky server -> resume -> verify -> probe.

Uses REAL ffmpeg/ffprobe. Skipped if ffmpeg is unavailable.
"""

from __future__ import annotations

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

from ._flaky_http import serve

pytestmark = pytest.mark.skipif(
    shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None,
    reason="ffmpeg not available on PATH",
)


def _drain(runner: JobRunner, limit: int = 60) -> int:
    ran = 0
    while runner.run_once():
        ran += 1
        if ran >= limit:
            break
    return ran


def test_fetch_resumes_then_probes(tmp_path: Path) -> None:
    media = tmp_path / "sample.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(media),
    ])
    content = media.read_bytes()

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
        display_name="sample.mp4", source_path="url:pending", online=False,
    )

    registry = default_registry()
    register_ingest_handlers(registry)
    runner = JobRunner(db, registry)

    with serve(content, cut_after=len(content) // 2) as url:
        enqueue(
            db, queue="ingest.io", kind="ingest.fetch",
            payload={"asset_id": asset["id"], "source_url": url},
            idempotency_key=f"fetch:{asset['id']}", max_attempts=5,
        )
        _drain(runner)

    a = repos.get_asset(db, asset["id"])
    assert a is not None
    assert a["online"] == 1                      # fetch promoted it
    assert Path(a["source_path"]).exists()       # local file now
    assert (a["width"], a["height"]) == (320, 240)  # probe ran afterwards
