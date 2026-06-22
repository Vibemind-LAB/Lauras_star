"""RL1 — timeline_clips.role column + repo helpers.

Tests pin:
* migration default: existing clip without explicit role reads back role == 'base';
* add_timeline_clip with lane=1, role='replace' round-trips both fields correctly;
* update_timeline_clip_role flips the role back to 'base';
* delete_timeline_clip removes the row (list returns empty).
"""

from __future__ import annotations

from pathlib import Path

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase


def _db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    return db


def _seed(db: SqliteDatabase) -> tuple[str, str, str]:
    """Return (project_id, asset_id, timeline_id)."""
    project = repos.create_project(
        db,
        name="TestProject",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root="/tmp/ws",
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="clip.mov",
        source_path="/clip.mov",
    )
    timeline = repos.create_timeline(
        db, project_id=project["id"], name="rc", kind="rough_cut"
    )
    return project["id"], asset["id"], timeline["id"]


def test_default_role_is_base(tmp_path: Path) -> None:
    """A clip inserted without an explicit role defaults to 'base'."""
    db = _db(tmp_path)
    _pid, asset_id, tl_id = _seed(db)
    repos.add_timeline_clip(
        db,
        timeline_id=tl_id,
        asset_id=asset_id,
        src_in_frame=0,
        src_out_frame_exclusive=30,
        seq_in_frame=0,
        seq_out_frame_exclusive=30,
    )
    clips = repos.list_timeline_clips(db, tl_id)
    assert len(clips) == 1
    assert clips[0]["role"] == "base"


def test_add_clip_with_lane_and_replace_role(tmp_path: Path) -> None:
    """add_timeline_clip with lane=1, role='replace' round-trips both fields."""
    db = _db(tmp_path)
    _pid, asset_id, tl_id = _seed(db)
    returned = repos.add_timeline_clip(
        db,
        timeline_id=tl_id,
        asset_id=asset_id,
        src_in_frame=0,
        src_out_frame_exclusive=30,
        seq_in_frame=0,
        seq_out_frame_exclusive=30,
        lane=1,
        role="replace",
    )
    assert returned["role"] == "replace"
    assert returned["lane"] == 1

    clips = repos.list_timeline_clips(db, tl_id)
    assert len(clips) == 1
    assert clips[0]["role"] == "replace"
    assert clips[0]["lane"] == 1


def test_update_timeline_clip_role(tmp_path: Path) -> None:
    """update_timeline_clip_role flips the stored role and returns True."""
    db = _db(tmp_path)
    _pid, asset_id, tl_id = _seed(db)
    clip = repos.add_timeline_clip(
        db,
        timeline_id=tl_id,
        asset_id=asset_id,
        src_in_frame=0,
        src_out_frame_exclusive=30,
        seq_in_frame=0,
        seq_out_frame_exclusive=30,
        lane=1,
        role="replace",
    )
    clip_id: str = clip["id"]

    result = repos.update_timeline_clip_role(db, clip_id, "base")
    assert result is True

    clips = repos.list_timeline_clips(db, tl_id)
    assert clips[0]["role"] == "base"


def test_delete_timeline_clip(tmp_path: Path) -> None:
    """delete_timeline_clip removes the row; subsequent list is empty."""
    db = _db(tmp_path)
    _pid, asset_id, tl_id = _seed(db)
    clip = repos.add_timeline_clip(
        db,
        timeline_id=tl_id,
        asset_id=asset_id,
        src_in_frame=0,
        src_out_frame_exclusive=30,
        seq_in_frame=0,
        seq_out_frame_exclusive=30,
    )
    clip_id: str = clip["id"]

    result = repos.delete_timeline_clip(db, clip_id)
    assert result is True

    clips = repos.list_timeline_clips(db, tl_id)
    assert clips == []
