"""End-to-end: reel options flow through the ``export.render`` job to a real
vertical 1080x1920 MP4 via ffmpeg.

This is the authoritative whole-stack proof for the reel feature: it exercises
``create_export(options=...)`` -> queue -> ``handle_render`` -> ``render_clips_mp4``
(with the reel crop/scale/drawtext filtergraph) -> real ffmpeg output. Skipped if
ffmpeg/ffprobe are unavailable. Mirrors ``test_render_job.py``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.ingest.ffmpeg import run_ffmpeg
from laura.ingest.handlers import register_ingest_handlers
from laura.jobs import JobRunner, default_registry, enqueue

pytestmark = pytest.mark.skipif(
    shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None
    or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available on PATH",
)


def _drain(runner: JobRunner, limit: int = 60) -> int:
    ran = 0
    while runner.run_once():
        ran += 1
        if ran >= limit:
            break
    return ran


def _video_dims(path: str) -> tuple[int, int]:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0", path,
        ],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    width, height = (int(x) for x in out.split(",")[:2])
    return width, height


def test_reel_render_job_produces_vertical_mp4(tmp_path: Path) -> None:
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
    repos.add_timeline_clip(
        db, timeline_id=tl["id"], asset_id=asset["id"],
        src_in_frame=0, src_out_frame_exclusive=30,
        seq_in_frame=0, seq_out_frame_exclusive=30,
    )

    # Apostrophe + comma + colon + % in the hook: a real-world caption that the
    # old inline drawtext escaping could not render. Locks the textfile= fix E2E.
    hook = "Geht's los, jetzt: 100%!"
    exp = repos.create_export(
        db, project_id=project["id"], timeline_id=tl["id"], format="mp4",
        options={"vertical": True, "hook_text": hook, "disclosure_text": "KI"},
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
    # The whole point: the job produced a vertical 9:16 reel.
    assert _video_dims(done["path"]) == (1080, 1920)
    # Reel options survived the create_export -> get_export round-trip.
    assert done["options"] == {
        "vertical": True, "hook_text": hook, "disclosure_text": "KI",
    }
