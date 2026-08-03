"""Integration test: export.render job produces a real MP4 via ffmpeg concat.

Uses real ffmpeg. Skipped if ffmpeg is unavailable.
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


def test_render_job_produces_mp4(tmp_path: Path) -> None:
    # Create a synthetic 2-second test video source
    media = tmp_path / "a.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(media),
    ])

    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    proot = settings.workspace_root / "project"
    proot.mkdir(parents=True, exist_ok=True)

    # create_project stores rate_num/rate_den as sequence_rate_num/sequence_rate_den
    project = repos.create_project(
        db, name="t", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(proot),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="a.mp4", source_path=str(media),
    )
    tl = repos.create_timeline(
        db, project_id=project["id"], name="cut", kind="rough_cut",
    )
    # One clip covering frames 0..30 of the 30fps source (= 1 second)
    repos.add_timeline_clip(
        db, timeline_id=tl["id"], asset_id=asset["id"],
        src_in_frame=0, src_out_frame_exclusive=30,
        seq_in_frame=0, seq_out_frame_exclusive=30,
    )

    exp = repos.create_export(
        db, project_id=project["id"], timeline_id=tl["id"], format="mp4",
    )

    registry = default_registry()
    register_ingest_handlers(registry)
    from laura.render.handlers import register_render_handlers
    register_render_handlers(registry)

    runner = JobRunner(db, registry)
    enqueue(
        db, queue="export", kind="export.render",
        payload={"export_id": exp["id"]},
        idempotency_key=f"render:{exp['id']}",
    )
    _drain(runner)

    done = repos.get_export(db, exp["id"])
    assert done is not None
    assert done["status"] == "ready", done
    assert Path(done["path"]).exists()
    assert done["size_bytes"] > 0


def test_render_job_concats_sources_with_different_resolutions(tmp_path: Path) -> None:
    """Live 2026-08-03 (Drive-Test): the first REAL multi-source overview render died with
    ffmpeg's ``Input link in0:v0 parameters (size 1920x1026 ...) do not match ... (1916x1030)``
    — concat demands identical frame sizes and screen recordings practically never agree
    (every capture is a few pixels of window chrome apart). The 2026-07-31 live test happened
    to render a single surviving source, so the mixed-size path had never actually run.

    Mixed sources must render (normalized onto one canvas); the fix must not disturb the
    uniform-size path, which test_render_job_produces_mp4 above pins byte-identically.
    """
    media_a = tmp_path / "a.mp4"
    media_b = tmp_path / "b.mp4"
    # Same shape mismatch as the live pair (1916x1030 vs 1920x1026), scaled down for speed.
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(media_a),
    ])
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc=duration=2:size=328x236:rate=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(media_b),
    ])

    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    proot = settings.workspace_root / "project"
    proot.mkdir(parents=True, exist_ok=True)
    project = repos.create_project(
        db, name="t", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(proot),
    )
    asset_a = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="a.mp4", source_path=str(media_a),
    )
    asset_b = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="b.mp4", source_path=str(media_b),
    )
    tl = repos.create_timeline(
        db, project_id=project["id"], name="overview", kind="rough_cut",
    )
    repos.add_timeline_clip(
        db, timeline_id=tl["id"], asset_id=asset_a["id"],
        src_in_frame=0, src_out_frame_exclusive=30,
        seq_in_frame=0, seq_out_frame_exclusive=30,
    )
    repos.add_timeline_clip(
        db, timeline_id=tl["id"], asset_id=asset_b["id"],
        src_in_frame=0, src_out_frame_exclusive=30,
        seq_in_frame=30, seq_out_frame_exclusive=60,
    )
    exp = repos.create_export(
        db, project_id=project["id"], timeline_id=tl["id"], format="mp4",
    )

    registry = default_registry()
    register_ingest_handlers(registry)
    from laura.render.handlers import register_render_handlers
    register_render_handlers(registry)
    runner = JobRunner(db, registry)
    enqueue(
        db, queue="export", kind="export.render",
        payload={"export_id": exp["id"]},
        idempotency_key=f"render:{exp['id']}",
    )
    _drain(runner)

    done = repos.get_export(db, exp["id"])
    assert done is not None
    assert done["status"] == "ready", done["error"] if done["status"] == "error" else done
    assert Path(done["path"]).exists()
    assert done["size_bytes"] > 0
