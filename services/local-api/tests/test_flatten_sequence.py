# services/local-api/tests/test_flatten_sequence.py
from __future__ import annotations

from pathlib import Path

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.sequences.flatten import flatten_sequence


def _scene_timeline(db, project_id, asset_id, src_in, src_out):
    """A materialized scene timeline with one clip, rebased to seq 0."""
    tl = repos.create_timeline(db, project_id=project_id, name="s", kind="scene")
    repos.replace_timeline_clips(
        db,
        tl["id"],
        [
            {
                "asset_id": asset_id,
                "src_in_frame": src_in,
                "src_out_frame_exclusive": src_out,
                "seq_in_frame": 0,
                "seq_out_frame_exclusive": src_out - src_in,
                "lane": 0,
                "speed_num": 1,
                "speed_den": 1,
            }
        ],
    )
    return tl


def test_flatten_concatenates_scenes_with_offsets(tmp_path: Path) -> None:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    a1 = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a1",
        source_path="/tmp/a1.mp4",
    )
    a2 = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a2",
        source_path="/tmp/a2.mp4",
    )
    rc = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_scenes(db, project["id"], rc["id"], [(0, 30), (30, 70)])
    s1, s2 = repos.list_scenes(db, rc["id"])
    # materialize each scene to its own timeline (40f and 30f from two assets)
    t1 = _scene_timeline(db, project["id"], a1["id"], 100, 130)  # 30 frames
    t2 = _scene_timeline(db, project["id"], a2["id"], 200, 240)  # 40 frames
    repos.set_scene_timeline(db, s1["id"], t1["id"])
    repos.set_scene_timeline(db, s2["id"], t2["id"])
    seq = repos.create_timeline(db, project_id=project["id"], name="seq", kind="sequence")
    repos.replace_sequence_items(db, seq["id"], [s2["id"], s1["id"]])  # s2 first
    rows = flatten_sequence(db, seq["id"])
    assert [(r["asset_id"], r["seq_in_frame"], r["seq_out_frame_exclusive"]) for r in rows] == [
        (a2["id"], 0, 40),     # s2: 40 frames at offset 0
        (a1["id"], 40, 70),    # s1: 30 frames at offset 40
    ]
    assert rows[0]["src_in_frame"] == 200 and rows[1]["src_in_frame"] == 100
