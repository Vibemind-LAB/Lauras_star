"""tool_build_roughcut (Axis 1) — agent-operable rough-cut build from analysis shots.

Completes the MCP toolset: ``tool_next_action`` suggests ``roughcut_from_shots`` but no tool
executed it. This wraps ``scenes.build.autobuild_asset_edit_ready`` so an agent can build the
rough cut + scenes on demand (idempotent), mirroring ``tool_start_analysis``'s ok/error contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.mcp import tool_build_roughcut


def _mkdb(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    return db


def _seed_analyzed_asset(db: SqliteDatabase) -> dict[str, Any]:
    """Project + asset + succeeded analysis run + one kept shot. Returns the asset row."""
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mov", source_path="/a.mov"
    )
    run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="1", config={})
    repos.start_analysis_run(db, run["id"])
    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})
    repos.insert_shots(
        db,
        asset_id=asset["id"],
        run_id=run["id"],
        shots=[{"src_in_frame": 0, "src_out_frame_exclusive": 100, "method": "test"}],
    )
    return asset


def test_build_roughcut_creates_clips_and_scenes(tmp_path: Path) -> None:
    db = _mkdb(tmp_path)
    asset = _seed_analyzed_asset(db)

    result = tool_build_roughcut(db, asset["id"])

    assert result["ok"] is True
    assert result["asset_id"] == asset["id"]
    tl_id = result["timeline_id"]
    assert tl_id
    assert len(repos.list_timeline_clips(db, tl_id)) >= 1
    assert result["scene_count"] == len(repos.list_scenes(db, tl_id))
    assert result["scene_count"] >= 1


def test_build_roughcut_is_idempotent(tmp_path: Path) -> None:
    db = _mkdb(tmp_path)
    asset = _seed_analyzed_asset(db)
    first = tool_build_roughcut(db, asset["id"])
    second = tool_build_roughcut(db, asset["id"])
    assert second["ok"] is True
    assert second["timeline_id"] == first["timeline_id"]  # same rough cut, not a duplicate
    assert second["scene_count"] == first["scene_count"]


def test_build_roughcut_unknown_asset(tmp_path: Path) -> None:
    db = _mkdb(tmp_path)
    result = tool_build_roughcut(db, "does-not-exist")
    assert result["ok"] is False
    assert result["error"] == "asset not found"


def test_build_roughcut_requires_succeeded_analysis(tmp_path: Path) -> None:
    db = _mkdb(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a", source_path="/a.mov"
    )
    run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="1", config={})
    repos.start_analysis_run(db, run["id"])  # running, not succeeded
    result = tool_build_roughcut(db, asset["id"])
    assert result["ok"] is False
    assert result["error"] == "no succeeded analysis run"
