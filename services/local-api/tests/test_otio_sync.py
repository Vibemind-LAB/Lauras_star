from __future__ import annotations

from pathlib import Path

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.editing.otio_sync import rebuild_otio


def test_rebuild_otio_writes_nonempty_otio(tmp_path: Path) -> None:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a",
        source_path="/tmp/a.mp4",
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="t", kind="scene")
    repos.replace_timeline_clips(
        db,
        tl["id"],
        [
            {
                "asset_id": asset["id"],
                "src_in_frame": 0,
                "src_out_frame_exclusive": 30,
                "seq_in_frame": 0,
                "seq_out_frame_exclusive": 30,
                "lane": 0,
                "speed_num": 1,
                "speed_den": 1,
            }
        ],
    )
    rebuild_otio(db, tl["id"])
    fresh = repos.get_timeline(db, tl["id"])
    assert fresh is not None
    assert fresh["otio_json"] and fresh["otio_json"] != "{}"
