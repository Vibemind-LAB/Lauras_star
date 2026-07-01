"""tool_render_timeline (Axis 1) — agent-operable timeline render.

Completes the MCP toolset: ``tool_next_action``'s final ``render_reel`` step now has an
executable tool. Mirrors the ``timelines.py`` export.render endpoint — creates an mp4 export and
enqueues an ``export.render`` job. Requires the timeline to have clips. The tool only enqueues;
the actual ffmpeg render runs in the background job (not exercised here).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.mcp import tool_render_timeline


def _mkdb(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    return db


def _project(db: SqliteDatabase) -> dict[str, Any]:
    return repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )


def _seed_timeline_with_clip(db: SqliteDatabase) -> dict[str, Any]:
    project = _project(db)
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a", source_path="/a.mov"
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.add_timeline_clip(
        db, timeline_id=tl["id"], asset_id=asset["id"],
        src_in_frame=0, src_out_frame_exclusive=30,
        seq_in_frame=0, seq_out_frame_exclusive=30,
    )
    return tl


def test_render_timeline_creates_export_and_job(tmp_path: Path) -> None:
    db = _mkdb(tmp_path)
    tl = _seed_timeline_with_clip(db)

    result = tool_render_timeline(db, tl["id"])

    assert result["ok"] is True
    assert result["timeline_id"] == tl["id"]
    assert result["export_id"]
    assert result["job_id"]
    exp = repos.get_export(db, result["export_id"])
    assert exp is not None
    assert exp["timeline_id"] == tl["id"]


def test_render_timeline_unknown_timeline(tmp_path: Path) -> None:
    db = _mkdb(tmp_path)
    result = tool_render_timeline(db, "does-not-exist")
    assert result["ok"] is False
    assert result["error"] == "timeline not found"


def test_render_timeline_requires_clips(tmp_path: Path) -> None:
    db = _mkdb(tmp_path)
    project = _project(db)
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    result = tool_render_timeline(db, tl["id"])
    assert result["ok"] is False
    assert result["error"] == "timeline has no clips"
