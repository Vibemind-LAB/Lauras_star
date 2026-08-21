"""Plan A / Task 4 — build VideoTransitions for rough_cut/scene from clip-level fields."""

from __future__ import annotations

from pathlib import Path

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.render.handlers import _clip_video_transitions


def _db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    return db


def _seed_rough_cut_3(db: SqliteDatabase) -> tuple[str, list[str]]:
    project = repos.create_project(
        db, name="P", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/ws"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="c.mov", source_path="/c.mov"
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    ids: list[str] = []
    for si, so in [(0, 100), (100, 160), (160, 240)]:
        clip = repos.add_timeline_clip(
            db,
            timeline_id=tl["id"],
            asset_id=asset["id"],
            src_in_frame=si,
            src_out_frame_exclusive=so,
            seq_in_frame=si,
            seq_out_frame_exclusive=so,
        )
        ids.append(clip["id"])
    return tl["id"], ids


def test_clip_video_transitions_for_rough_cut(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tl_id, clip_ids = _seed_rough_cut_3(db)
    repos.set_clip_transition(db, clip_id=clip_ids[0], kind="crossfade", frames=12)
    tr = _clip_video_transitions(db, tl_id)
    assert len(tr) == 1
    assert tr[0].kind == "crossfade"
    assert tr[0].boundary_frame == 100  # seq_out of clip 0
    assert tr[0].duration_frames == 12


def test_clip_video_transitions_hard_clips_yield_nothing(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tl_id, _ids = _seed_rough_cut_3(db)
    assert _clip_video_transitions(db, tl_id) == []


def test_clip_video_transitions_ignores_transition_on_last_clip(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tl_id, clip_ids = _seed_rough_cut_3(db)
    # a trailing CROSSFADE has no following clip to fold into -> still not emitted
    repos.set_clip_transition(db, clip_id=clip_ids[2], kind="crossfade", frames=12)
    assert _clip_video_transitions(db, tl_id) == []


def test_clip_video_transitions_emits_trailing_fade_on_last_clip(tmp_path: Path) -> None:
    """I-1: a trailing "fade" (final fade-out) on the LAST clip has no following clip to
    fold into, but unlike crossfade it is still meaningful (a fade-out at stream end) and
    must be emitted, boundary = the last clip's own seq_out (== total video length)."""
    db = _db(tmp_path)
    tl_id, clip_ids = _seed_rough_cut_3(db)
    repos.set_clip_transition(db, clip_id=clip_ids[2], kind="fade", frames=12)
    tr = _clip_video_transitions(db, tl_id)
    assert len(tr) == 1
    assert tr[0].kind == "fade"
    assert tr[0].boundary_frame == 240  # seq_out of the last (3rd) clip
    assert tr[0].duration_frames == 12


def test_clip_video_transitions_mixes_boundary_crossfade_and_trailing_fade(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    tl_id, clip_ids = _seed_rough_cut_3(db)
    repos.set_clip_transition(db, clip_id=clip_ids[0], kind="crossfade", frames=8)
    repos.set_clip_transition(db, clip_id=clip_ids[2], kind="fade", frames=12)
    tr = _clip_video_transitions(db, tl_id)
    assert len(tr) == 2
    by_boundary = {t.boundary_frame: t for t in tr}
    assert by_boundary[100].kind == "crossfade"
    assert by_boundary[100].duration_frames == 8
    assert by_boundary[240].kind == "fade"
    assert by_boundary[240].duration_frames == 12
