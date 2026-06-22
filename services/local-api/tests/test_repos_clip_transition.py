"""Plan A / Task 2 — set_clip_transition repo + fields exposed via list_timeline_clips."""

from __future__ import annotations

from pathlib import Path

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase


def _db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    return db


def _seed_clip(db: SqliteDatabase) -> tuple[str, str]:
    project = repos.create_project(
        db, name="P", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/ws"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="c.mov", source_path="/c.mov"
    )
    timeline = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    clip = repos.add_timeline_clip(
        db,
        timeline_id=timeline["id"],
        asset_id=asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=30,
        seq_in_frame=0,
        seq_out_frame_exclusive=30,
    )
    return timeline["id"], clip["id"]


def test_clip_transition_defaults_to_hard(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tl_id, clip_id = _seed_clip(db)
    clip = next(c for c in repos.list_timeline_clips(db, tl_id) if c["id"] == clip_id)
    assert clip["transition_after_kind"] == "hard"
    assert clip["transition_after_frames"] == 0


def test_set_clip_transition_roundtrips(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tl_id, clip_id = _seed_clip(db)
    ok = repos.set_clip_transition(db, clip_id=clip_id, kind="crossfade", frames=12)
    assert ok is True
    clip = next(c for c in repos.list_timeline_clips(db, tl_id) if c["id"] == clip_id)
    assert clip["transition_after_kind"] == "crossfade"
    assert clip["transition_after_frames"] == 12


def test_set_clip_transition_unknown_clip_returns_false(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed_clip(db)
    assert repos.set_clip_transition(db, clip_id="nope", kind="fade", frames=5) is False
