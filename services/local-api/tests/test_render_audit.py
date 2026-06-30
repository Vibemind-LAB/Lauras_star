"""Compliance test: audit_event written on export render success (D2).

Drives handle_render end-to-end against a real ffmpeg-generated clip and
asserts that a 'export.render' audit row lands in the DB.

Skipped automatically when ffmpeg is not on PATH.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.ingest.ffmpeg import run_ffmpeg
from laura.jobs.runner import JobContext
from laura.render import handlers

_FFMPEG_BIN = os.environ.get("LAURA_FFMPEG", "ffmpeg")
_ffmpeg_available = shutil.which(_FFMPEG_BIN) is not None


def _seed_render_scene(tmp_path: Path) -> tuple[Database, str]:
    """Create a project + timeline + real video clip ready for export."""
    media = tmp_path / "clip.mp4"
    run_ffmpeg([
        "-f", "lavfi",
        "-i", "color=c=blue:s=320x240:r=30:d=1",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        str(media),
    ])

    workspace = tmp_path / "ws" / "project"
    workspace.mkdir(parents=True, exist_ok=True)

    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    project = repos.create_project(
        db,
        name="render-audit",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(workspace),
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="cut", kind="rough_cut")
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="clip.mp4",
        source_path=str(media),
    )
    repos.add_timeline_clip(
        db,
        timeline_id=tl["id"],
        asset_id=asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=30,
        seq_in_frame=0,
        seq_out_frame_exclusive=30,
        lane=0,
        role="base",
    )
    export = repos.create_export(
        db,
        project_id=project["id"],
        timeline_id=tl["id"],
        format="mp4",
        options={},
    )
    return db, str(export["id"])


def _ctx(db: Database, kind: str, payload: dict[str, Any]) -> JobContext:
    return JobContext(job_id="j1", kind=kind, queue="render", payload=payload, db=db)


@pytest.mark.skipif(not _ffmpeg_available, reason="ffmpeg not available on PATH")
def test_render_success_writes_audit_event(tmp_path: Path) -> None:
    db, export_id = _seed_render_scene(tmp_path)
    handlers.handle_render(
        _ctx(db, "export.render", {"export_id": export_id})
    )
    events = repos.list_audit_events(db, limit=50)
    assert any(
        e["action"] == "export.render" and e["entity_type"] == "export"
        for e in events
    ), f"no export.render audit event found; got {[e['action'] for e in events]}"
