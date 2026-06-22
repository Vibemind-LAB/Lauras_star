"""Integration test: a kind="sequence" timeline (two scene references) renders to a real MP4.

Uses real ffmpeg. Skipped if ffmpeg is unavailable.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.ingest.ffmpeg import run_ffmpeg
from laura.jobs import JobRunner, default_registry, enqueue

pytestmark = pytest.mark.skipif(
    shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None, reason="ffmpeg"
)


def _src(tmp_path: Path, name: str, secs: int) -> Path:
    p = tmp_path / name
    run_ffmpeg([
        "-f", "lavfi", "-i", f"testsrc=duration={secs}:size=320x240:rate=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p),
    ])
    return p


def _scene_tl(db: SqliteDatabase, pid: str, aid: str) -> dict[str, Any]:
    tl = repos.create_timeline(db, project_id=pid, name="s", kind="scene")
    repos.replace_timeline_clips(db, tl["id"], [{
        "asset_id": aid, "src_in_frame": 0, "src_out_frame_exclusive": 30,
        "seq_in_frame": 0, "seq_out_frame_exclusive": 30, "lane": 0,
        "speed_num": 1, "speed_den": 1,
    }])
    return tl


def test_sequence_renders_to_mp4(tmp_path: Path) -> None:
    m1, m2 = _src(tmp_path, "a1.mp4", 1), _src(tmp_path, "a2.mp4", 1)
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    proot = settings.workspace_root / "project"
    proot.mkdir(parents=True, exist_ok=True)
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(proot),
    )
    a1 = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a1",
        source_path=str(m1),
    )
    a2 = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a2",
        source_path=str(m2),
    )
    rc = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_scenes(db, project["id"], rc["id"], [(0, 30), (30, 60)])
    s1, s2 = repos.list_scenes(db, rc["id"])
    repos.set_scene_timeline(db, s1["id"], _scene_tl(db, project["id"], a1["id"])["id"])
    repos.set_scene_timeline(db, s2["id"], _scene_tl(db, project["id"], a2["id"])["id"])
    seq = repos.get_or_create_project_sequence(db, project["id"])
    repos.replace_sequence_items(db, seq["id"], [s1["id"], s2["id"]])
    exp = repos.create_export(
        db, project_id=project["id"], timeline_id=seq["id"], format="mp4",
    )
    registry = default_registry()
    from laura.render.handlers import register_render_handlers
    register_render_handlers(registry)
    runner = JobRunner(db, registry)
    enqueue(
        db, queue="export", kind="export.render",
        payload={"export_id": exp["id"]},
        idempotency_key=f"r:{exp['id']}",
    )
    while runner.run_once():
        pass
    done = repos.get_export(db, exp["id"])
    assert done is not None
    assert done["status"] == "ready", done
    assert Path(done["path"]).exists() and done["size_bytes"] > 0
